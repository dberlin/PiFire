"""Unit coverage for the cookfile read, upgrade, chart, and creation pipeline.

Cookfiles carry the seven historical JSON members plus the learning diagnostics
member required from new writers and optional when reading legacy archives.

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

Three latent bugs, all now FIXED. Each test below asserts the corrected
behavior:

1. HIGH -- `upgrade_cookfile`'s pre-v1.5.0 `graph_data` conversion branch
   (file_mgmt/cookfile.py:266-286) crashed with `KeyError: 'history_page'`
   because it called `default_probe_config()` (common/defaults.py:315) with
   an ad-hoc dict that only had a `probe_settings` key. FIX: the ad-hoc dict
   now also carries `"history_page": {"probe_config": {}}` -- the other key
   `default_probe_config()` unconditionally reads (to check for reusable
   pre-existing per-probe config) -- so it takes the "build fresh defaults"
   branch for all three probes instead of raising.
2. HIGH -- `read_cookfile` (file_mgmt/cookfile.py:183) crashed with
   `KeyError: 'version'` on a corrupt/non-zip `.pifire` file, instead of
   returning the `status` error string `read_json_file_data` already
   produced. FIX: the `if status != "OK": break` guard now runs immediately
   after each read, before the metadata version-check block touches the
   (possibly empty, on error) dict -- so a corrupt file now returns the
   documented `("Error: ...", ...)` status shape instead of crashing.
3. LOW/MEDIUM -- `prepare_chartdata`'s history-population loop
   (file_mgmt/cookfile.py:423-427) did `for key in probe_mapper["primarysp"]:
   ... ; break` -- it only ever appended the primary-setpoint series for the
   FIRST "Primary"-type probe, per index. FIX: the `break` was removed, so
   every "Primary"-type probe's setpoint series is now filled from the same
   shared `history["PSP"]` value at each index.
"""

import json
import logging
import math
import pathlib
import zipfile

import pytest

from common.control_trace import ControlTraceRecord, ControllerType, SessionPayload, TraceEventKind, TraceSetting
from common.cook_diagnostics import ControllerLearningReport, collect_cook_learning_diagnostics
from common.model_evidence import ConfidenceDecisionEvidence, EvidenceKind, ModelEvidenceRecord
import file_mgmt.cookfile as cookfile_mod
from common.common import epoch_to_time, process_metrics
from common.persistence.control_trace import append_control_trace
from common.persistence.history import append_metric, read_all_metrics, read_history, update_metrics, write_history
from common.persistence.model_evidence import append_model_evidence
from common.defaults import default_metrics
from file_mgmt.cookfile import create_cookfile, prepare_chartdata, read_cookfile, upgrade_cookfile
from file_mgmt.downsample import max_interpolation_error


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


def test_prepare_chartdata_empty_history_returns_empty_series(ds):
    """With no rows in the datastore and `history=None` (the default),
    `read_history()` comes back `[]`, taking the `list_length == 0` branch.

    This used to fabricate a single now()-timestamped point of value 0 per
    series. It no longer does, and that is the point of this test: a 0 stamped
    at "now" is a temperature reading that was never taken, and a consumer has
    no way to tell it apart from a real 0. It also made `data` two different
    element types -- {"x", "y"} objects on the populated branch, bare ints
    here -- which Chart.js happens to tolerate and a typed client does not.
    Empty lists say "no data" honestly.
    """
    result = prepare_chartdata(_PROBE_CONFIG, num_items=10, reduce=True, data_points=60)

    assert result["time_labels"] == []
    probe_mapper = result["probe_mapper"]
    assert result["chart_data"][probe_mapper["probes"]["grill1"]]["data"] == []
    assert result["chart_data"][probe_mapper["targets"]["grill1"]]["data"] == []
    assert result["chart_data"][probe_mapper["primarysp"]["grill1"]]["data"] == []
    assert result["chart_data"][probe_mapper["probes"]["probe1"]]["data"] == []
    # The datasets themselves still exist -- only their points are empty, so a
    # chart keeps its legend/colors and simply draws nothing.
    assert result["chart_data"][probe_mapper["probes"]["grill1"]]["label"] == "Grill"
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


def test_prepare_chartdata_reduce_true_downsamples_by_fidelity():
    """`reduce=True` with the window bigger than `data_points` (now the
    "downsample above this many samples" threshold, not a target point
    count) selects points via `file_mgmt.downsample.select_indices`
    (LTTB against a fidelity target) instead of the old every-Nth
    `step = int(num_items / data_points)` decimation. Every-Nth could step
    straight over a short dip; LTTB keeps whatever it takes to stay within
    tolerance, so a narrow event inside a big flat window survives."""
    n = 20000
    dip_at, dip_len = 10000, 40
    grill = [200.0] * n
    for i in range(dip_at, dip_at + dip_len):
        grill[i] = 150.0
    history = {
        "T": list(range(n)),
        "PSP": [225] * n,
        "P": {"grill1": grill},
        "F": {"probe1": [90.0] * n},
        "NT": {"grill1": [225] * n, "probe1": [165] * n},
    }

    result = prepare_chartdata(_PROBE_CONFIG, num_items=n, reduce=True, data_points=1000, history=history)

    pm = result["probe_mapper"]
    grill_values = [pt["y"] for pt in result["chart_data"][pm["probes"]["grill1"]]["data"]]
    assert len(grill_values) < n  # fewer points than raw samples -- downsampled
    assert grill_values[0] == 200.0 and grill_values[-1] == 200.0  # endpoints always kept
    assert 150.0 in grill_values  # the dip survives LTTB where every-Nth could miss it


