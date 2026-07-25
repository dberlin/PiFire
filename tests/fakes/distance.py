class FakeDistance:
    def __init__(self, level=100):
        self._level = level
        self.sample_requests = 0

    def request_sample(self):
        self.sample_requests += 1

    def get_level(self):
        return self._level

    def update_distances(self, empty, full):
        pass
