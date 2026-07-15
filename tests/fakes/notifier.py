class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.checks = []

    def send(self, name):
        self.sent.append(name)

    def check(self, settings, control, **kwargs):
        self.checks.append(kwargs)
        return control

    def get_targets(self, notify_data):
        return {}