def test_prepare_chartdata_reduce_true_preserves_psp_step_change():
    """CRITICAL regression: `prepare_chartdata`'s `series` fed to
    `select_indices` (cookfile.py:427-428, pre-fix) was built from only
    `history["P"]` and `history["F"]` -- but the single `window` computed
    from that fidelity check is then used to plot `history["NT"]` (targets)
    and `history["PSP"]` (primary setpoint) too. NT and PSP are STEP
    functions: dropping the sample at a step makes the chart draw a ramp
    that never happened -- e.g. a setpoint bumped from 200 to 240 mid-cook
    would render as a gradual climb instead of the instant change it was.

    This pins the fix at the `prepare_chartdata` level: a >10000-sample
    window (the downsample gate) holding a single PSP step change must
    still resolve that step within `tolerance` once selected, exactly like
    a probe series would."""
    n = 20000
    step_idx = 12345
    psp = [200.0] * step_idx + [240.0] * (n - step_idx)
    history = {
        "T": list(range(n)),
        "PSP": psp,
        "P": {"grill1": [200.0] * n},  # flat -- NOT the most dynamic series
        "F": {"probe1": [90.0] * n},
        "NT": {"grill1": [225.0] * n, "probe1": [165.0] * n},
    }

    result = prepare_chartdata(_PROBE_CONFIG, num_items=n, reduce=True, data_points=10000, history=history)

    pm = result["probe_mapper"]
    psp_points = result["chart_data"][pm["primarysp"]["grill1"]]["data"]
    selected = [pt["x"] for pt in psp_points]  # x == raw index here (T == range(n))

    # The step itself must be resolvable from the selected samples alone --
    # not smeared into a multi-thousand-sample ramp by the drawn line.
    assert max_interpolation_error(psp, selected) <= 2.0
    # And the jump is still an instant 200 -> 240 in the returned series,
    # not something in between.
    values_at_selected = [pt["y"] for pt in psp_points]
    assert set(values_at_selected) <= {200.0, 240.0}


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


def test_prepare_chartdata_multiple_primary_probes_all_get_setpoint_data():
    """FIXED (was latent bug #3) -- file_mgmt/cookfile.py:423-427.

    `for key in probe_mapper["primarysp"]: chart_data[...].append(...)`
    (the trailing `break` was removed) now appends the shared
    `history["PSP"]` value to EVERY "Primary"-type probe's series, per
    history index -- not just the first. With two "Primary" probes in
    probe_config (allowed by the schema, if atypical), both probes' setpoint
    series are populated identically from the single shared PSP value."""
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
    # Fixed: grill2 is also "Primary" and now receives the same shared PSP
    # series as grill1, instead of staying permanently empty.
    assert result["chart_data"][pm["primarysp"]["grill2"]]["data"] == [
        {"x": 1000, "y": 225},
        {"x": 2000, "y": 230},
    ]


def test_prepare_chartdata_reduce_true_carries_null_probe_readings_through():
    """CRITICAL regression: an unplugged / open-circuit / badly-read probe
    stores Python `None` (`probes/base.py:251-253` returns `(None, 0)` and
    the Kalman stage at `:374` deliberately passes it through), which
    round-trips as JSON `null` through `write_history`/`unpack_history` into
    `history["P"][label]`. The old every-Nth decimation only INDEXED, so a
    `None` was carried straight into `{"x": ts, "y": null}` -- rendered as a
    gap by Chart.js (`spanGaps: False`) and by uPlot (which
    `historyAdapter.ts` documents and pads for, typing `HistoryPoint.y` as
    `number | null`). The fidelity selection replaced that with value
    ARITHMETIC and raised `TypeError` on the first `None`, i.e. a 500 on both
    `GET /api/history/chart` and the legacy `POST /history/refresh` for any
    window above the gate.

    Pinned here end to end, above the 10000-sample gate so the reduce path is
    genuinely entered, with both shapes: an isolated dropped read and a run
    long enough to swallow whole LTTB buckets.
    """
    n = 12000
    grill = [200.0 + 20.0 * math.sin(2 * math.pi * i / 3000.0) for i in range(n)]
    food = [90.0] * n
    food[4321] = None  # one bad read
    for i in range(7000, 9000):  # probe pulled out mid-cook
        food[i] = None
    history = {
        "T": [1700000000000 + i * 3000 for i in range(n)],
        "PSP": [225.0] * n,
        "P": {"grill1": grill},
        "F": {"probe1": food},
        "NT": {"grill1": [225.0] * n, "probe1": [165.0] * n},
    }

    result = prepare_chartdata(_PROBE_CONFIG, num_items=n, reduce=True, data_points=10000, history=history)

    pm = result["probe_mapper"]
    food_points = result["chart_data"][pm["probes"]["probe1"]]["data"]
    index_of = {ts: i for i, ts in enumerate(history["T"])}

    # Every emitted point is the raw sample verbatim -- so a dropped read
    # arrives as `null`, never as a fabricated 0 F reading (the failure mode
    # commit 1d8a67a9 removed from the empty-history branch) and never
    # silently dropped (which would desynchronise it from `time_labels`).
    assert food_points
    for point in food_points:
        assert point["y"] == food[index_of[point["x"]]] or (point["y"] is None and food[index_of[point["x"]]] is None)
    assert any(point["y"] is None for point in food_points)
    assert 0 not in [point["y"] for point in food_points]

    # The grill series is unaffected by its neighbour's dropout, and every
    # dataset stays in lockstep with time_labels.
    grill_points = result["chart_data"][pm["probes"]["grill1"]]["data"]
    assert len(grill_points) == len(food_points) == len(result["time_labels"])
    assert all(point["y"] is not None for point in grill_points)


