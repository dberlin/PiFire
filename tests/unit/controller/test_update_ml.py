"""Coverage for controller/update_ml.py: create_new_model() and update_model().

Both `dump` and `load` are imported at module scope (`from joblib import dump,
load`), so tests patch the names bound in the module (controller.update_ml.dump
/ .load), not the global joblib functions. LinearRegression.fit/predict are
NOT mocked -- they're cheap, deterministic, and real sklearn behavior is what
we want to exercise; only persistence (dump) is faked out.
"""

import controller.update_ml as um

_CSV = "current,setpoint,rate_change,cycle_ratio\n105,165,1,1\n139,165,1.6,0.22\n169,165,1.4,0.05\n"


def _write_csv(tmp_path, contents=_CSV):
    csv = tmp_path / "ds.csv"
    csv.write_text(contents)
    return csv


# --- create_new_model: happy path -------------------------------------------------


def test_create_new_model_fits_and_dumps(monkeypatch, tmp_path):
    csv = _write_csv(tmp_path)
    dumped = {}
    monkeypatch.setattr(um, "dump", lambda model, outfile: dumped.setdefault("out", outfile))

    um.create_new_model(infile=str(csv), outfile=str(tmp_path / "m.joblib"), test=False)

    assert dumped["out"].endswith("m.joblib")


def test_create_new_model_dumps_a_fitted_linear_regression(monkeypatch, tmp_path):
    from sklearn.linear_model import LinearRegression

    csv = _write_csv(tmp_path)
    dumped = {}
    monkeypatch.setattr(um, "dump", lambda model, outfile: dumped.setdefault("model", model))

    um.create_new_model(infile=str(csv), outfile=str(tmp_path / "m.joblib"), test=False)

    model = dumped["model"]
    assert isinstance(model, LinearRegression)
    # Fitted models expose coef_ (raises AttributeError if .fit() was never called).
    assert model.coef_ is not None


def test_create_new_model_test_true_prints_prediction_sweep(monkeypatch, tmp_path, capsys):
    csv = _write_csv(tmp_path)
    monkeypatch.setattr(um, "dump", lambda model, outfile: None)

    um.create_new_model(infile=str(csv), outfile=str(tmp_path / "m.joblib"), test=True)

    out = capsys.readouterr().out
    assert "Training model against dataset" in out
    assert "Finished training model" in out
    # range_of_values=100 prediction lines, starting at start_current=110, set_point=165.
    assert "110, 165, 1," in out
    assert "209, 165, 1," in out  # start_current + (range_of_values - 1)


def test_create_new_model_test_false_skips_prediction_sweep(monkeypatch, tmp_path, capsys):
    csv = _write_csv(tmp_path)
    monkeypatch.setattr(um, "dump", lambda model, outfile: None)

    um.create_new_model(infile=str(csv), outfile=str(tmp_path / "m.joblib"), test=False)

    out = capsys.readouterr().out
    assert "110, 165, 1," not in out


# --- create_new_model: unreadable infile -----------------------------------------


def test_create_new_model_missing_infile_prints_error_and_returns(tmp_path, capsys):
    missing = tmp_path / "does_not_exist.csv"

    result = um.create_new_model(infile=str(missing), outfile=str(tmp_path / "m.joblib"), test=False)

    assert result is None
    out = capsys.readouterr().out
    assert f"ERROR: Failed to read file {missing}" in out


# --- update_model -----------------------------------------------------------------


def test_update_model_success_prints_finished(monkeypatch, capsys):
    monkeypatch.setattr(um, "load", lambda infile: object())

    um.update_model(infile="whatever.joblib")

    out = capsys.readouterr().out
    assert "Finished loading model." in out


def test_update_model_load_failure_prints_error(monkeypatch, capsys):
    def boom(infile):
        raise OSError("nope")

    monkeypatch.setattr(um, "load", boom)

    result = um.update_model(infile="whatever.joblib")

    assert result is None
    out = capsys.readouterr().out
    assert "ERROR: Failed to read file whatever.joblib" in out
