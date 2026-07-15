import logging
import time

from common import datastore


class SqliteLogHandler(logging.Handler):
    """Log sink writing formatted records into the logs table under `name`."""

    def __init__(self, name):
        super().__init__()
        self.name = name

    def emit(self, record):
        try:
            datastore.execute_write(
                "INSERT INTO logs(name, ts, message) VALUES(?,?,?)",
                (self.name, int(time.time() * 1000), self.format(record)),
            )
        except Exception:  # never let logging crash the caller
            self.handleError(record)