def _all_y_values(result):
    return [point["y"] for series in result["chart_data"] for point in series["data"]]


def test_prepare_chartdata_skips_primary_history_key_absent_from_probe_config(caplog):
    """CRITICAL regression: history rows are DURABLE and name whatever probes
    were configured when they were written, while `probe_mapper` is built from
    the CURRENT `probe_config`. Rename a probe, delete one, or swap a probe
    module and every older row names a key the mapper does not have, which
    raised `KeyError` at `chart_data[probe_mapper["probes"][key]]` -- so
    `GET /api/history/chart` returned a 500 and the History page was dead for
    as long as those rows stayed in the store, with nothing to recover it.

    A row naming a probe the configuration no longer has is missing data, not
    a server error: the unresolvable key is dropped and the series that do
    resolve still render.
    """
    history = {
        "T": [1000, 2000],
        "PSP": [225, 226],
        "P": {"grill1": [100, 110], "OldGrill": [500, 510]},
        "F": {"probe1": [90, 95]},
        "NT": {"grill1": [225, 225], "probe1": [165, 165]},
    }

    with caplog.at_level(logging.WARNING, logger="events"):
        result = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history)

    pm = result["probe_mapper"]
    # The unknown key has no dataset to land in and nothing anywhere in the
    # output carries its readings -- it is absent, not misfiled onto a
    # neighbouring probe's series.
    assert "OldGrill" not in pm["probes"]
    assert 500 not in _all_y_values(result)
    assert 510 not in _all_y_values(result)
    # Every key that does resolve is untouched, points and all.
    assert [pt["y"] for pt in result["chart_data"][pm["probes"]["grill1"]]["data"]] == [100, 110]
    assert [pt["y"] for pt in result["chart_data"][pm["probes"]["probe1"]]["data"]] == [90, 95]
    assert result["time_labels"] == [1000, 2000]


def test_prepare_chartdata_skips_food_history_key_absent_from_probe_config():
    """The same durable-row mismatch on the `history["F"]` loop, which reads
    the same `probe_mapper["probes"]` map."""
    history = {
        "T": [1000, 2000],
        "PSP": [225, 226],
        "P": {"grill1": [100, 110]},
        "F": {"probe1": [90, 95], "RetiredFood": [700, 710]},
        "NT": {"grill1": [225, 225], "probe1": [165, 165]},
    }

    result = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history)

    pm = result["probe_mapper"]
    assert "RetiredFood" not in pm["probes"]
    assert 700 not in _all_y_values(result)
    assert [pt["y"] for pt in result["chart_data"][pm["probes"]["probe1"]]["data"]] == [90, 95]
    assert [pt["y"] for pt in result["chart_data"][pm["probes"]["grill1"]]["data"]] == [100, 110]


def test_prepare_chartdata_skips_target_history_key_absent_from_probe_config():
    """`history["NT"]` resolves against `probe_mapper["targets"]`, a separate
    map -- a stale notify-target key must drop the same way a probe key does."""
    history = {
        "T": [1000, 2000],
        "PSP": [225, 226],
        "P": {"grill1": [100, 110]},
        "F": {"probe1": [90, 95]},
        "NT": {"grill1": [225, 225], "probe1": [165, 165], "OldGrill": [400, 400]},
    }

    result = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history)

    pm = result["probe_mapper"]
    assert "OldGrill" not in pm["targets"]
    assert 400 not in _all_y_values(result)
    assert [pt["y"] for pt in result["chart_data"][pm["targets"]["grill1"]]["data"]] == [225, 225]
    assert [pt["y"] for pt in result["chart_data"][pm["targets"]["probe1"]]["data"]] == [165, 165]


def test_prepare_chartdata_all_history_keys_unknown_returns_empty_probe_series():
    """A wholesale probe swap leaves rows where NOTHING resolves. That must
    still return a payload -- empty per-probe series, the legend intact -- and
    never raise. The primary-setpoint series is keyed off the mapper rather
    than off a row key, so it is unaffected and still fills."""
    history = {
        "T": [1000, 2000],
        "PSP": [225, 226],
        "P": {"PitProbe": [100, 110]},
        "F": {"PinkProbe": [90, 95]},
        "NT": {"PitProbe": [225, 225], "PinkProbe": [165, 165]},
    }

    result = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history)

    pm = result["probe_mapper"]
    assert result["chart_data"][pm["probes"]["grill1"]]["data"] == []
    assert result["chart_data"][pm["probes"]["probe1"]]["data"] == []
    assert result["chart_data"][pm["targets"]["grill1"]]["data"] == []
    assert result["chart_data"][pm["targets"]["probe1"]]["data"] == []
    # The datasets still exist with their labels, so the chart keeps its
    # legend and simply draws nothing for those probes.
    assert result["chart_data"][pm["probes"]["grill1"]]["label"] == "Grill"
    assert [pt["y"] for pt in result["chart_data"][pm["primarysp"]["grill1"]]["data"]] == [225, 226]
    assert result["time_labels"] == [1000, 2000]


def test_prepare_chartdata_logs_every_dropped_key_once_per_call(caplog):
    """A silently-missing series looks like a bug in the chart, so the drop is
    operator-visible: one WARNING per call naming every key dropped, not one
    per row of a window that can be hundreds of rows wide."""
    history = {
        "T": [1000, 2000, 3000],
        "PSP": [225, 226, 227],
        "P": {"grill1": [100, 110, 120], "OldGrill": [500, 510, 520]},
        "F": {"probe1": [90, 95, 100], "RetiredFood": [700, 710, 720]},
        "NT": {"grill1": [225, 225, 225], "StaleTarget": [400, 400, 400]},
    }

    with caplog.at_level(logging.WARNING, logger="events"):
        prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history)

    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    for key in ("OldGrill", "RetiredFood", "StaleTarget"):
        assert key in message


