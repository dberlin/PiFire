"""Unit coverage for file_mgmt/cookfile.py: `read_cookfile`, `upgrade_cookfile`,
`prepare_chartdata`, and `create_cookfile` -- the read/upgrade/build pipeline
for PiFire `.pifire` cook-file zip archives (metadata/graph_data/raw_data/
graph_labels/events/comments/assets JSON members, see `_default_cookfilestruct`).

Fixture strategy
-----------------
`.pifire` archives are hand-built directly with `zipfile`/`json` (mirrors
`tests/web/test_page_cookfile.py`'s `_write_cookfile`, but self-contained here
since these are DB-only unit tests with no live_server/Playwright). All
archives live under `tmp_path` -- nothing is written under the repo tree
except PiFire's own gitignored `./logs/events.log` (already exercised the
same way by other tests; see `test_create_logger_handlers.py`). `assets.json`
is always `[]` in these fixtures so `read_json_file_data`'s default
`unpackassets=True` path (used internally by `read_cookfile`) never touches
`./static/img/tmp/`.

Three LATENT BUGS found and pinned (not fixed) here -- see each test's
docstring for detail:

1. HIGH -- `upgrade_cookfile`'s pre-v1.5.0 `graph_data` conversion branch
   (file_mgmt/cookfile.py:278) always crashes with `KeyError: 'history_page'`
   because it calls `default_probe_config()` (common/defaults.py:315) with an
   ad-hoc dict that only has a `probe_settings` key. Since `graph_data` is
   processed before `graph_labels`/`events`/`comments`/`assets` in
   `json_types`, NO pre-v1.5.0 `.pifire` file can currently be upgraded --
   the very feature `upgrade_cookfile` exists for is unreachable for its
   oldest supported input.
2. HIGH -- `read_cookfile` (file_mgmt/cookfile.py:183) crashes with
   `KeyError: 'version'` on a corrupt/non-zip `.pifire` file, instead of
   returning the `status` error string `read_json_file_data` already
   produced. The version-check block runs BEFORE the `if status != "OK":
   break` guard, so the documented corrupt-file error path
   (`blueprints/cookfile/routes.py` branches on `status != "OK"` to render
   `cferror.html`) is unreachable -- a corrupt upload/history file 500s the
   Flask route instead of showing the friendly error page.
3. LOW/MEDIUM -- `prepare_chartdata`'s history-population loop
   (file_mgmt/cookfile.py:423-427) does `for key in probe_mapper["primarysp"]:
   ... ; break` -- it only ever appends the primary-setpoint series for the
   FIRST "Primary"-type probe, per index. If a probe_config has more than one
   "Primary" probe (schema-permitted, if unusual), every additional Primary
   probe's setpoint series is silently left permanently empty across the
   whole chart -- no crash, just missing chart data.
"""

import json
import pathlib
import zipfile

import pytest

import file_mgmt.cookfile as cookfile_mod
from common.datastore_accessors import read_history, read_metrics, write_history, write_metrics
from common.defaults import default_metrics
from file_mgmt.cookfile import create_cookfile, prepare_chartdata, read_cookfile, upgrade_cookfile


class _FrozenDateTime:
    """A datetime.datetime replacement whose `.now()` always returns the
    same fixed instant, so two back-to-back `create_cookfile()` calls compute
    the exact same title -- reproducing a same-clock-minute collision on
    demand (mirrors tests/unit/file_mgmt/test_recipes.py's `_FrozenDateTime`)."""

    import datetime as _dt

    _frozen = _dt.datetime(2026, 7, 18, 12, 34)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen


@pytest.fixture
def isolated_history_folder(tmp_path, monkeypatch):
    history_dir = str(tmp_path / "history") + "/"
    monkeypatch.setattr(cookfile_mod, "HISTORY_FOLDER", history_dir)
    return history_dir


