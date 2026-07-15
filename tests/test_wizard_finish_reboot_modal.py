"""Tests for wizard-finish.html's reboot-required modal.

Two layers:
1. A cheap static Jinja render check that the modal markup/JS wiring exists.
2. A Playwright e2e check (skips cleanly if Chromium isn't installed, matching
   tests/test_wizard_nested_modal_scroll.py's existing pattern) that drives the real
   page: percent==142 shows the modal instead of auto-redirecting, and each button
   navigates to the right /admin/* URL; percent==101 still auto-redirects with no modal.

Network calls are mocked entirely in-browser via Playwright's page.route() -- nothing
here ever talks to a real Flask server, launches the real `python wizard.py &`
subprocess (which the real POST /wizard/finish route does), or hits the real
/admin/reboot or /admin/restart routes.
"""

import os

import jinja2
import pytest

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WIZARD_TEMPLATE_DIR = os.path.join(BASE, 'blueprints', 'wizard', 'templates')
BASE_TEMPLATE_DIR = os.path.join(BASE, 'templates')


class _FakeRequest:
	"""Minimal stand-in for Flask's `request` proxy, which base.html references
	unconditionally (request.MOBILE, request.path) -- a real Flask render adds
	this global automatically, but a bare jinja2.Environment does not."""

	MOBILE = False
	path = '/wizard/finish'


def _render_wizard_finish():
	env = jinja2.Environment(loader=jinja2.FileSystemLoader([WIZARD_TEMPLATE_DIR, BASE_TEMPLATE_DIR]))
	env.globals['url_for'] = lambda *a, **k: '#'
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


@pytest.mark.skipif(_PLAYWRIGHT_UNAVAILABLE_REASON is not None, reason=_PLAYWRIGHT_UNAVAILABLE_REASON or '')
class TestRebootModalInteraction:
	"""Serves the real rendered template over a real (local, static-asset-only) Flask
	dev server so jQuery/Bootstrap load correctly, but via a test-only route added
	directly to the running app instance in this fixture -- never through the real
	POST /wizard/finish route, which kicks off a real `python wizard.py &` process."""

	@pytest.fixture(scope='class')
	def live_server(self, tmp_path_factory):
		"""Seeds an isolated temp SQLite DB + settings BEFORE importing `app` (mirrors
		tests/test_wizard_nested_modal_scroll.py's live_server fixture) -- app.py calls
		datastore.init() and read_settings() at import time, so without this the
		import would touch this machine's real datastore/settings instead of
		test-scoped ones."""
		import os
		import threading

		from werkzeug.serving import make_server

		from common import datastore
		from common.common import default_settings, write_settings_store

		tmp_dir = tmp_path_factory.mktemp('wizard_finish_reboot_modal_e2e')
		db_path = str(tmp_dir / 'test.db')
		os.environ['PIFIRE_DB_PATH'] = db_path
		datastore._reset_for_tests(db_path)
		datastore.init()
		write_settings_store(default_settings())

		from app import app as flask_app
		from flask import render_template

		@flask_app.route('/test-only/wizard-finish')
		def _test_only_wizard_finish():
			return render_template('wizard/wizard-finish.html', page_theme='light', grill_name='Test Grill')

		srv = make_server('127.0.0.1', 0, flask_app)
		port = srv.server_address[1]
		thread = threading.Thread(target=srv.serve_forever, daemon=True)
		thread.start()
		try:
			yield f'http://127.0.0.1:{port}'
		finally:
			srv.shutdown()
			thread.join(timeout=5)
			datastore._reset_for_tests(None)
			os.environ.pop('PIFIRE_DB_PATH', None)

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
