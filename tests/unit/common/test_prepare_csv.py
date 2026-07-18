"""Task 4 (bugfix-latent-bugs): `common/app.py::prepare_csv()` crashes when
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
"""

import os

from common.app import prepare_csv


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
