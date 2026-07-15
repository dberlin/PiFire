"""Tests for wizard-finish.html's reboot-required modal.

Two layers:
1. A cheap static Jinja render check that the modal markup/JS wiring exists.
2. A Playwright e2e check (skips cleanly if Chromium isn't installed, matching
   tests/web/test_wizard_nested_modal_scroll.py's existing pattern) that drives the real
   page: percent==142 shows the modal instead of auto-redirecting, and each button
   navigates to the right /admin/* URL; percent==101 still auto-redirects with no modal.

Network calls are mocked entirely in-browser via Playwright's page.route() -- nothing
here ever talks to a real Flask server, launches the real `python wizard.py &`
subprocess (which the real POST /wizard/finish route does), or hits the real
/admin/reboot or /admin/restart routes.

The Playwright fixture serves the page from a throwaway, single-purpose Flask app --
deliberately NOT the real app.py singleton. Other Playwright test files in this suite
(e.g. test_wizard_nested_modal_scroll.py) already serve requests against that same
shared module-level Flask object; once any of them has served a request, Flask
forbids registering new routes on it for the rest of the process. Registering a
'/test-only/wizard-finish' route on the real app worked when this file ran alone but
raised `AssertionError: The setup method 'route' can no longer be called` once the
full test suite ran multiple such files. The throwaway app sidesteps this by never
touching app.py's shared object at all: it reuses the same manual Jinja render as the
static tests above (so app.py's settings/datastore machinery isn't needed either),
just with url_for('static', ...) resolved to a real /static/<filename> path so
jQuery/Bootstrap actually load, and every other url_for(...) call (blueprint nav
links this page never uses) stubbed to '#'.
"""

import os

import jinja2
import pytest

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
WIZARD_TEMPLATE_DIR = os.path.join(BASE, 'blueprints', 'wizard', 'templates')
BASE_TEMPLATE_DIR = os.path.join(BASE, 'templates')


class _FakeRequest:
	"""Minimal stand-in for Flask's `request` proxy, which base.html references
	unconditionally (request.MOBILE, request.path) -- a real Flask render adds
	this global automatically, but a bare jinja2.Environment does not."""

	MOBILE = False
	path = '/wizard/finish'


def _stub_url_for(endpoint, **values):
	"""Default url_for stand-in: every endpoint resolves to '#'. Fine for the static
	markup checks below, which never load real assets."""
	return '#'


def _render_wizard_finish(url_for=_stub_url_for):
	env = jinja2.Environment(loader=jinja2.FileSystemLoader([WIZARD_TEMPLATE_DIR, BASE_TEMPLATE_DIR]))
	env.globals['url_for'] = url_for
	env.globals['request'] = _FakeRequest()
	template = env.get_template('wizard/wizard-finish.html')
	return template.render(page_theme='light', grill_name='Test Grill')


def test_modal_markup_present_and_forces_a_choice():
	html = _render_wizard_finish()

	assert 'id="rebootModal"' in html
	assert 'data-backdrop="static"' in html
	assert 'data-keyboard="false"' in html
	assert 'id="rebootNowBtn"' in html
	assert 'id="restartServicesBtn"' in html
	# No dismiss/close (X) button inside the reboot modal specifically -- the user
	# must click one of the two explicit buttons.
	reboot_modal_start = html.index('id="rebootModal"')
	reboot_modal_chunk = html[reboot_modal_start : reboot_modal_start + 800]
	assert 'data-dismiss="modal"' not in reboot_modal_chunk


def test_js_shows_modal_on_142_and_auto_redirects_on_101():
	html = _render_wizard_finish()

	assert 'data.percent == 142' in html
	assert "$('#rebootModal').modal('show')" in html
	assert "location.href = '/admin/restart'" in html


