"""Every uv step logs what it did and aborts the script when it fails.

Three properties, and all three have to hold at once -- each one on its own is
defeated by the absence of the others:

  * FATAL. None of these scripts run under `set -e`, so an unchecked failure is
    discarded. For `uv venv` that is the worst case: `source .venv/bin/activate`
    on the next line fails just as quietly, and the rest of the install runs
    against the SYSTEM interpreter -- which on Debian and Fedora is externally
    managed, so it resurfaces as an unrelated dependency error rather than as
    "the venv was never created".

  * LOGGED. `curl | tee` output on a terminal is gone the moment the install
    ends, and installs are routinely run over ssh or from a one-shot
    `curl ... | bash`. Whatever uv printed about WHY it failed has to be in the
    logfile, because that file is all anyone has afterwards.

  * PIPEFAIL, which is what ties the two together. Appending `| tee -a` to a
    guarded command hands the pipeline's exit status to tee -- which always
    succeeds -- so adding the logging silently UNDOES the guard unless pipefail
    is in effect. install-debian.sh and install-fedora.sh set it once at the top
    of the file; install.sh and pifire-dietpi.sh use a guarded subshell.

Structural, like test_supervisor_restart_is_uniform: it pins the shape at every
call site, so a fifth installer -- or this step moved elsewhere -- carries the
properties whether or not anyone writes a test for it.

LIMIT: it recognises the `if ! ...; then ... exit N ... fi` shape only. A guard
written some other correct way (an explicit PIPESTATUS check, a helper that
aborts on the caller's behalf) reads as a violation here, and this sweep has to
learn it.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]

#: Every fresh installer that installs uv and creates the venv. Kept explicit
#: rather than globbed so coverage loss is visible.
SCRIPTS = [
    "auto-install/install.sh",
    "auto-install/install-debian.sh",
    "auto-install/install-fedora.sh",
    "auto-install/pifire-dietpi.sh",
]

#: The uv steps, as commands rather than prose.
STEPS = {
    "uv venv": re.compile(r"(?:^|[;&|(]\s*|!\s+)uv\s+venv\b"),
    "the uv installer": re.compile(r"astral\.sh/uv/install\.sh"),
}

PIPEFAIL = "set -o pipefail"


def call_sites(lines, pattern):
    """Indexes of every non-comment line matching `pattern`."""
    return [
        index for index, line in enumerate(lines) if pattern.search(line.strip()) and not line.strip().startswith("#")
    ]


def enclosing_guard(lines, index):
    """(start, body) of the `if ! ...` block containing `lines[index]`.

    Returns (None, []) when the command is not inside one -- either because it
    is unguarded, or because the guard is a shape this sweep does not know.
    """
    for start in range(index, max(index - 4, -1), -1):
        if not lines[start].strip().startswith("if ! "):
            continue
        body = []
        for line in lines[start + 1 :]:
            if line.strip() == "fi":
                return start, body
            body.append(line)
        break
    return None, []


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("step", sorted(STEPS))
def test_uv_step_aborts_the_script_when_it_fails(script, step):
    lines = (ROOT / script).read_text().splitlines()
    sites = call_sites(lines, STEPS[step])
    assert sites, f"{script} no longer runs {step} -- update SCRIPTS/STEPS"

    for index in sites:
        start, body = enclosing_guard(lines, index)
        assert start is not None, (
            f"{script}:{index + 1} runs {step} unguarded; nothing here runs under `set -e`, "
            f"so the failure is discarded and the script carries on: {lines[index].strip()}"
        )
        assert any("exit" in line for line in body), (
            f"{script}:{index + 1} notices {step} failing but does not stop; it has to abort, not warn and carry on"
        )


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("step", sorted(STEPS))
def test_uv_step_output_reaches_the_logfile(script, step):
    lines = (ROOT / script).read_text().splitlines()

    for index in call_sites(lines, STEPS[step]):
        assert "tee -a" in lines[index], (
            f"{script}:{index + 1} sends {step} output to the terminal only; when it fails, "
            f"the reason is not in the logfile that is all anyone has afterwards: "
            f"{lines[index].strip()}"
        )


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_uv_installer_logs_curls_own_errors_too(script):
    """Piping curl into sh captures the INSTALLER's output, not curl's.

    curl's diagnostics -- the 404, the DNS failure, the TLS error -- go to its
    stderr, which is not part of the pipe that tee is on, so the one line saying
    why the download never happened was the one line that missed the log. It is
    redirected to the file rather than teed: a `2> >(tee ...)` process
    substitution can lose the write when the script exits immediately after, and
    the guard prints its own "Failed to download or install UV" to the terminal,
    so nothing is left silent.
    """
    lines = (ROOT / script).read_text().splitlines()

    for index in call_sites(lines, STEPS["the uv installer"]):
        assert re.search(r"2>>\s*\S", lines[index]), (
            f"{script}:{index + 1} lets curl's own errors bypass the log; a failed download "
            f"leaves no record of why: {lines[index].strip()}"
        )


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("step", sorted(STEPS))
def test_logging_a_uv_step_does_not_hand_its_exit_status_to_tee(script, step):
    lines = (ROOT / script).read_text().splitlines()
    file_wide = any(line == PIPEFAIL for line in lines)

    for index in call_sites(lines, STEPS[step]):
        if "|" not in lines[index]:
            continue
        start, _ = enclosing_guard(lines, index)
        in_guard = start is not None and any(line.strip() == PIPEFAIL for line in lines[start : index + 1])
        assert file_wide or in_guard, (
            f"{script}:{index + 1} pipes {step} without pipefail in effect, so the guard tests "
            f"tee's exit status -- which always succeeds -- and the failure is swallowed: "
            f"{lines[index].strip()}"
        )