def _write_zip(path, files):
    """Write a `.pifire`-shaped zip at `path` from a {member_name: obj} dict
    (obj is json.dumps'd; member_name should include the .json extension)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, obj in files.items():
            archive.writestr(name, json.dumps(obj))


def _current_version(ds):
    return cookfile_mod.read_settings()["versions"]["cookfile"]


def _base_metadata(version, **overrides):
    metadata = {
        "title": "Test-CookFile",
        "starttime": 1000,
        "endtime": 2000,
        "units": "F",
        "thumbnail": "",
        "id": "test-id",
        "version": version,
    }
    metadata.update(overrides)
    return metadata


# ---------------------------------------------------------------------------
# prepare_chartdata
# ---------------------------------------------------------------------------

_PROBE_CONFIG = {
    "grill1": {
        "name": "Grill",
        "type": "Primary",
        "enabled": True,
        "bg_color": "#111",
        "line_color": "#222",
        "bg_color_target": "#333",
        "line_color_target": "#444",
        "bg_color_setpoint": "#555",
        "line_color_setpoint": "#666",
    },
    "probe1": {
        "name": "Probe 1",
        "type": "Food",
        "enabled": True,
        "bg_color": "#777",
        "line_color": "#888",
        "bg_color_target": "#999",
        "line_color_target": "#aaa",
    },
}


def test_prepare_chartdata_empty_history_returns_single_placeholder_point(ds):
    """With no rows in the datastore and `history=None` (the default),
    `read_history()` comes back `[]`, taking the `list_length == 0` branch
    (cookfile.py:430-439): a single now()-timestamped point of value 0 per
    probe/target/primarysp series, instead of a real series."""
    result = prepare_chartdata(_PROBE_CONFIG, num_items=10, reduce=True, data_points=60)

    assert len(result["time_labels"]) == 1
    probe_mapper = result["probe_mapper"]
    assert result["chart_data"][probe_mapper["probes"]["grill1"]]["data"] == [0]
    assert result["chart_data"][probe_mapper["targets"]["grill1"]]["data"] == [0]
    assert result["chart_data"][probe_mapper["primarysp"]["grill1"]]["data"] == [0]
    assert result["chart_data"][probe_mapper["probes"]["probe1"]]["data"] == [0]
    # probe1 is "Food" type -- no primarysp entry created for it at all.
    assert "probe1" not in probe_mapper["primarysp"]


def test_prepare_chartdata_with_datastore_history_builds_real_series(ds):
    """`history=None` + real rows written via `write_history` exercises the
    `read_history()` + `unpack_history()` path (cookfile.py:386-389), which
    no prior test hit (every existing caller either passed reduce=True with
    num_items > data_points, hitting only the OTHER branch of the step-calc,
    or never got to a non-empty-history read). Mirrors exactly how
    `create_cookfile()` itself calls this function (num_items=0, reduce=False,
    data_points=0)."""
    write_history(
        {
            "probe_history": {"primary": {"grill1": 100}, "food": {"probe1": 90}, "aux": {}},
            "primary_setpoint": 225,
            "notify_targets": {"grill1": 225, "probe1": 165},
        }
    )
    write_history(
        {
            "probe_history": {"primary": {"grill1": 110}, "food": {"probe1": 95}, "aux": {}},
            "primary_setpoint": 225,
            "notify_targets": {"grill1": 225, "probe1": 165},
        }
    )

    result = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0)

    assert len(result["time_labels"]) == 2
    probe_mapper = result["probe_mapper"]
    grill_data = result["chart_data"][probe_mapper["probes"]["grill1"]]["data"]
    probe_data = result["chart_data"][probe_mapper["probes"]["probe1"]]["data"]
    assert [pt["y"] for pt in grill_data] == [100, 110]
    assert [pt["y"] for pt in probe_data] == [90, 95]


def test_prepare_chartdata_explicit_history_uses_its_own_length():
    """Passing `history=` explicitly (as `upgrade_cookfile`'s pre-1.5.0
    `graph_data` conversion does) takes the `else: list_length =
    len(history["T"])` branch (cookfile.py:393) instead of querying the
    datastore at all. Asserts every series (probe/target/primarysp) carries
    the real seeded values through in order -- not just "it ran"."""
    history = {
        "T": [1000, 2000, 3000],
        "PSP": [225, 226, 227],
        "P": {"grill1": [100, 110, 120]},
        "F": {"probe1": [90, 95, 100]},
        "NT": {"grill1": [225, 225, 225], "probe1": [165, 165, 165]},
    }

    result = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history)

    pm = result["probe_mapper"]
    assert result["time_labels"] == [1000, 2000, 3000]
    assert [p["y"] for p in result["chart_data"][pm["probes"]["grill1"]]["data"]] == [100, 110, 120]
    assert [p["y"] for p in result["chart_data"][pm["probes"]["probe1"]]["data"]] == [90, 95, 100]
    assert [p["y"] for p in result["chart_data"][pm["targets"]["grill1"]]["data"]] == [225, 225, 225]
    assert [p["y"] for p in result["chart_data"][pm["targets"]["probe1"]]["data"]] == [165, 165, 165]
    assert [p["y"] for p in result["chart_data"][pm["primarysp"]["grill1"]]["data"]] == [225, 226, 227]


def test_prepare_chartdata_clamps_num_items_to_available_history_length():
    """Requesting more items (`num_items=10`) than actually exist
    (3 history rows) must clamp `num_items` down to the real length
    (cookfile.py:395-396) -- without the clamp, `range(list_length -
    num_items, list_length, step)` = `range(-7, 3, 1)` and `history["T"][-7]`
    on a 3-element list raises IndexError. This proves the clamp works, not
    just that no exception happened to occur."""
    history = {
        "T": [1000, 2000, 3000],
        "PSP": [225, 226, 227],
        "P": {"grill1": [100, 110, 120]},
        "F": {"probe1": [90, 95, 100]},
        "NT": {"grill1": [225, 225, 225], "probe1": [165, 165, 165]},
    }

    result = prepare_chartdata(_PROBE_CONFIG, num_items=10, reduce=False, data_points=0, history=history)

    assert result["time_labels"] == [1000, 2000, 3000]


def test_prepare_chartdata_reduce_true_downsamples_by_step(ds):
    """`reduce=True` with `num_items > data_points` takes the `step =
    int(num_items / data_points)` branch (cookfile.py:398-399) -- downsamples
    by keeping every `step`-th point instead of all of them. 6 real history
    rows, `data_points=2` -> `step = int(6/2) = 3` -> only indices 0 and 3
    are kept, not all 6."""
    for i in range(6):
        write_history(
            {
                "probe_history": {"primary": {"grill1": 100 + i}, "food": {"probe1": 90}, "aux": {}},
                "primary_setpoint": 225,
                "notify_targets": {"grill1": 225, "probe1": 165},
            }
        )

    result = prepare_chartdata(_PROBE_CONFIG, num_items=6, reduce=True, data_points=2)

    pm = result["probe_mapper"]
    grill_values = [pt["y"] for pt in result["chart_data"][pm["probes"]["grill1"]]["data"]]
    assert grill_values == [100, 103]
    assert len(result["time_labels"]) == 2


def test_prepare_chartdata_food_only_probe_config_skips_primarysp_loop():
    """A probe_config with no "Primary"-type probe leaves
    `probe_mapper["primarysp"]` empty, so the `for key in
    probe_mapper["primarysp"]:` body (cookfile.py:423-427) never executes for
    any index -- confirms that's a safe no-op, not a crash, and time_labels
    still gets built correctly."""
    probe_config = {"probe1": _PROBE_CONFIG["probe1"]}
    history = {"T": [1000, 2000], "PSP": [225, 225], "P": {}, "F": {"probe1": [90, 95]}, "NT": {"probe1": [165, 165]}}

    result = prepare_chartdata(probe_config, num_items=0, reduce=False, data_points=0, history=history)

    assert result["probe_mapper"]["primarysp"] == {}
    assert result["time_labels"] == [1000, 2000]


def test_prepare_chartdata_custom_chart_info_skips_default_template():
    """Passing a non-empty `chart_info=` skips the whole default-template
    dict literal (cookfile.py:305->331 branch, previously never taken by any
    test) -- `chart_obj = chart_info.copy()` starts from the caller's dict
    instead. Verify the caller's custom key survives into the output AND the
    per-probe fields are still populated correctly on top of it."""
    history = {
        "T": [1000],
        "PSP": [225],
        "P": {"grill1": [100]},
        "F": {"probe1": [90]},
        "NT": {"grill1": [225], "probe1": [165]},
    }

    result = prepare_chartdata(
        _PROBE_CONFIG,
        chart_info={"pointStyle": "custom-marker"},
        num_items=0,
        reduce=False,
        data_points=0,
        history=history,
    )

    pm = result["probe_mapper"]
    grill_chart_obj = result["chart_data"][pm["probes"]["grill1"]]
    assert grill_chart_obj["pointStyle"] == "custom-marker"
    assert grill_chart_obj["label"] == "Grill"
    assert grill_chart_obj["backgroundColor"] == "#111"
    # None of the default template's other keys (e.g. "fill", "lineTension")
    # leak in -- chart_obj only ever has what chart_info had plus what the
    # function explicitly assigns.
    assert "lineTension" not in grill_chart_obj


def test_prepare_chartdata_multiple_primary_probes_only_first_gets_setpoint_data():
    """LATENT BUG (#3, LOW/MEDIUM severity) -- file_mgmt/cookfile.py:419-427.

    `for key in probe_mapper["primarysp"]: chart_data[...].append(...);
    break` appends the shared `history["PSP"]` value to only the FIRST
    "Primary"-type probe's series, per history index, then `break`s out of
    the inner loop. With two "Primary" probes in probe_config (allowed by
    the schema, if atypical), the second Primary probe's setpoint series is
    NEVER populated across the entire chart -- not even a placeholder, just
    permanently `[]` -- even though real PSP data exists in history. No
    crash, just silently-dropped chart data for any multi-primary
    configuration."""
    probe_config = {
        "grill1": dict(_PROBE_CONFIG["grill1"]),
        "grill2": dict(_PROBE_CONFIG["grill1"], name="Grill 2"),
    }
    history = {
        "T": [1000, 2000],
        "PSP": [225, 230],
        "P": {"grill1": [100, 105], "grill2": [90, 95]},
        "F": {},
        "NT": {"grill1": [225, 225], "grill2": [225, 225]},
    }

    result = prepare_chartdata(probe_config, num_items=0, reduce=False, data_points=0, history=history)

    pm = result["probe_mapper"]
    assert result["chart_data"][pm["primarysp"]["grill1"]]["data"] == [
        {"x": 1000, "y": 225},
        {"x": 2000, "y": 230},
    ]
    # The bug: grill2 is also "Primary", has its own primarysp chart slot,
    # but it never receives any data points at all.
    assert result["chart_data"][pm["primarysp"]["grill2"]]["data"] == []


# ---------------------------------------------------------------------------
# read_cookfile
# ---------------------------------------------------------------------------


def test_read_cookfile_ok_returns_full_struct_matching_file_contents(ds, tmp_path):
    """Happy path: current-version file, all 7 members present and
    well-formed. Asserts the returned struct is exactly what was written --
    not just `status == "OK"`."""
    version = _current_version(ds)
    metadata = _base_metadata(version, title="My Cook", starttime=111, endtime=222)
    graph_data = {
        "time_labels": [111, 222],
        "chart_data": [{"label": "Grill", "data": [1, 2]}],
        "probe_mapper": {"probes": {"grill1": 0}},
    }
    raw_data = [{"T": 111, "P": {"grill1": 100}, "PSP": 225, "F": {}, "NT": {}, "AUX": {}}]
    graph_labels = {"probes": {"grill1": "Grill"}, "targets": {}, "primarysp": {}}
    events = [dict(default_metrics(), id=0, mode="Smoke")]
    comments = [{"id": "c1", "text": "hi", "assets": []}]

    path = str(tmp_path / "ok.pifire")
    _write_zip(
        path,
        {
            "metadata.json": metadata,
            "graph_data.json": graph_data,
            "raw_data.json": raw_data,
            "graph_labels.json": graph_labels,
            "events.json": events,
            "comments.json": comments,
            "assets.json": [],
        },
    )

    struct, status = read_cookfile(path)

    assert status == "OK"
    assert struct["metadata"]["title"] == "My Cook"
    assert struct["graph_data"] == graph_data
    assert struct["raw_data"] == raw_data
    assert struct["graph_labels"] == graph_labels
    assert struct["events"] == events
    assert struct["comments"] == comments
    assert struct["assets"] == []


def test_read_cookfile_old_version_returns_warning_and_only_metadata(ds, tmp_path):
    """A file whose metadata version is below `settings["versions"]["cookfile"]`
    sets `status` to the "WARNING: Older cookfile version format!" string
    (cookfile.py:192). Because that status is also `!= "OK"`, the very next
    check (cookfile.py:193-194) breaks the loop immediately after
    "metadata" -- by design (blueprints/cookfile/routes.py's
    `classify_cookfile_error` matches "version" in the status string and
    routes to the upgrade-prompt error page rather than rendering
    partially-old-schema data), NOT a bug: this characterizes that only
    `metadata` ends up populated, nothing else."""
    metadata = _base_metadata("1.0.0")
    path = str(tmp_path / "old.pifire")
    _write_zip(
        path,
        {
            "metadata.json": metadata,
            "graph_data.json": {},
            "raw_data.json": [],
            "graph_labels.json": {},
            "events.json": [],
            "comments.json": [],
            "assets.json": [],
        },
    )

    struct, status = read_cookfile(path)

    assert status.startswith("WARNING: Older cookfile version format!")
    assert list(struct.keys()) == ["metadata"]
    assert struct["metadata"]["version"] == "1.0.0"


def test_read_cookfile_corrupt_zip_raises_keyerror_instead_of_returning_status(ds, tmp_path):
    """LATENT BUG (#2, HIGH severity) -- file_mgmt/cookfile.py:183.

    `read_json_file_data()` (file_mgmt/common.py) already handles a corrupt
    (non-zip) file gracefully, returning `({}, "Error: ...")`. But
    `read_cookfile`'s metadata-version-check block runs UNCONDITIONALLY right
    after that read, before the `status != "OK"` guard:
    `fileversion = semantic_ver_to_list(cook_file_struct["metadata"]["version"])`
    -- indexing `["version"]` on the empty `{}` dict raises `KeyError:
    'version'`, propagating out of `read_cookfile` entirely instead of
    returning the documented error status. Every caller in
    blueprints/cookfile/routes.py and blueprints/history/routes.py branches
    on `status != "OK"` to render a friendly `cferror.html` -- for a genuinely
    corrupt `.pifire` (the exact case this is meant to handle), the route
    would 500 instead."""
    path = str(tmp_path / "corrupt.pifire")
    with open(path, "wb") as f:
        f.write(b"this is not a zip file")

    with pytest.raises(KeyError, match="version"):
        read_cookfile(path)


# ---------------------------------------------------------------------------
# upgrade_cookfile
# ---------------------------------------------------------------------------


def test_upgrade_cookfile_current_version_passes_data_through_unchanged(ds, tmp_path):
    """Already-current-version file: every member falls through to the
    generic `else: cookfilestruct[jsonfile] = jsondata` branch, and the
    change is persisted back to the archive (verified via a second
    `read_cookfile` on the same path)."""
    version = _current_version(ds)
    metadata = _base_metadata(version)
    graph_data = {"time_labels": [111], "chart_data": [], "probe_mapper": {}}
    graph_labels = {"probes": {}, "targets": {}, "primarysp": {}}
    raw_data = [{"T": 111, "P": {}, "PSP": 0, "F": {}, "NT": {}, "AUX": {}}]
    events = []
    comments = [{"id": "c1", "text": "hi", "assets": []}]

    path = str(tmp_path / "current.pifire")
    _write_zip(
        path,
        {
            "metadata.json": metadata,
            "graph_data.json": graph_data,
            "raw_data.json": raw_data,
            "graph_labels.json": graph_labels,
            "events.json": events,
            "comments.json": comments,
            "assets.json": [],
        },
    )

    struct, status = upgrade_cookfile(path)

    assert status == "OK"
    assert struct["metadata"]["version"] == version
    assert struct["graph_data"] == graph_data
    assert struct["raw_data"] == raw_data
    assert struct["comments"] == comments

    reread, reread_status = read_cookfile(path)
    assert reread_status == "OK"
    assert reread["raw_data"] == raw_data


def test_upgrade_cookfile_stops_on_read_error_for_non_raw_data_member(ds, tmp_path):
    """When a member OTHER than `raw_data` fails to read (here:
    `comments.json` is simply absent from the archive), the loop takes the
    `elif status != "OK": break` branch (cookfile.py:230-231) and stops --
    members already processed (`metadata`/`raw_data`/`graph_data`/
    `graph_labels`/`events`) are in the result, but `comments`/`assets`
    never get processed and stay at `_default_cookfilestruct()`'s defaults."""
    version = _current_version(ds)
    metadata = _base_metadata(version)
    graph_data = {"time_labels": [111], "chart_data": [], "probe_mapper": {}}
    graph_labels = {"probes": {}, "targets": {}, "primarysp": {}}
    raw_data = [{"T": 111, "P": {}, "PSP": 0, "F": {}, "NT": {}, "AUX": {}}]
    events = [dict(default_metrics(), id=0, mode="Smoke")]

    path = str(tmp_path / "missing_comments.pifire")
    _write_zip(
        path,
        {
            "metadata.json": metadata,
            "graph_data.json": graph_data,
            "raw_data.json": raw_data,
            "graph_labels.json": graph_labels,
            "events.json": events,
            # comments.json intentionally omitted
            "assets.json": [],
        },
    )

    struct, status = upgrade_cookfile(path)

    assert status != "OK"
    assert status.startswith("Error")
    assert struct["events"] == events
    # Never reached -- still the _default_cookfilestruct() defaults.
    assert struct["comments"] == []
    assert struct["assets"] == []


def _write_old_format_pifire(path, comments=None, assets=None):
    """Build a genuine pre-v1.5.0-shaped `.pifire`: flat `graph_data`
    (`grill1_temp`/`grill1_setpoint`/`probe1_temp`/`probe2_temp`/
    `probe1_setpoint`/`probe2_setpoint`/`time_labels`) and flat
    `graph_labels` (`grill1_label`/`probe1_label`/`probe2_label`), matching
    exactly what cookfile.py:216-227 and :250-260 expect to read. No
    `raw_data.json` member at all, so the raw_data-reconstruction-from-
    graph_data branch (cookfile.py:210-229) is exercised."""
    metadata = _base_metadata("1.0.0")
    graph_data = {
        "time_labels": [1000, 2000],
        "grill1_temp": [100, 110],
        "grill1_setpoint": [225, 225],
        "probe1_temp": [90, 95],
        "probe2_temp": [80, 85],
        "probe1_setpoint": [165, 165],
        "probe2_setpoint": [165, 165],
    }
    graph_labels = {"grill1_label": "Grill", "probe1_label": "Probe 1", "probe2_label": "Probe 2"}
    _write_zip(
        path,
        {
            "metadata.json": metadata,
            # raw_data.json intentionally omitted
            "graph_data.json": graph_data,
            "graph_labels.json": graph_labels,
            "events.json": [],
            "comments.json": comments if comments is not None else [{"id": "c1", "text": "hi"}],
            "assets.json": assets if assets is not None else {},
        },
    )


def _stub_probe_config(_settings_dict):
    """A stand-in for `common.defaults.default_probe_config` that returns a
    valid probe_config for the exact grill1/probe1/probe2 labels
    `upgrade_cookfile`'s pre-1.5.0 branch hardcodes (cookfile.py:270-274),
    WITHOUT the `KeyError: 'history_page'` bug (see latent bug #1) --
    used to exercise the surrounding (correct) conversion logic in
    isolation from that separate, already-pinned bug."""
    return {
        "grill1": {
            "name": "Grill",
            "type": "Primary",
            "enabled": True,
            "bg_color": "#1",
            "line_color": "#2",
            "bg_color_target": "#3",
            "line_color_target": "#4",
            "bg_color_setpoint": "#5",
            "line_color_setpoint": "#6",
        },
        "probe1": {
            "name": "Probe 1",
            "type": "Food",
            "enabled": True,
            "bg_color": "#7",
            "line_color": "#8",
            "bg_color_target": "#9",
            "line_color_target": "#a",
        },
        "probe2": {
            "name": "Probe 2",
            "type": "Food",
            "enabled": True,
            "bg_color": "#b",
            "line_color": "#c",
            "bg_color_target": "#d",
            "line_color_target": "#e",
        },
    }


def test_upgrade_cookfile_pre_1_5_0_graph_data_conversion_crashes(ds, tmp_path):
    """LATENT BUG (#1, HIGH severity) -- file_mgmt/cookfile.py:278.

    `upgrade_cookfile`'s pre-v1.5.0 `graph_data` conversion branch builds an
    ad-hoc `probe_info = {"probe_settings": {...}}` dict and calls
    `default_probe_config(probe_info)`. `default_probe_config()`
    (common/defaults.py:315) unconditionally reads
    `settings["history_page"]["probe_config"]` -- a key the ad-hoc dict never
    has -- raising `KeyError: 'history_page'`. Since `json_types` processes
    `graph_data` before `graph_labels`/`events`/`comments`/`assets`, this
    crash happens on EVERY real pre-v1.5.0 `.pifire` file, before any of
    those later members are converted: there is currently no way to upgrade
    (or repair, which calls the same code) the oldest cookfile schema
    version `upgrade_cookfile` claims to support."""
    path = str(tmp_path / "old_unpatched.pifire")
    _write_old_format_pifire(path)

    with pytest.raises(KeyError, match="history_page"):
        upgrade_cookfile(path)


def test_upgrade_cookfile_pre_1_5_0_full_conversion_with_working_probe_config(ds, tmp_path, monkeypatch):
    """Exercises the INTENDED pre-v1.5.0 -> current upgrade behavior end to
    end, with `default_probe_config` patched to a working stub (see
    `_stub_probe_config`) so the real bug (#1, pinned separately above)
    doesn't block reaching the rest of the conversion logic: raw_data
    reconstruction from flat graph_data (cookfile.py:210-229), the
    graph_labels flat->nested conversion (cookfile.py:249-261), the
    comments `assets` key backfill (cookfile.py:240-241), and the assets
    `{}` -> `[]` normalization (cookfile.py:245). Asserts real transformed
    values, not just that the call succeeded."""
    version = _current_version(ds)
    monkeypatch.setattr(cookfile_mod, "default_probe_config", _stub_probe_config)

    path = str(tmp_path / "old_patched.pifire")
    _write_old_format_pifire(path, comments=[{"id": "c1", "text": "hi"}], assets={})

    struct, status = upgrade_cookfile(path)

    assert status == "OK"

    # metadata bumped to the current cookfile version.
    assert struct["metadata"]["version"] == version

    # raw_data reconstructed from the flat graph_data (cookfile.py:216-227).
    assert struct["raw_data"] == [
        {
            "T": 1000,
            "P": {"grill1": 100},
            "PSP": 225,
            "F": {"probe1": 90, "probe2": 80},
            "NT": {"grill1": 225, "probe1": 165, "probe2": 165},
            "AUX": {},
        },
        {
            "T": 2000,
            "P": {"grill1": 110},
            "PSP": 225,
            "F": {"probe1": 95, "probe2": 85},
            "NT": {"grill1": 225, "probe1": 165, "probe2": 165},
            "AUX": {},
        },
    ]

    # graph_labels flat -> nested (cookfile.py:249-261).
    assert struct["graph_labels"] == {
        "primarysp": {"grill1": "Grill Set Point"},
        "probes": {"grill1": "Grill", "probe1": "Probe 1", "probe2": "Probe 2"},
        "targets": {"grill1": "Grill Target", "probe1": "Probe 1 Target", "probe2": "Probe 2 Target"},
    }

    # graph_data rebuilt via prepare_chartdata() using the stubbed probe_config.
    pm = struct["graph_data"]["probe_mapper"]
    assert struct["graph_data"]["time_labels"] == [1000, 2000]
    grill_series = struct["graph_data"]["chart_data"][pm["probes"]["grill1"]]["data"]
    assert [p["y"] for p in grill_series] == [100, 110]

    # comments backfilled with an "assets" key (cookfile.py:240-241).
    assert struct["comments"] == [{"id": "c1", "text": "hi", "assets": []}]

    # assets normalized from {} to [] (cookfile.py:245).
    assert struct["assets"] == []


# ---------------------------------------------------------------------------
# create_cookfile
# ---------------------------------------------------------------------------


def _default_probe_labels(settings):
    primary = next(p["label"] for p in settings["probe_settings"]["probe_map"]["probe_info"] if p["type"] == "Primary")
    food = [p["label"] for p in settings["probe_settings"]["probe_map"]["probe_info"] if p["type"] == "Food"]
    return primary, food


def _seed_history_row(primary_label, food_labels, primary_val, food_val):
    write_history(
        {
            "probe_history": {
                "primary": {primary_label: primary_val},
                "food": {label: food_val for label in food_labels},
                "aux": {},
            },
            "primary_setpoint": 225,
            "notify_targets": {primary_label: 225, **{label: 165 for label in food_labels}},
        }
    )


def test_create_cookfile_writes_pifire_archive_with_seeded_history_and_metrics(ds, isolated_history_folder):
    """End-to-end: seed real history + metrics rows in the datastore, call
    `create_cookfile()`, and read the produced `.pifire` archive straight off
    disk -- asserting the actual seeded values round-trip through
    prepare_chartdata/process_metrics into the written JSON members, and
    that the datastore is flushed afterward (create_cookfile's documented
    "history/metrics purged after stop" behavior)."""
    settings = cookfile_mod.read_settings()
    primary_label, food_labels = _default_probe_labels(settings)
    _seed_history_row(primary_label, food_labels, 100, 90)
    _seed_history_row(primary_label, food_labels, 110, 95)

    write_metrics(dict(default_metrics(), id=0, mode="Smoke", augerontime=120), new_metric=True)
    write_metrics(dict(default_metrics(), id=1, mode="Stop", augerontime=30), new_metric=True)

    create_cookfile()

    pifire_files = list(pathlib.Path(isolated_history_folder).glob("*.pifire"))
    assert len(pifire_files) == 1

    with zipfile.ZipFile(pifire_files[0]) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        graph_data = json.loads(archive.read("graph_data.json"))
        raw_data = json.loads(archive.read("raw_data.json"))
        events = json.loads(archive.read("events.json"))
        comments = json.loads(archive.read("comments.json"))
        assets = json.loads(archive.read("assets.json"))

    assert metadata["version"] == settings["versions"]["cookfile"]
    assert metadata["title"].endswith("-CookFile")
    assert metadata["starttime"] == graph_data["time_labels"][0]
    assert metadata["endtime"] == graph_data["time_labels"][-1]

    assert len(raw_data) == 2
    assert raw_data[0]["P"][primary_label] == 100
    assert raw_data[1]["P"][primary_label] == 110

    assert len(events) == 2
    assert events[0]["mode"] == "Smoke"
    assert events[0]["augerontime_c"] == "120 s"
    assert events[1]["mode"] == "Stop"

    assert comments == []
    assert assets == []

    # Datastore purged after the cook file is written.
    assert read_history() == []
    assert read_metrics(all=True) == []


def test_create_cookfile_title_collision_appends_numeric_suffix(ds, isolated_history_folder, monkeypatch):
    """Two `create_cookfile()` calls in the same clock-minute (frozen via
    `_FrozenDateTime`, mirroring test_recipes.py's collision test) must NOT
    silently overwrite each other -- the while-loop at cookfile.py:130-136
    should append `-1` to the second file's name and leave the first file's
    bytes untouched."""
    monkeypatch.setattr(cookfile_mod.datetime, "datetime", _FrozenDateTime)
    settings = cookfile_mod.read_settings()
    primary_label, food_labels = _default_probe_labels(settings)

    _seed_history_row(primary_label, food_labels, 100, 90)
    create_cookfile()

    first_files = list(pathlib.Path(isolated_history_folder).glob("*.pifire"))
    assert len(first_files) == 1
    first_path = first_files[0]
    first_bytes = first_path.read_bytes()

    _seed_history_row(primary_label, food_labels, 200, 190)
    create_cookfile()

    all_files = sorted(pathlib.Path(isolated_history_folder).glob("*.pifire"))
    assert len(all_files) == 2
    second_path = next(p for p in all_files if p != first_path)
    assert second_path.name == first_path.name.replace(".pifire", "-1.pifire")
    assert first_path.read_bytes() == first_bytes
