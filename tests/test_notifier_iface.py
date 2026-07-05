from controller.runtime.notifier import Notifier


def test_valkey_notifier_is_a_notifier():
	from controller.runtime.notifier import ValkeyNotifier
	assert isinstance(ValkeyNotifier(), Notifier)