_PLAYWRIGHT_UNAVAILABLE_REASON = None
try:
	from playwright.sync_api import sync_playwright

	with sync_playwright() as _pw:
		if not os.path.exists(_pw.chromium.executable_path):
			_PLAYWRIGHT_UNAVAILABLE_REASON = (
				f'chromium not installed at {_pw.chromium.executable_path!r} -- '
				'run `uv run playwright install chromium`'
			)
except Exception as exc:  # pragma: no cover - only exercised if playwright itself is unusable here
	_PLAYWRIGHT_UNAVAILABLE_REASON = f'playwright unavailable: {exc}'


def _static_asset_url_for(endpoint, **values):
	"""url_for stand-in for the live-server render: 'static' resolves to a real
	/static/<filename> path (so jQuery/Bootstrap actually load in the browser);
	every other endpoint (blueprint nav links this page never uses) stubs to '#'."""
	if endpoint == 'static':
		return f'/static/{values.get("filename", "")}'
	return '#'


@pytest.fixture(scope='class')
def live_server():
	"""Serves the pre-rendered wizard-finish.html on a throwaway, single-purpose
	Flask app -- see the module docstring for why this deliberately does not reuse
	the real app.py singleton."""
	import threading

	from flask import Flask
	from werkzeug.serving import make_server

	html = _render_wizard_finish(url_for=_static_asset_url_for)

	test_app = Flask(__name__, static_folder=os.path.join(BASE, 'static'), static_url_path='/static')

	@test_app.route('/test-only/wizard-finish')
	def _test_only_wizard_finish():
		return html

	srv = make_server('127.0.0.1', 0, test_app)
	port = srv.server_address[1]
	thread = threading.Thread(target=srv.serve_forever, daemon=True)
	thread.start()
	try:
		yield f'http://127.0.0.1:{port}'
	finally:
		srv.shutdown()
		thread.join(timeout=5)


@pytest.mark.skipif(_PLAYWRIGHT_UNAVAILABLE_REASON is not None, reason=_PLAYWRIGHT_UNAVAILABLE_REASON or '')
class TestRebootModalInteraction:
	"""Drives the rendered page in a real browser via the throwaway Flask app served
	by the live_server fixture above."""

	def _goto_with_mocked_status(self, page, base_url, percent):
		def _fulfill_status(route):
			route.fulfill(json={'percent': percent, 'status': 'Finished!', 'output': ' - Finished!'})

		def _fulfill_admin(route):
			route.fulfill(status=200, body='ok')

		page.route('**/wizard/installstatus', _fulfill_status)
		page.route('**/admin/reboot', _fulfill_admin)
		page.route('**/admin/restart', _fulfill_admin)
		page.goto(f'{base_url}/test-only/wizard-finish', wait_until='networkidle')

	def test_percent_142_shows_modal_instead_of_auto_redirecting(self, live_server, page):
		self._goto_with_mocked_status(page, live_server, percent=142)

		page.wait_for_selector('#rebootModal.show', timeout=3000)
		assert page.url.endswith('/test-only/wizard-finish'), 'must not auto-navigate away when a reboot is required'

	def test_percent_142_restart_services_button_navigates_to_admin_restart(self, live_server, page):
		self._goto_with_mocked_status(page, live_server, percent=142)
		page.wait_for_selector('#rebootModal.show', timeout=3000)

		page.click('#restartServicesBtn')
		page.wait_for_url('**/admin/restart', timeout=3000)

	def test_percent_142_reboot_now_button_navigates_to_admin_reboot(self, live_server, page):
		self._goto_with_mocked_status(page, live_server, percent=142)
		page.wait_for_selector('#rebootModal.show', timeout=3000)

		page.click('#rebootNowBtn')
		page.wait_for_url('**/admin/reboot', timeout=3000)

	def test_percent_101_still_auto_redirects_with_no_modal(self, live_server, page):
		self._goto_with_mocked_status(page, live_server, percent=101)

		page.wait_for_url('**/admin/restart', timeout=3000)
		assert not page.evaluate("document.querySelector('#rebootModal')?.classList.contains('show')")
