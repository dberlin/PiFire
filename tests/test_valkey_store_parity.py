import pytest

valkey = pytest.importorskip("valkey")


def _valkey_available():
	try:
		valkey.StrictValkey('localhost', 6379, socket_connect_timeout=0.2).ping()
		return True
	except Exception:
		return False


pytestmark = pytest.mark.skipif(not _valkey_available(), reason="no local valkey-server")


def test_valkey_store_smoke():
	# Read-only smoke: exercises the pass-through against a live server
	# without writing (leaves no residue on a real instance's Valkey).
	from controller.runtime.store import ValkeyStore
	s = ValkeyStore()
	assert isinstance(s.read_control(), dict)
	assert isinstance(s.read_settings(), dict)


def test_valkey_display_queue_roundtrip():
	from controller.runtime.store import ValkeyStore
	s = ValkeyStore()
	s.display_commands().flush()
	s.display_commands().push(['text', 'ERROR'])
	assert s.display_commands().drain() == [['text', 'ERROR']]