def test_prepare_chartdata_history_without_targets_or_setpoint_still_renders():
    """`reduce=True` already read NT/PSP through `.get`, but the row loop read
    them directly -- so a payload lacking either rendered above the fidelity
    gate and raised below it. Both sources are optional on both paths."""
    history = {"T": [1000, 2000], "P": {"grill1": [100, 110]}, "F": {"probe1": [90, 95]}}

    result = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history)

    pm = result["probe_mapper"]
    assert [pt["y"] for pt in result["chart_data"][pm["probes"]["grill1"]]["data"]] == [100, 110]
    assert result["chart_data"][pm["targets"]["grill1"]]["data"] == []
    assert result["chart_data"][pm["primarysp"]["grill1"]]["data"] == []
    assert result["time_labels"] == [1000, 2000]


def test_prepare_chartdata_fully_resolving_payload_is_unchanged_by_the_guard():
    """The guard must cost a payload whose keys all resolve nothing at all:
    same datasets, same points, same order, same time_labels."""
    history = {
        "T": [1000, 2000, 3000],
        "PSP": [225, 226, 227],
        "P": {"grill1": [100, 110, 120]},
        "F": {"probe1": [90, 95, 100]},
        "NT": {"grill1": [225, 225, 225], "probe1": [165, 165, 165]},
    }

    result = prepare_chartdata(_PROBE_CONFIG, num_items=0, reduce=False, data_points=0, history=history)

    pm = result["probe_mapper"]
    assert pm == {
        "probes": {"grill1": 0, "probe1": 3},
        "targets": {"grill1": 1, "probe1": 4},
        "primarysp": {"grill1": 2},
    }
    assert result["time_labels"] == [1000, 2000, 3000]
    assert [series["data"] for series in result["chart_data"]] == [
        [{"x": 1000, "y": 100}, {"x": 2000, "y": 110}, {"x": 3000, "y": 120}],
        [{"x": 1000, "y": 225}, {"x": 2000, "y": 225}, {"x": 3000, "y": 225}],
        [{"x": 1000, "y": 225}, {"x": 2000, "y": 226}, {"x": 3000, "y": 227}],
        [{"x": 1000, "y": 90}, {"x": 2000, "y": 95}, {"x": 3000, "y": 100}],
        [{"x": 1000, "y": 165}, {"x": 2000, "y": 165}, {"x": 3000, "y": 165}],
    ]


def test_prepare_chartdata_reduce_path_ignores_the_mapper_entirely():
    """The `series` list handed to `select_indices` iterates the history's own
    values and never consults `probe_mapper`, so an unresolvable key reaches
    the fidelity arithmetic unchanged: the selected window is identical
    whether or not the current config still knows that probe."""
    n = 12000
    grill = [200.0 + 20.0 * math.sin(2 * math.pi * i / 3000.0) for i in range(n)]
    stale = [50.0 + 40.0 * math.sin(2 * math.pi * i / 700.0) for i in range(n)]
    base = {
        "T": [1700000000000 + i * 3000 for i in range(n)],
        "PSP": [225.0] * n,
        "F": {"probe1": [90.0] * n},
        "NT": {"grill1": [225.0] * n, "probe1": [165.0] * n},
    }

    with_stale = prepare_chartdata(
        _PROBE_CONFIG,
        num_items=n,
        reduce=True,
        data_points=10000,
        history={**base, "P": {"grill1": list(grill), "OldGrill": stale}},
    )
    stale_as_known = prepare_chartdata(
        {**_PROBE_CONFIG, "OldGrill": _PROBE_CONFIG["probe1"]},
        num_items=n,
        reduce=True,
        data_points=10000,
        history={**base, "P": {"grill1": list(grill), "OldGrill": stale}},
    )

    assert with_stale["time_labels"] == stale_as_known["time_labels"]
    grill_slot = with_stale["probe_mapper"]["probes"]["grill1"]
    known_slot = stale_as_known["probe_mapper"]["probes"]["grill1"]
    assert with_stale["chart_data"][grill_slot]["data"] == stale_as_known["chart_data"][known_slot]["data"]


# ---------------------------------------------------------------------------
# read_cookfile
# ---------------------------------------------------------------------------


def test_read_cookfile_ok_returns_full_struct_matching_file_contents(ds, tmp_path):
    """A current legacy archive without diagnostics remains readable.

    Its seven required members round-trip unchanged and the optional
    diagnostics value is explicitly absent.
    """
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
    assert struct["learning_diagnostics"] is None


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


def _write_versioned_cookfile(path, version):
    _write_zip(
        path,
        {
            "metadata.json": _base_metadata(version),
            "graph_data.json": {},
            "raw_data.json": [],
            "graph_labels.json": {},
            "events.json": [],
            "comments.json": [],
            "assets.json": [],
        },
    )


