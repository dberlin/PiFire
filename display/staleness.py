"""How a stale probe reading says so, for both on-device dashboards.

A probe device may have no reading to give -- a network-polled one returns
None for a channel whose cache has gone stale rather than inventing a number.
The card goes on showing the last real value, because 40 s of age is still
worth something to someone deciding whether to open the lid, but it must not
read as live: a temperature is plausible at any value, so absence has to be
said rather than implied.

The wording is shared with the web UI (`staleLabel` in
web-react/src/helpers/dashboard/deriveView.ts) so that an operator moving
between the panel and their phone reads one story about the same probe. Both
sides are pinned against the same table of cases.
"""


def resolve_reading(value, last_entry, now_ms):
    """
    What a card should show for one probe, and whether that number is live.

    A probe with no current reading keeps showing its last real one, carrying
    the age with it. Resolved here rather than by passing the absence down:
    the Qt card's `temp` is a typed double that refuses a null (which happens
    to keep the last value on screen, but says nothing about its age and logs
    on every frame), and the pygame card coerced it to 0, which is a plausible
    temperature.

    :param value: the probe's current reading, or None if it had none to give
    :param last_entry: {"temp": x, "ts": epoch_ms} for its last real reading,
        or None if it has produced nothing at all
    :param now_ms: wall clock, for the age
    :return: (temp, has_temp, stale_text); temp is 0.0 when has_temp is False
    """
    if value is not None:
        return float(value), True, ""
    if last_entry is None:
        return 0.0, False, ""
    return float(last_entry["temp"]), True, stale_label((now_ms - last_entry["ts"]) / 1000)


def stale_label(seconds):
    """
    Render a reading's age the way both dashboards word it.

    :param seconds: whole seconds since the reading was taken
    :return: e.g. "last data 47s ago", "last data 3m ago", "last data 2h ago"
    """
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"last data {seconds}s ago"
    if seconds < 3600:
        return f"last data {seconds // 60}m ago"
    return f"last data {seconds // 3600}h ago"
