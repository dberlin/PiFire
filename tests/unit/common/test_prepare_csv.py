"""`common/app.py::prepare_csv()` crashes when
history is empty (fresh install / after a history clear).

`blueprints/history/routes.py`'s `export` action calls `prepare_csv()` with
no arguments, so `data` keeps its `[]` default and the function falls
through to `data = read_history()`. Two compounding latent bugs live on
that path:

  1. `read_history` is referenced but never imported into `common/app.py`
     -- any call that reaches `data = read_history()` raises `NameError`
     regardless of what history actually contains.
  2. Once `read_history()` legitimately returns `[]` (no history recorded
     yet), the label-building code unconditionally indexes `data[0]`
     *before* the `if list_length > 0:` row-writing guard, raising
     `IndexError`.

Either one 500s the `/history/export` route. The fix guards the label
building the same way the row-writing loop already is guarded, producing
the existing "No Data\\n" convention (already used by this function's own
row-writing `else` branch, and by the sibling `prepare_metrics_csv()`)
instead of crashing.

A SECOND bug lived on the same two functions: both composed their output
path as `filename.replace("./history/", "")` followed by `"/tmp/" + ...`.
The `.replace` only ever matched the DEFAULT `HISTORY_FOLDER` literal, so
under any other configured folder -- including every test's temp folder --
the full source path survived into the result and produced a nonexistent
directory like `/tmp//tmp/pytest-xyz/history/Cook.pifire-Pifire-Export.csv`,
which `open(..., "w")` cannot create. That is precisely why the three
legacy `dl_cookfile`/`dl_eventfile`/`dl_graphfile` branches in
`blueprints/cookfile/routes.py` had never had a single test. Both functions
now take `os.path.basename()`, which is correct for any folder (and,
incidentally, keeps a `../..` form value from escaping the temp dir).
"""

import os

from common.app import prepare_csv, prepare_metrics_csv
from common.defaults import default_metrics


def test_prepare_csv_no_args_empty_history_does_not_crash(ds):
    """Direct repro of the route's call: `prepare_csv()` with the default
    empty `data` falls through to `read_history()`, which returns `[]` on
    a freshly initialized datastore (no history written)."""
    result = prepare_csv()
    try:
        assert os.path.exists(result)
        with open(result) as f:
            content = f.read()
        assert content == "No Data\n"
    finally:
        os.remove(result)


def test_prepare_csv_explicit_empty_data_does_not_crash(ds):
    """Same empty-case guard, exercised via the cookfile route's calling
    convention: `prepare_csv(data, filename)` with an empty `raw_data`
    (e.g. a cookfile with no recorded events). Note `data == []` is True
    for any empty list, so this still falls through to `read_history()`
    same as the no-args call above -- the `ds` fixture keeps that
    read isolated to an empty temp datastore."""
    result = prepare_csv([], "some-cookfile")
    try:
        assert os.path.exists(result)
        with open(result) as f:
            content = f.read()
        assert content == "No Data\n"
    finally:
        os.remove(result)


def _raw_row(ts, grill, food):
    return {
        "T": ts,
        "P": {"grill1": grill},
        "PSP": 225,
        "F": {"probe1": food},
        "NT": {"grill1": 225, "probe1": 165},
        "AUX": {},
    }


def _event_row(**overrides):
    event = default_metrics()
    event.update(overrides)
    return event


def test_prepare_csv_works_under_a_non_default_history_folder(ds, tmp_path):
    """The route passes the cook file's FULL path. Under the default folder
    the `./history/` prefix was stripped by `.replace`; under any other
    folder it survived and `"/tmp/" + <absolute path> + ".csv"` named a
    directory that does not exist, so `open()` raised."""
    history_dir = str(tmp_path / "history") + "/"
    os.makedirs(history_dir, exist_ok=True)
    source = history_dir + "MyCook.pifire"

    result = prepare_csv([_raw_row(1000, 225, 150), _raw_row(2000, 230, 160)], source)

    try:
        assert os.path.dirname(result) == "/tmp", result
        assert os.path.basename(result) == "MyCook.pifire-Pifire-Export.csv"
        lines = open(result).read().splitlines()
        assert lines[0].startswith("Time, ")
        assert len(lines) == 3  # header + two rows
    finally:
        os.remove(result)


def test_prepare_metrics_csv_works_under_a_non_default_history_folder(ds, tmp_path):
    """Same composition bug in the sibling function used by `dl_eventfile`."""
    history_dir = str(tmp_path / "history") + "/"
    os.makedirs(history_dir, exist_ok=True)
    source = history_dir + "MyCook.pifire"

    result = prepare_metrics_csv([_event_row(id=0, mode="Smoke"), _event_row(id=1, mode="Stop")], source)

    try:
        assert os.path.dirname(result) == "/tmp", result
        assert os.path.basename(result) == "MyCook.pifire-PiFire-Metrics-Export.csv"
        lines = open(result).read().splitlines()
        assert len(lines) == 3  # header + two events
    finally:
        os.remove(result)


def test_prepare_csv_default_history_folder_name_is_unchanged(ds):
    """The default folder must keep producing exactly the name it always
    did -- `os.path.basename` has to be a superset of the old `.replace`,
    not a replacement with different output."""
    result = prepare_csv([_raw_row(1000, 225, 150)], "./history/MyCook.pifire")
    try:
        assert result == "/tmp/MyCook.pifire-Pifire-Export.csv"
    finally:
        os.remove(result)


def test_prepare_csv_does_not_let_a_filename_escape_the_temp_dir(ds):
    """`filename` reaches these functions straight off a form field. With the
    old `"/tmp/" + filename` concatenation, `../../etc/passwd` composed
    `/tmp/../../etc/passwd-Pifire-Export.csv` -- i.e. a write outside /tmp."""
    result = prepare_csv([_raw_row(1000, 225, 150)], "../../etc/passwd")
    try:
        assert os.path.dirname(os.path.abspath(result)) == "/tmp", result
    finally:
        os.remove(result)
