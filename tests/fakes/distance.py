class FakeDistance:
    def __init__(self, level=100):
        self._level = level

    def get_level(self, override=False):
        return self._level

    def update_distances(self, empty, full):
        pass