@pytest.mark.parametrize("version", ["2.4.0", "2.0.0", "1.5.0", "1.6.0", "1.5.1", "10.0.0"])
def test_read_cookfile_accepts_any_version_at_or_above_the_minimum(ds, tmp_path, version):
    """The version gate compared major/minor/patch INDEPENDENTLY
    (`fileversion[0] >= min[0] and fileversion[1] >= min[1] and ...`), which
    is not how semantic versions order. Against the shipped minimum of 1.5.0
    that made "2.4.0" read as older than "1.5.0" (2 >= 1 passes, but 4 >= 5
    fails), so a file written by a NEWER PiFire was reported as an old-format
    file and routed to the repair/upgrade prompt -- which would then rewrite
    it downwards. Every version here is >= 1.5.0 and must load."""
    assert _current_version(ds) == "1.5.0", "this test's expectations are keyed to the shipped minimum"
    path = str(tmp_path / "newer.pifire")
    _write_versioned_cookfile(path, version)

    struct, status = read_cookfile(path)

    assert status == "OK", f"{version} should not be treated as older than 1.5.0"
    assert struct["metadata"]["version"] == version


@pytest.mark.parametrize("version", ["1.4.9", "1.0.0", "0.9.9", "1.4.0"])
def test_read_cookfile_still_rejects_versions_below_the_minimum(ds, tmp_path, version):
    """The counterpart: genuinely older files must still take the
    upgrade-prompt path. `0.9.9` is the case the independent-component
    comparison got right only by accident (9 >= 5 passes, 0 >= 1 fails)."""
    path = str(tmp_path / "older.pifire")
    _write_versioned_cookfile(path, version)

    _struct, status = read_cookfile(path)

    assert status.startswith("WARNING: Older cookfile version format!")


def test_read_cookfile_corrupt_zip_returns_error_status_instead_of_crashing(ds, tmp_path):
    """FIXED (was latent bug #2) -- file_mgmt/cookfile.py:183.

    `read_json_file_data()` (file_mgmt/common.py) already handles a corrupt
    (non-zip) file gracefully, returning `({}, "Error: ...")`. Previously,
    `read_cookfile`'s metadata-version-check block ran UNCONDITIONALLY right
    after that read, before the `status != "OK"` guard, so indexing
    `["version"]` on the empty `{}` dict raised `KeyError: 'version'`. FIX:
    the `if status != "OK": break` guard now runs immediately after each
    read, before the version-check code touches `cook_file_struct`, so a
    corrupt file returns the documented error-status shape instead. Every
    caller in blueprints/cookfile/routes.py and blueprints/history/routes.py
    branches on `status != "OK"` to render a friendly `cferror.html` -- for a
    genuinely corrupt `.pifire` (the exact case this is meant to handle),
    the route now gets the status string it expects instead of a 500."""
    path = str(tmp_path / "corrupt.pifire")
    with open(path, "wb") as f:
        f.write(b"this is not a zip file")

    struct, status = read_cookfile(path)

    assert status != "OK"
    assert status.startswith("Error")
    # Only "metadata" was attempted before the read failure broke the loop.
    assert struct == {"metadata": {}}


def test_read_cookfile_rejects_malformed_present_learning_diagnostics(ds, tmp_path):
    """Present malformed diagnostics are an error, never legacy absence."""
    path = str(tmp_path / "malformed-diagnostics.pifire")
    _write_versioned_cookfile(path, _current_version(ds))
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("learning_diagnostics.json", b"{not-json")

    struct, status = read_cookfile(path)

    assert status == "Error: JSON Decoding Error."
    assert struct["learning_diagnostics"] is None


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


def test_upgrade_cookfile_pre_1_5_0_graph_data_conversion_succeeds_with_real_probe_config(ds, tmp_path):
    """FIXED (was latent bug #1) -- file_mgmt/cookfile.py:278.

    `upgrade_cookfile`'s pre-v1.5.0 `graph_data` conversion branch builds an
    ad-hoc `probe_info` dict and calls `default_probe_config(probe_info)`.
    `default_probe_config()` (common/defaults.py:315) unconditionally reads
    `settings["history_page"]["probe_config"]` to check for pre-existing
    per-probe config to reuse -- a key the ad-hoc dict previously never had,
    raising `KeyError: 'history_page'` on every real pre-v1.5.0 `.pifire`
    file. FIX: the ad-hoc dict now also carries
    `"history_page": {"probe_config": {}}`, so `default_probe_config()`
    takes its normal "no existing entry -> build fresh defaults" branch for
    each probe instead of crashing.

    This test uses the REAL, unpatched `default_probe_config()` (unlike
    `test_upgrade_cookfile_pre_1_5_0_full_conversion_with_working_probe_config`
    below, which uses a stub to characterize the surrounding logic in
    isolation) -- it is the end-to-end proof that the oldest supported
    cookfile schema version can now actually be upgraded, and that the
    output is a genuinely valid modern `graph_data`/probe_mapper structure:
    all three probes present, only the "Primary" probe (grill1) has a
    primarysp chart slot, and the real history values flow through
    correctly."""
    path = str(tmp_path / "old_unpatched.pifire")
    _write_old_format_pifire(path)

    struct, status = upgrade_cookfile(path)

    assert status == "OK"

    graph_data = struct["graph_data"]
    pm = graph_data["probe_mapper"]
    assert set(pm["probes"]) == {"grill1", "probe1", "probe2"}
    assert set(pm["targets"]) == {"grill1", "probe1", "probe2"}
    # Only grill1 is "Primary" -- it alone gets a primarysp chart slot.
    assert set(pm["primarysp"]) == {"grill1"}

    grill_series = graph_data["chart_data"][pm["probes"]["grill1"]]["data"]
    assert [p["y"] for p in grill_series] == [100, 110]
    grill_sp_series = graph_data["chart_data"][pm["primarysp"]["grill1"]]["data"]
    assert [p["y"] for p in grill_sp_series] == [225, 225]
    assert graph_data["time_labels"] == [1000, 2000]

    # The rest of the (already-correct) conversion still runs after this
    # branch: metadata bumped to current, raw_data reconstructed.
    assert struct["metadata"]["version"] == _current_version(ds)
    assert struct["raw_data"][0]["P"]["grill1"] == 100


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


