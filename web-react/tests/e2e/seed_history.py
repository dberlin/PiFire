"""Seed (and un-seed) synthetic history rows for the /history e2e spec.

The `/api/history/chart` endpoint is READ-ONLY -- there is no API a browser
test could use to put rows into the history store, and an empty store makes
HistoryPage render its "No history yet" empty state instead of mounting a
chart. The two behaviours history.spec.ts exists to prove (a window change
resetting the x-scale, and the cursor tooltip rendering real values) can only
be exercised against a real uPlot canvas with real data, so the spec shells
out to this script in `beforeAll` and hands the printed id range back to
`afterAll`.

Rows are inserted with explicit, BACKDATED timestamps -- `write_history()`
always stamps `int(time.time() * 1000)`, which would pile every row into the
same instant and collapse the chart's x-range to a few hundred milliseconds.
Driving the INSERT directly is what lets the seeded data span a real window.

Cleanup deletes exactly the id range this script created (reported on stdout
as JSON), never `DELETE FROM history` and never `read_history(flushhistory=
True)`: either of those would also destroy rows written by a concurrently
running control.py.

Usage:
    python seed_history.py seed
    python seed_history.py clean <first_id> <last_id>
"""

import json
import math
import os
import sys
import time

# This script lives three directories down from the repo root; `common` is
# importable from the root, and `common.datastore` resolves the DB path
# relative to its OWN location, so the caller's cwd never matters.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from common import datastore
from common.datastore_accessors import read_settings

# 600 rows, one every 3 seconds, spans 30 minutes of history. The endpoint
# windows by ROW COUNT (`num_items = minutes * SAMPLES_PER_MINUTE`, 20/min),
# not by wall clock, so 600 rows is exactly what `minutes=30` asks for and
# anything above 30 selects the same full set -- which is what lets the spec
# change the window without changing the data.
ROW_COUNT = 600
INTERVAL_MS = 3000


def probe_keys():
    """The primary and food probe keys of the configuration this backend is
    actually running.

    History rows name probe keys, and `prepare_chartdata` renders only the
    keys `settings["history_page"]["probe_config"]` still has -- a hardcoded
    key seeds rows the chart drops, so the spec would measure an empty chart
    rather than the seeded curve. Every grill in the field names its probes
    differently, so the keys come from the configuration, not from here.
    """
    probe_config = read_settings()["history_page"]["probe_config"]
    primary = [key for key, probe in probe_config.items() if probe.get("type") == "Primary"]
    if not primary:
        raise SystemExit(
            "seed_history: settings['history_page']['probe_config'] has no Primary probe "
            f"(configured: {sorted(probe_config) or 'none'}). The history chart's grill series "
            "cannot be seeded against this configuration."
        )
    food = tuple(key for key, probe in probe_config.items() if probe.get("type") == "Food")
    return primary[0], food


def _row_values(i, food_count):
    """A visibly-shaped curve, not a flat line.

    prepare_chartdata reduces with a fidelity tolerance (default 2 degrees),
    so a flat series would collapse to two points and leave nothing to hover
    or zoom into. A 100->400 degree sweep keeps well over a hundred points
    after reduction.
    """
    frac = i / (ROW_COUNT - 1)
    grill = 100.0 + 300.0 * frac + 8.0 * math.sin(frac * 12.0)
    food = [70.0 + 90.0 * frac + 4.0 * math.sin(frac * 7.0 + n) for n in range(food_count)]
    # A step, so the setpoint series is a genuine step function like the real
    # thing (and so the reduce path has an edge it must preserve).
    psp = 225.0 if frac < 0.5 else 250.0
    return grill, food, psp


def seed():
    primary_key, food_keys = probe_keys()
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - ROW_COUNT * INTERVAL_MS

    first_id = None
    last_id = None
    # `transaction()` is BEGIN IMMEDIATE, so this whole loop holds the write
    # lock: the 600 ids it allocates are contiguous and exclusively ours, and
    # no concurrent control.py row can land inside the reported range.
    # (Reading MAX(id) beforehand would NOT be equivalent -- the table is
    # AUTOINCREMENT, so ids resume past the high-water mark rather than
    # restarting at 1 after a delete.)
    with datastore.transaction() as conn:
        for i in range(ROW_COUNT):
            grill, food, psp = _row_values(i, len(food_keys))
            cur = conn.execute(
                "INSERT INTO history(ts,psp,primary_temps,food_temps,aux_temps,"
                "notify_targets,ext_data) VALUES(?,?,?,?,?,?,?)",
                (
                    start_ms + i * INTERVAL_MS,
                    psp,
                    json.dumps({primary_key: round(grill, 1)}),
                    json.dumps({k: round(v, 1) for k, v in zip(food_keys, food, strict=True)}),
                    json.dumps({}),
                    json.dumps({primary_key: 0, **{k: 0 for k in food_keys}}),
                    None,
                ),
            )
            if first_id is None:
                first_id = cur.lastrowid
            last_id = cur.lastrowid

    print(json.dumps({"first_id": first_id, "last_id": last_id, "rows": ROW_COUNT}))


def clean(first_id, last_id):
    with datastore.transaction() as conn:
        cur = conn.execute("DELETE FROM history WHERE id >= ? AND id <= ?", (first_id, last_id))
        deleted = cur.rowcount
    print(json.dumps({"deleted": deleted}))


def main(argv):
    if len(argv) >= 2 and argv[1] == "seed":
        seed()
        return 0
    if len(argv) == 4 and argv[1] == "clean":
        clean(int(argv[2]), int(argv[3]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
