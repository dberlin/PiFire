from common.i2c_bus import find_i2c_bus


def test_find_i2c_bus_matches_mcp2221_adapter(tmp_path):
    """find_i2c_bus resolves an MCP2221 kernel i2c adapter by its 'MCP2221' name,
    the same substring-match mechanism CP2112 uses."""
    bus = tmp_path / "i2c-7"
    bus.mkdir()
    (bus / "name").write_text("MCP2221 usb-i2c bridge\n")
    # An unrelated adapter present alongside must not confuse the match.
    other = tmp_path / "i2c-0"
    other.mkdir()
    (other / "name").write_text("SMBus PIIX4 adapter\n")

    assert find_i2c_bus(match="MCP2221", devices_path=str(tmp_path)) == 7