def _no_learning_report(_controller: str) -> None:
    return None


def _diagnostic_session(cook_id: str) -> ControlTraceRecord:
    return ControlTraceRecord(
        ts_ms=100,
        session_id="session-pid-sp",
        cook_id=cook_id,
        controller=ControllerType.PID_SP,
        event_kind=TraceEventKind.SESSION,
        payload=SessionPayload(
            controller=ControllerType.PID_SP,
            controller_config=(TraceSetting(key="policy", value="pid_sp"),),
            temperature_unit="F",
            control_period_seconds=2.0,
            model_revision=None,
            model_provenance=None,
            pulse_slot_seconds=2.0,
            pulse_frame_seconds=20.0,
            fan_authority=False,
            fan_pwm_capable=True,
            fan_min_duty=0.0,
            fan_max_duty=1.0,
            setpoint=225.0,
            ambient_temperature=70.0,
            software_version="1.2.3",
            build_version="test",
        ),
    )


def _diagnostic_evidence(cook_id: str) -> ModelEvidenceRecord:
    return ModelEvidenceRecord(
        evidence_id="evidence-confidence",
        kind=EvidenceKind.CONFIDENCE_DECISION,
        session_id="session-pid-sp",
        cook_id=cook_id,
        timestamp_ms=200,
        role_generation=1,
        model_digest=None,
        provenance_digest=None,
        payload=ConfidenceDecisionEvidence(decision_id="decision-confidence", blocked=False),
    )


def _diagnostic_provider(controller: str) -> ControllerLearningReport:
    return ControllerLearningReport(
        controller=controller,
        schema_version=1,
        revision=f"{controller}-revision",
        report={"sample_count": 4},
    )


def _track_flush_history(monkeypatch):
    real_flush_history = cookfile_mod.flush_history
    calls = []

    def tracked_flush_history():
        calls.append(None)
        real_flush_history()

    monkeypatch.setattr(cookfile_mod, "flush_history", tracked_flush_history)
    return calls


def test_new_cookfile_contains_complete_learning_diagnostics(
    ds,
    isolated_history_folder,
    monkeypatch,
):
    """A new archive carries validated diagnostics captured before the final flush."""
    settings = cookfile_mod.read_settings()
    primary_label, food_labels = _default_probe_labels(settings)
    _seed_history_row(primary_label, food_labels, 100, 90)
    cook_id = "cook-diagnostics-7"
    append_metric(dict(default_metrics(), mode="Smoke", augerontime=120))
    update_metrics({"id": cook_id})
    append_control_trace([_diagnostic_session(cook_id)])
    append_model_evidence([_diagnostic_evidence(cook_id)])
    flush_calls = _track_flush_history(monkeypatch)

    create_cookfile(learning_report_provider=_diagnostic_provider)

    pifire_files = list(pathlib.Path(isolated_history_folder).glob("*.pifire"))
    assert len(pifire_files) == 1
    with zipfile.ZipFile(pifire_files[0]) as archive:
        required_members = {
            "metadata.json",
            "graph_data.json",
            "raw_data.json",
            "graph_labels.json",
            "events.json",
            "comments.json",
            "assets.json",
            "learning_diagnostics.json",
        }
        assert required_members <= set(archive.namelist())
        payload = json.loads(archive.read("learning_diagnostics.json"))
        assert json.loads(archive.read("raw_data.json"))[0]["P"][primary_label] == 100
        assert json.loads(archive.read("events.json"))[0]["id"] == cook_id
    reread, status = read_cookfile(pifire_files[0])
    assert status == "OK"
    assert reread["learning_diagnostics"] == payload

    assert payload["schema_version"] == 1
    assert payload["cook_id"] == cook_id
    assert payload["controllers"] == ["pid_sp"]
    assert payload["reports"] == [
        {
            "controller": "pid_sp",
            "schema_version": 1,
            "revision": "pid_sp-revision",
            "report": {"sample_count": 4},
        }
    ]
    assert payload["control_trace"]["records"]
    assert payload["model_evidence"]["records"]
    assert payload["capture_errors"] == []
    assert flush_calls == [None]
    assert read_history() == []
    assert read_all_metrics() == []


@pytest.mark.parametrize(
    "metric_ids",
    [(None,), ("cook-a", "cook-b")],
    ids=["missing", "mixed"],
)
def test_invalid_metrics_identity_still_writes_diagnostics_and_flushes(
    ds,
    isolated_history_folder,
    monkeypatch,
    metric_ids,
):
    """Missing or mixed metric identity is recorded, never guessed or fatal."""
    settings = cookfile_mod.read_settings()
    primary_label, food_labels = _default_probe_labels(settings)
    _seed_history_row(primary_label, food_labels, 100, 90)
    for metric_id in metric_ids:
        append_metric(dict(default_metrics(), mode="Smoke"))
        update_metrics({"id": metric_id})
    flush_calls = _track_flush_history(monkeypatch)

    create_cookfile(learning_report_provider=_diagnostic_provider)

    pifire_files = list(pathlib.Path(isolated_history_folder).glob("*.pifire"))
    assert len(pifire_files) == 1
    with zipfile.ZipFile(pifire_files[0]) as archive:
        assert {
            "metadata.json",
            "graph_data.json",
            "raw_data.json",
            "graph_labels.json",
            "events.json",
            "comments.json",
            "assets.json",
            "learning_diagnostics.json",
        } <= set(archive.namelist())
        assert json.loads(archive.read("raw_data.json"))[0]["P"][primary_label] == 100
        assert len(json.loads(archive.read("events.json"))) == len(metric_ids)
        payload = json.loads(archive.read("learning_diagnostics.json"))

    assert payload["cook_id"] is None
    assert payload["controllers"] == []
    assert payload["control_trace"]["records"] == []
    assert payload["model_evidence"]["records"] == []
    assert payload["capture_errors"] == [
        {
            "source": "collector",
            "code": "cook-identity-invalid",
            "detail": "cook_id must be a non-blank, whitespace-trimmed string",
        }
    ]
    assert flush_calls == [None]
    assert read_history() == []
    assert read_all_metrics() == []


