"""The TS validation rules and the Python ones must agree.

i2cBusTypes.ts duplicates the per-field format checks so the wizard can report
them on a keystroke rather than an HTTP round trip. Python stays authoritative
for configurations that arrive by settings import or a hand-edited backup. Two
copies of a rule drift; this runs the real `i2cBusError` (via `bun`) and the
real `parse_i2c_bus` over the same case list and asserts their accept/reject
verdicts still match, so the drift is a red test rather than a config the
wizard accepts and the control process rejects.

A hand-authored expectation on either side only ever proves it agrees with
itself, so nothing here is asserted against a value someone read off the TS
source by eye: both verdicts are produced by executing the real code, and the
CASES table's `python_accept`/`ts_accept` columns are pinned assertions about
what that execution currently returns, not a re-implementation of the rules.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from common.i2c_bus_config import I2CBusConfigError, parse_i2c_bus

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TS_RULE_SCRIPT = REPO_ROOT / "web-react/scripts/i2c-bus-rule-check.ts"
BUN = shutil.which("bun")

if BUN is None:
    pytest.skip(
        "bun is not on PATH; this test cross-derives its TS-side verdicts by "
        "executing i2cBusTypes.ts's real i2cBusError with bun, so without bun "
        "it cannot run rather than falling back to a hand-authored expectation.",
        allow_module_level=True,
    )

# (label, config, python_accept, ts_accept, divergence)
#
# `python_accept`/`ts_accept` are pinned to what the two real implementations
# currently do -- not a guess. Where they match, the case proves the rules
# agree. Where they don't, `divergence` records why that is accepted rather
# than a bug: parse_i2c_bus also has to accept an already-normalized object
# already, so it is intentionally more permissive than the wizard's
# keystroke-time UX hints.
CASES = [
    ("basic", {"kind": "basic"}, True, True, None),
    ("kernel bus_num 0", {"kind": "kernel", "bus_num": 0}, True, True, None),
    ("kernel bus_num -1", {"kind": "kernel", "bus_num": -1}, False, False, None),
    ("kernel bus_num 3", {"kind": "kernel", "bus_num": 3}, True, True, None),
    (
        "kernel bus_num null",
        {"kind": "kernel", "bus_num": None},
        False,
        False,
        None,
    ),
    (
        "kernel bus_num non-numeric",
        {"kind": "kernel", "bus_num": "CP2112"},
        False,
        False,
        None,
    ),
    ("kernel adapter empty", {"kind": "kernel", "adapter": ""}, False, False, None),
    (
        "kernel adapter whitespace-only",
        {"kind": "kernel", "adapter": "  "},
        False,
        False,
        None,
    ),
    (
        "kernel adapter populated",
        {"kind": "kernel", "adapter": "CP2112"},
        True,
        True,
        None,
    ),
    ("kernel serial empty", {"kind": "kernel", "serial": ""}, False, False, None),
    ("kernel serial populated", {"kind": "kernel", "serial": "AB12"}, True, True, None),
    ("ft232h url empty", {"kind": "ft232h", "url": ""}, True, True, None),
    (
        "ft232h url '1'",
        {"kind": "ft232h", "url": "1"},
        True,
        False,
        (
            "FT232HBus.__post_init__ normalizes '1' to '' -- both name 'the first "
            "FT232H found' -- so parse_i2c_bus accepts it; the TS rule only "
            "special-cases a blank url, so it reports '1' as not ftdi://-prefixed."
        ),
    ),
    (
        "ft232h url ftdi://...",
        {"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9/1"},
        True,
        True,
        None,
    ),
    (
        "ft232h url not ftdi://",
        {"kind": "ft232h", "url": "CP2112"},
        True,
        False,
        (
            "parse_i2c_bus stores any non-blank, non-'1' ft232h url as-is -- it "
            "does not format-check it. The wizard's ftdi:// prefix check is a "
            "keystroke-time UX hint, not a structural rule Python enforces."
        ),
    ),
    ("mcp2221 serial empty", {"kind": "mcp2221", "serial": ""}, True, True, None),
    (
        "mcp2221 serial populated",
        {"kind": "mcp2221", "serial": "0123"},
        True,
        True,
        None,
    ),
]


@pytest.fixture(scope="module")
def ts_verdicts():
    """Runs the real i2cBusError, once, over every case's config."""
    configs = [config for _, config, _, _, _ in CASES]
    result = subprocess.run(
        [BUN, "run", str(TS_RULE_SCRIPT)],
        input=json.dumps(configs),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    verdicts = json.loads(result.stdout)
    assert len(verdicts) == len(CASES), "bun script returned the wrong number of verdicts"
    return verdicts


def _python_accepts(config):
    try:
        parse_i2c_bus(config)
    except I2CBusConfigError:
        return False
    return True


@pytest.mark.parametrize("label,config,python_accept,ts_accept,divergence", CASES, ids=[c[0] for c in CASES])
def test_python_matches_its_pinned_verdict(label, config, python_accept, ts_accept, divergence):
    assert _python_accepts(config) is python_accept, label


def test_ts_matches_its_pinned_verdict(ts_verdicts):
    for (label, _config, _python_accept, ts_accept, _divergence), verdict in zip(CASES, ts_verdicts):
        assert verdict is ts_accept, label


def test_python_and_ts_agree_except_where_documented(ts_verdicts):
    """The parity assertion: run both real implementations over the same
    cases and require the same accept/reject verdict, unless the case names a
    documented, intentional reason they differ."""
    for (label, config, python_accept, ts_accept, divergence), ts_verdict in zip(CASES, ts_verdicts):
        python_verdict = _python_accepts(config)
        assert ts_verdict is ts_accept, f"{label}: TS verdict drifted from its pin"
        if divergence is None:
            assert python_verdict == ts_verdict, (
                f"{label}: Python and TS disagree with no documented reason -- {config}"
            )
        else:
            assert python_verdict != ts_verdict, (
                f"{label}: documented as a divergence ({divergence}) but the two sides now agree -- "
                "update or drop the documented difference"
            )
