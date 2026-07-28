import logging
import time

from common import datastore


class SqliteLogHandler(logging.Handler):
    """Log sink writing formatted records into the logs table under `name`.

    Retention is not this handler's business: the `logs_prune` trigger in
    common.datastore trims the table. Doing it here would only cover records
    that came through a handler instance, and would reset on every
    reset_loggers() -- see _logs_retention_ddl for why that matters.
    """

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