@pytest.mark.parametrize(
    ("failure_mode", "expected_errors"),
    [
        (
            "trace",
            [
                {
                    "source": "control_trace",
                    "code": "control-trace-read-failed",
                    "detail": "trace unavailable",
                }
            ],
        ),
        (
            "evidence-report",
            [
                {
                    "source": "model_evidence",
                    "code": "model-evidence-read-failed",
                    "detail": "evidence unavailable",
                },
                {
                    "source": "report:pid_sp",
                    "code": "report-read-failed",
                    "detail": "report unavailable",
                },
            ],
        ),
    ],
)
def test_diagnostics_source_failure_still_writes_archive_before_one_flush(
    ds,
    isolated_history_folder,
    monkeypatch,
    failure_mode,
    expected_errors,
):
    """Contained source failures retain the archive and capture-before-flush order."""
    settings = cookfile_mod.read_settings()
    primary_label, food_labels = _default_probe_labels(settings)
    _seed_history_row(primary_label, food_labels, 100, 90)
    cook_id = "cook-failure-7"
    append_metric(dict(default_metrics(), mode="Smoke"))
    update_metrics({"id": cook_id})
    order = []
    real_flush_history = cookfile_mod.flush_history

    def tracked_flush_history():
        order.append("flush")
        real_flush_history()

    def trace_failure(_cook_id):
        raise RuntimeError("trace unavailable")

    def evidence_failure(*, cook_id):
        raise RuntimeError("evidence unavailable")

    def report_failure(_controller):
        raise RuntimeError("report unavailable")

    def successful_trace(_cook_id):
        return [_diagnostic_session(cook_id)]

    def successful_evidence(*, cook_id):
        return []

    def collect_with_injected_failures(captured_cook_id, report_provider, *, warn):
        order.append("capture")
        return collect_cook_learning_diagnostics(
            captured_cook_id,
            report_provider,
            read_trace=trace_failure if failure_mode == "trace" else successful_trace,
            read_evidence=evidence_failure if failure_mode == "evidence-report" else successful_evidence,
            clock_ms=lambda: 1_787_490_000_000,
            warn=warn,
        )

    monkeypatch.setattr(cookfile_mod, "flush_history", tracked_flush_history)
    monkeypatch.setattr(cookfile_mod, "collect_cook_learning_diagnostics", collect_with_injected_failures)
    provider = report_failure if failure_mode == "evidence-report" else _diagnostic_provider

    create_cookfile(learning_report_provider=provider)

    pifire_files = list(pathlib.Path(isolated_history_folder).glob("*.pifire"))
    assert len(pifire_files) == 1
    with zipfile.ZipFile(pifire_files[0]) as archive:
        assert {
            "metadata.json",
            "graph_data.json",
            "raw_data.json",
            "graph_labels.json",
            "events.json",
            "comments.json",
            "assets.json",
            "learning_diagnostics.json",
        } <= set(archive.namelist())
        assert json.loads(archive.read("raw_data.json"))[0]["P"][primary_label] == 100
        assert json.loads(archive.read("events.json"))[0]["id"] == cook_id
        payload = json.loads(archive.read("learning_diagnostics.json"))

    assert payload["cook_id"] == cook_id
    assert payload["capture_errors"] == expected_errors
    assert order == ["capture", "flush"]
    assert read_history() == []
    assert read_all_metrics() == []


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

    append_metric(dict(default_metrics(), id=0, mode="Smoke", augerontime=120))
    append_metric(dict(default_metrics(), id=1, mode="Stop", augerontime=30))

    create_cookfile(learning_report_provider=_no_learning_report)

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
    assert read_all_metrics() == []


# ---------------------------------------------------------------------------
# process_metrics: None starttime/endtime guard (LIVE crash regression)
#
# Real crash (control.py, verbatim):
#   File "common/common.py", line 456, in process_metrics
#       metrics_data[index]["starttime_c"] = epoch_to_time(starttime / 1000)
#   TypeError: unsupported operand type(s) for /: 'NoneType' and 'int'
#
# Root cause (fixed at the source in this same commit): SmokeMode.setup()/
# StartupMode.setup() (controller/runtime/modes/smoke.py & startup.py,
# `_init_smoke_cycle`) called `ctx.store.update_metrics(self.state.metrics)`
# while `self.state.metrics` was still the freshly-constructed WorkCycleState
# default `{}` (setup() runs BEFORE ControlMode.run() stamps a fresh metrics
# row -- see base.py's `self.setup()` at line ~573 vs. the
# `append_metric()` stamp two lines later). update_metrics()'s
# "replace last record" path builds `[metrics.get(k) for k in METRIC_COLUMNS]`,
# so a dict missing 'starttime' silently NULLs that column (and every other
# column not in the small dict) on the PREVIOUS mode's already-stamped row.
# These tests pin `process_metrics` (and `create_cookfile`, which calls it) as
# a second, independent line of defense for any row that reaches it with a
# None starttime/endtime -- regardless of how it got that way.
# ---------------------------------------------------------------------------


