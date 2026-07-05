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
	from controller.runtime.store import ValkeyStore
	s = ValkeyStore()
	s.read_control()  # smoke: must not raise
	s.write_generic_key('parity_probe', {'ok': True})


def test_valkey_display_queue_roundtrip():
	from controller.runtime.store import ValkeyStore
	s = ValkeyStore()
	s.display_commands().flush()
	s.display_commands().push(['text', 'ERROR'])
	assert s.display_commands().drain() == [['text', 'ERROR']]
