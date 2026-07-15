from tests.conftest import DSI_LAYOUT_SRC, dsi_layout_out_path, load_json

OUT = dsi_layout_out_path("dsi_1280x720t")
SRC = DSI_LAYOUT_SRC

SCALE = 1.5
OFFSETS = {"profile_1": (40, 0), "profile_2": (0, 40)}

EMBER_DASH_OBJECT_NAMES = [
    "header_bar",
    "probe_card_0",
    "probe_card_1",
    "probe_card_2",
    "probe_card_3",
    "probe_card_4",
    "primary_gauge",
    "cook_time",
    "button_row",
    "system_card",
    "duty_pill_left",
    "duty_pill_right",
    "hopper_vertical",
]


def test_splash_image_unchanged():
    assert load_json(OUT)["metadata"]["splash_image"] == "./static/img/display/splash_800x480.png"


def test_dash_background_is_bespoke_ember_background():
    assert load_json(OUT)["metadata"]["dash_background"].endswith("background_ember_1280x720.png")


def test_profile_1_dash_is_bespoke_ember_layout():
    """Task 25: profile_1.dash is a bespoke layout built from the new ember
    flexobject types (Tasks 17-23), not a scaled copy of the 800x480 source."""
    d = load_json(OUT)
    dash = d["profile_1"]["dash"]
    names = [obj["name"] for obj in dash]
    assert names == EMBER_DASH_OBJECT_NAMES

    by_name = {obj["name"]: obj for obj in dash}
    assert by_name["header_bar"]["type"] == "header_bar"
    assert by_name["primary_gauge"]["type"] == "gauge_ember"
    assert by_name["system_card"]["type"] == "system_card"
    assert by_name["duty_pill_left"]["type"] == "duty_pill"
    assert by_name["duty_pill_right"]["type"] == "duty_pill"
    assert by_name["hopper_vertical"]["type"] == "hopper_vertical"
    assert by_name["button_row"]["type"] == "button_row"
    assert by_name["cook_time"]["type"] == "cook_time_bar"
    for index in range(5):
        assert by_name[f"probe_card_{index}"]["type"] == "probe_card"


def test_profile_1_dash_objects_have_common_flexobject_keys():
    d = load_json(OUT)
    for obj in d["profile_1"]["dash"]:
        assert isinstance(obj["animation_enabled"], bool)
        assert isinstance(obj["glow"], bool)
        assert isinstance(obj["data"], dict)
        assert isinstance(obj["button_list"], list)
        assert isinstance(obj["button_value"], list)
        assert isinstance(obj["touch_areas"], list)


def test_profile_2_dash_is_untouched_scaled_layout():
    """profile_2 (portrait) is not part of Task 25 - it stays the scaled
    800x480-derived layout used by every other resolution."""
    d = load_json(OUT)
    names = [obj["name"] for obj in d["profile_2"]["dash"]]
    assert "primary_gauge" in names
    assert d["profile_2"]["dash"][0]["type"] == "gauge"
    assert "header_bar" not in names


def _assert_scaled(so, oo, xoff, yoff):
    if "position" in so:
        x, y = so["position"]
        assert oo["position"] == [round(x * SCALE + xoff), round(y * SCALE + yoff)]
    if "size" in so:
        w, h = so["size"]
        assert oo["size"] == [round(w * SCALE), round(h * SCALE)]


def test_transform_matches_source_for_still_scaled_sections():
    """Task 25 only replaces profile_1.dash and metadata.dash_background.
    profile_1's home/menus/input and everything in profile_2 (home/dash/
    menus/input) remain uniformly scaled from the 800x480 source, exactly
    like every other resolution this generator produces."""
    src = load_json(SRC)
    out = load_json(OUT)
    for profile, (xoff, yoff) in OFFSETS.items():
        sp, op = src[profile], out[profile]
        sections = ("home",) if profile == "profile_1" else ("home", "dash")
        for section in sections:
            for so, oo in zip(sp.get(section, []), op.get(section, [])):
                _assert_scaled(so, oo, xoff, yoff)
        for section in ("menus", "input"):
            for key in sp.get(section, {}):
                _assert_scaled(sp[section][key], op[section][key], xoff, yoff)