def test_process_metrics_none_starttime_uses_safe_default():
    poisoned = dict(default_metrics(), id=0, mode="Smoke", starttime=None, endtime=0)
    healthy = dict(default_metrics(), id=1, mode="Stop", starttime=100000, endtime=200000)

    result = process_metrics([poisoned, healthy])

    assert result[0]["starttime"] == 0  # safe default substituted
    assert result[0]["starttime_c"] == epoch_to_time(0)
    assert result[0]["timeinmode"] == "Active"  # endtime == 0 branch, unaffected
    # The healthy row is processed normally and unaffected by the poisoned one.
    assert result[1]["starttime_c"] == epoch_to_time(100000 / 1000)
    assert result[1]["endtime_c"] == epoch_to_time(200000 / 1000)


def test_process_metrics_none_endtime_uses_safe_default():
    poisoned = dict(default_metrics(), id=0, mode="Smoke", starttime=100000, endtime=None)

    result = process_metrics([poisoned])

    assert result[0]["endtime"] == 0  # safe default substituted
    assert result[0]["endtime_c"] == 0
    assert result[0]["timeinmode"] == "Active"  # endtime == 0 branch after the guard
    assert result[0]["starttime_c"] == epoch_to_time(100000 / 1000)  # untouched by the endtime guard


def test_process_metrics_none_augerontime_uses_safe_default():
    poisoned = dict(default_metrics(), id=0, mode="Smoke", starttime=100000, endtime=200000, augerontime=None)

    result = process_metrics([poisoned])

    assert result[0]["augerontime"] == 0  # safe default substituted
    assert result[0]["augerontime_c"] == "0 s"
    assert result[0]["estusage_m"] == "0 grams"


def test_process_metrics_survives_fully_nulled_live_datastore_row():
    """Reproduces the EXACT shape found in the live datastore during this
    fix's inspection (`.superpowers/sdd/task-cookfile-crash-report.md`): the
    real "replace last record" corruption nulls EVERY column not present in
    the small dict the caller passed (SmokeMode/StartupMode.setup() only ever
    set 'p_mode'/'auger_cycle_time'), not just starttime/endtime -- 'id',
    'mode', and 'augerontime' come back None too. starttime/endtime/
    augerontime are the only fields process_metrics does arithmetic on, so
    they're the only ones that need guarding for this function to survive the
    row; the rest (mode, id, ...) are read/compared, not computed on, so a
    None passes through harmlessly (e.g. `None == Mode.STOP` is just False)."""
    live_shaped_row = dict.fromkeys(default_metrics().keys())  # every column None, like seq=11 in pifire.db
    live_shaped_row["seq"] = 11

    result = process_metrics([live_shaped_row])  # pre-fix: crashed on starttime, then (post starttime/endtime-only
    # fix) crashed again on `int(augerontime)` -- this is why the guard covers augerontime too.

    assert result[0]["starttime"] == 0
    assert result[0]["endtime"] == 0
    assert result[0]["augerontime"] == 0
    assert result[0]["mode"] is None  # untouched: not part of the crash, no guard needed


def test_create_cookfile_survives_poisoned_none_starttime_row(ds, isolated_history_folder):
    """End-to-end reproduction of the LIVE crash via `create_cookfile()` ->
    `process_metrics()`, using a metrics row with a None starttime (the
    observable shape of the corruption described above)."""
    settings = cookfile_mod.read_settings()
    primary_label, food_labels = _default_probe_labels(settings)
    _seed_history_row(primary_label, food_labels, 100, 90)

    append_metric(dict(default_metrics(), id=0, mode="Smoke", augerontime=120))
    # append_metric(...) always force-stamps 'starttime' (see
    # common.persistence.history), so a None starttime can only reach the DB
    # via the "replace last record" path (new_metric=False) -- exactly how the
    # real corruption happens (a dict missing/None on 'starttime' overwrites the
    # last row wholesale). Poison the row just written the same way.
    update_metrics(dict(default_metrics(), id=0, mode="Smoke", augerontime=120, starttime=None))
    append_metric(dict(default_metrics(), id=1, mode="Stop", augerontime=30))

    assert read_all_metrics()[0]["starttime"] is None  # sanity: poisoned as expected

    create_cookfile(learning_report_provider=_no_learning_report)

    pifire_files = list(pathlib.Path(isolated_history_folder).glob("*.pifire"))
    assert len(pifire_files) == 1
    with zipfile.ZipFile(pifire_files[0]) as archive:
        events = json.loads(archive.read("events.json"))
    assert events[0]["starttime"] == 0  # safe default applied, row still produced
    assert events[0]["starttime_c"] == epoch_to_time(0)
    assert events[1]["mode"] == "Stop"  # the healthy row is unaffected


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
    create_cookfile(learning_report_provider=_no_learning_report)

    first_files = list(pathlib.Path(isolated_history_folder).glob("*.pifire"))
    assert len(first_files) == 1
    first_path = first_files[0]
    first_bytes = first_path.read_bytes()

    _seed_history_row(primary_label, food_labels, 200, 190)
    create_cookfile(learning_report_provider=_no_learning_report)

    all_files = sorted(pathlib.Path(isolated_history_folder).glob("*.pifire"))
    assert len(all_files) == 2
    second_path = next(p for p in all_files if p != first_path)
    assert second_path.name == first_path.name.replace(".pifire", "-1.pifire")
    assert first_path.read_bytes() == first_bytes
