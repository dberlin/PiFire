"""Notification seam so the control loop can be tested without a real backend."""

from abc import ABC, abstractmethod


class Notifier(ABC):
	@abstractmethod
	def send(self, name): ...
	@abstractmethod
	def check(self, settings, control, **kwargs): ...
	@abstractmethod
	def get_targets(self, notify_data): ...


class LiveNotifier(Notifier):
	def send(self, name):
		from notify.notifications import send_notifications

		send_notifications(name)

	def check(self, settings, control, **kwargs):
		from notify.notifications import check_notify

		return check_notify(settings, control, **kwargs)

	def get_targets(self, notify_data):
		from common import get_notify_targets

		return get_notify_targets(notify_data)
