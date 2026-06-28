class FakeHID:
    """Stand-in for hid.device(). Records writes, replays queued responses."""

    def __init__(self):
        self.written = []
        self.responses = []
        self.opened = None
        self.closed = False

    def open(self, vid, pid):
        self.opened = (vid, pid)

    def open_path(self, path):
        self.opened = path

    def write(self, buf):
        self.written.append(bytes(buf))
        return len(buf)

    def read(self, length, timeout_ms=None):
        if self.responses:
            return list(self.responses.pop(0))
        return [0] * length

    def close(self):
        self.closed = True

    # --- test helpers ---
    @property
    def last_report(self):
        return self.written[-1][1:]          # drop the leading report-ID byte

    def queue(self, *reports):
        for r in reports:
            r = bytes(r)
            self.responses.append(r + b"\x00" * (64 - len(r)))
