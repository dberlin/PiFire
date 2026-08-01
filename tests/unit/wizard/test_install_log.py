"""The wizard install-log reader.

The panel behind "Show output" appends what it is handed, so the reader owes it
two things beyond the bytes: a slice scoped to the CURRENT run, and a `reset`
flag whenever appending would splice unrelated output together.
"""

from common.install_log import MAX_BYTES, RUN_MARKER, read_install_log


def write_log(tmp_path, *lines):
    path = tmp_path / "wizard.log"
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return str(path)


def test_missing_log_reads_empty_rather_than_raising(tmp_path):
    # The panel can be opened before the installer has written anything.
    text, offset, reset = read_install_log(0, path=str(tmp_path / "absent.log"))
    assert (text, offset, reset) == ("", 0, False)


def test_reads_only_the_current_run(tmp_path):
    path = write_log(
        tmp_path,
        f"old | INFO | {RUN_MARKER}",
        "old | INFO | installing something from a previous run",
        f"new | INFO | {RUN_MARKER}",
        "new | INFO | Resolved 12 packages",
    )
    text, _, _ = read_install_log(0, path=path)

    assert "Resolved 12 packages" in text
    assert "previous run" not in text
    # The marker line itself is kept: it is the panel's "this install" header.
    assert text.startswith(f"new | INFO | {RUN_MARKER}")


def test_offset_returns_only_what_was_appended_since(tmp_path):
    path = write_log(tmp_path, f"t | INFO | {RUN_MARKER}", "t | INFO | first")
    first_text, offset, _ = read_install_log(0, path=path)
    assert "first" in first_text

    with open(path, "a", encoding="utf-8") as handle:
        handle.write("t | INFO | second\n")

    text, next_offset, reset = read_install_log(offset, path=path)
    assert text == "t | INFO | second\n"
    assert next_offset > offset
    assert reset is False


def test_offset_from_a_previous_run_resets_instead_of_appending(tmp_path):
    """A second install in the same browser session. The held offset is a real
    offset into the file, so nothing looks wrong -- but everything between it
    and the new run's marker belongs to the install that already finished."""
    path = write_log(tmp_path, f"t | INFO | {RUN_MARKER}", "t | INFO | first run line")
    _, stale_offset, _ = read_install_log(0, path=path)

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"t | INFO | {RUN_MARKER}\nt | INFO | second run line\n")

    text, _, reset = read_install_log(stale_offset, path=path)
    assert reset is True
    assert "second run line" in text
    assert "first run line" not in text


def test_offset_past_the_end_resets(tmp_path):
    """Rotation truncates the file under a cursor the client is still holding.
    Appending from there would silently drop the head of the new file."""
    path = write_log(tmp_path, f"t | INFO | {RUN_MARKER}", "t | INFO | line")
    text, _, reset = read_install_log(10_000_000, path=path)

    assert reset is True
    assert "line" in text


def test_oversized_read_is_capped_at_a_line_boundary(tmp_path):
    path = write_log(
        tmp_path,
        f"t | INFO | {RUN_MARKER}",
        *[f"t | INFO | padding line {n:06d}" for n in range(12_000)],
        "t | INFO | the final line",
    )
    text, _, reset = read_install_log(0, path=path)

    assert len(text.encode()) <= MAX_BYTES
    assert reset is True
    assert text.endswith("the final line\n")
    # Truncation lands between lines, never mid-line.
    assert text.startswith("t | INFO | padding line ")
