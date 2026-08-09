import threading
import time
from datetime import UTC, datetime


class InfluxNotificationHandler:
    def __init__(self, settings) -> None:
        self.queue = []
        # Seeded to 0 (not time.time()) so the very first notify() call after
        # construction -- the caller constructs the handler then calls
        # notify() in the same statement sequence
        # (notify/notifications.py:_send_influxdb_notification) -- is not
        # immediately swallowed by the 1s debounce in notify().
        self.last_updated = 0

        t1 = threading.Thread(
            target=self.publishing_thread,
            daemon=True,
            args=(
                settings["notify_services"]["influxdb"]["url"],
                settings["notify_services"]["influxdb"]["token"],
                settings["notify_services"]["influxdb"]["org"],
                settings["notify_services"]["influxdb"]["bucket"],
            ),
        )
        t1.start()

    def publishing_thread(self, url, token, org, bucket):
        from influxdb_client import InfluxDBClient

        bucket = bucket

        client = InfluxDBClient(url=url, token=token, org=org)

        from influxdb_client import WriteOptions

        write_api = None

        while True:
            time.sleep(1)
            if not write_api:
                write_api = client.write_api(
                    write_options=WriteOptions(
                        batch_size=100,
                        flush_interval=5_000,
                        jitter_interval=2_000,
                        retry_interval=2_000,
                        max_retries=3,
                        max_retry_delay=30_000,
                        exponential_base=2,
                    )
                )

            try:
                buf = self.queue.copy()
                if len(buf) > 0:
                    write_api.write(bucket, org, buf)
                    # Only drop the points we just wrote successfully -- not
                    # the whole queue -- so any points appended concurrently
                    # (by notify(), on another thread) while write() was in
                    # flight are preserved. If write() raises, the except
                    # below runs instead and self.queue is left untouched
                    # entirely, so the buffered points are retried on the
                    # next loop iteration instead of being silently dropped.
                    del self.queue[: len(buf)]
                time.sleep(5)
            except:
                write_api = None
                time.sleep(10)

    def notify(self, notifyevent, control, settings, pelletdb, in_data, grill_platform):
        if time.time() - self.last_updated < 1:
            return

        from influxdb_client import Point

        name = settings["globals"]["grill_name"]
        if len(name) == 0:
            name = "Smoker"

        def get_or_default(data, k, default):
            if data is not None and k in data:
                return data[k]
            return default

        PrimaryKey = list(in_data["probe_history"]["primary"].keys())[0]
        # Grills may be configured with 0, 1, or 2+ food probes -- don't
        # assume >=2 are present. Missing probes degrade to 0.0 rather than
        # raising IndexError (which had no handler anywhere up the call
        # chain and would silently kill notify() for any non-2-food-probe
        # configuration).
        food_probe_keys = list(in_data["probe_history"]["food"].keys())
        Probe1Key = food_probe_keys[0] if len(food_probe_keys) > 0 else None
        Probe2Key = food_probe_keys[1] if len(food_probe_keys) > 1 else None

        PrimaryTemp = in_data["probe_history"]["primary"][PrimaryKey]
        PrimarySetpoint = in_data["primary_setpoint"]
        PrimaryNotify = in_data["notify_targets"][PrimaryKey]
        Probe1Temp = in_data["probe_history"]["food"][Probe1Key] if Probe1Key is not None else 0.0
        Probe1Notify = in_data["notify_targets"][Probe1Key] if Probe1Key is not None else 0.0
        Probe2Temp = in_data["probe_history"]["food"][Probe2Key] if Probe2Key is not None else 0.0
        Probe2Notify = in_data["notify_targets"][Probe2Key] if Probe2Key is not None else 0.0

        p = (
            Point(name)
            .time(time=datetime.now(UTC))
            .field("GrillTemp", float(PrimaryTemp))
            .field("GrillSetPoint", float(PrimarySetpoint))
            .field("GrillNotifyPoint", float(PrimaryNotify))
            .field("Probe1Temp", float(Probe1Temp))
            .field("Probe1SetPoint", float(Probe1Notify))
            .field("Probe2Temp", float(Probe2Temp))
            .field("Probe2SetPoint", float(Probe2Notify))
            .field("Mode", str(get_or_default(control, "mode", "unknown")))
            .field("PelletLevel", int(get_or_default(get_or_default(pelletdb, "current", {}), "hopper_level", 100)))
        )
        if grill_platform is not None:
            outputs = grill_platform.GetOutputStatus()
            for key in outputs:
                p = p.field(key, int(outputs[key]))

        if notifyevent and "GRILL_STATE" != notifyevent:
            p = p.field("Event", str(notifyevent))

        self.queue.append(p)

        self.last_updated = time.time()
