"""Hand-built minimal-but-valid `.pifire` / `.pfrecipe` archives for tests.

Not a test module (no `test_` prefix), so pytest imports it without collecting
it. Extracted from tests/web/test_page_cookfile.py::_write_cookfile, which
still owns its own copy because its module docstring documents field-by-field
why each member is load-bearing; the /api/files test modules share this one
rather than growing a third and fourth copy.

Everything is written into a caller-supplied directory -- always a
`tempfile.mkdtemp` in practice. Nothing here touches the repo checkout.
"""

import json
import time
import uuid
import zipfile


def write_cookfile(history_dir, title, *, version=None, comments=None, assets=None, numeric_times=True):
    """Write `{title}.pifire` into `history_dir` and return the bare filename.

    `numeric_times=False` writes the pre-1.5 style string `time_labels`
    ("12:00:00") that upgrade_cookfile's old path and hand-built files can
    still carry -- the case the chart adapter has to survive.
    """
    from common.datastore_accessors import read_settings
    from common.defaults import default_metrics

    if version is None:
        version = read_settings()["versions"]["cookfile"]

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 3600_000

    event_start = default_metrics()
    event_start.update(
        {
            "id": 0,
            "starttime": start_ms,
            "endtime": now_ms,
            "mode": "Smoke",
            "augerontime": 120,
            "pellet_level_start": 100,
            "pellet_level_end": 95,
        }
    )
    event_end = default_metrics()
    event_end.update(
        {
            "id": 1,
            "starttime": now_ms,
            "endtime": now_ms,
            "mode": "Stop",
            "augerontime": 30,
            "pellet_level_start": 95,
            "pellet_level_end": 90,
        }
    )

    files = {
        "metadata.json": {
            "title": title,
            "starttime": start_ms,
            "endtime": now_ms,
            "units": "F",
            "thumbnail": "",
            "id": str(uuid.uuid4()),
            "version": version,
        },
        "graph_data.json": {
            "time_labels": [start_ms, now_ms] if numeric_times else ["12:00:00", "12:05:00"],
            "chart_data": [{"label": "Grill", "borderColor": "#f00", "data": [225, 230]}],
            "probe_mapper": {"probes": {"grill1": 0}, "targets": {}, "primarysp": {}},
        },
        "raw_data.json": [
            {
                "T": start_ms,
                "P": {"grill1": 225},
                "PSP": 225,
                "F": {"probe1": 150},
                "NT": {"grill1": 225, "probe1": 165},
                "AUX": {},
            },
            {
                "T": now_ms,
                "P": {"grill1": 230},
                "PSP": 225,
                "F": {"probe1": 160},
                "NT": {"grill1": 225, "probe1": 165},
                "AUX": {},
            },
        ],
        "graph_labels.json": {"probes": {"grill1": "Grill"}, "targets": {}, "primarysp": {}},
        "events.json": [event_start, event_end],
        "comments.json": comments if comments is not None else [],
        "assets.json": assets if assets is not None else [],
    }

    filename = f"{title}.pifire"
    with zipfile.ZipFile(history_dir + filename, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, json.dumps(data))
    return filename


def write_recipe(recipe_dir, title):
    """Write `{title}.pfrecipe` into `recipe_dir` and return the bare filename."""
    files = {
        "metadata.json": {
            "title": title,
            "id": str(uuid.uuid4()),
            "author": "",
            "description": "",
            "image": "",
            "thumbnail": "",
            "units": "F",
            "prep_time": 0,
            "cook_time": 0,
            "rating": 5,
            "difficulty": "Easy",
            "version": "1.1.0",
            "food_probes": 2,
            "username": "",
        },
        "recipe.json": {"ingredients": [], "instructions": [], "steps": []},
        "comments.json": [],
        "assets.json": [],
    }
    filename = f"{title}.pfrecipe"
    with zipfile.ZipFile(recipe_dir + filename, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, json.dumps(data))
    return filename
