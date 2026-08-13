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
    from common.persistence.runtime import read_settings
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
            "chart_data": [
                {
                    "label": "Grill",
                    "borderColor": "#f00",
                    "hidden": False,
                    #  prepare_chartdata appends {"x": epoch_ms, "y": temp} points
                    #  (file_mgmt/cookfile.py:449-463), not bare numbers.
                    "data": [{"x": start_ms, "y": 225}, {"x": now_ms, "y": 230}],
                }
            ],
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


def write_recipe(
    recipe_dir,
    title,
    *,
    food_probes=2,
    ingredients=None,
    instructions=None,
    steps=None,
    units="F",
):
    """Build a minimal-but-realistic .pfrecipe under recipe_dir.

    Defaults match file_mgmt/recipes.py's own defaults so a fixture archive and
    a create_recipefile() archive are interchangeable in a test.
    """
    if steps is None:
        steps = [
            {
                "mode": "Smoke",
                "trigger_temps": {"primary": 0, "food": [0] * food_probes},
                "hold_temp": 0,
                "timer": 0,
                "notify": False,
                "message": "",
                "pause": False,
            }
        ]
    if ingredients is None:
        ingredients = []
    if instructions is None:
        instructions = []
    files = {
        "metadata.json": {
            "title": title,
            "id": str(uuid.uuid4()),
            "author": "",
            "description": "",
            "image": "",
            "thumbnail": "",
            "units": units,
            "prep_time": 0,
            "cook_time": 0,
            "rating": 5,
            "difficulty": "Easy",
            "version": "1.1.0",
            "food_probes": food_probes,
            "username": "",
        },
        "recipe.json": {"ingredients": ingredients, "instructions": instructions, "steps": steps},
        "comments.json": [],
        "assets.json": [],
    }
    filename = f"{title}.pfrecipe"
    with zipfile.ZipFile(recipe_dir + filename, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, json.dumps(data))
    return filename


def write_legacy_cookfile(history_dir, title):
    """Write a genuine pre-v1.5.0-shaped `{title}.pifire` and return its name.

    Flat `graph_data` (grill1_temp / grill1_setpoint / probe1_temp / ... /
    time_labels) and flat `graph_labels` (grill1_label / probe1_label /
    probe2_label), exactly what upgrade_cookfile's conversion branch expects to
    read (file_mgmt/cookfile.py:220-227, :253-263). `raw_data.json` is omitted
    on purpose so the raw-data-reconstruction branch (:212-232) runs too.

    Same construction as tests/unit/file_mgmt/test_cookfile.py's
    _write_old_format_pifire; kept here so the HTTP tests can exercise the
    Attempt Conversion flow end to end.
    """
    files = {
        "metadata.json": {
            "title": title,
            "starttime": 1000,
            "endtime": 2000,
            "units": "F",
            "thumbnail": "",
            "id": str(uuid.uuid4()),
            "version": "1.0.0",
        },
        # raw_data.json intentionally omitted
        "graph_data.json": {
            "time_labels": [1000, 2000],
            "grill1_temp": [100, 110],
            "grill1_setpoint": [225, 225],
            "probe1_temp": [90, 95],
            "probe2_temp": [80, 85],
            "probe1_setpoint": [165, 165],
            "probe2_setpoint": [165, 165],
        },
        "graph_labels.json": {
            "grill1_label": "Grill",
            "probe1_label": "Probe 1",
            "probe2_label": "Probe 2",
        },
        "events.json": [],
        "comments.json": [],
        "assets.json": [],
    }
    filename = f"{title}.pifire"
    with zipfile.ZipFile(history_dir + filename, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, json.dumps(data))
    return filename
