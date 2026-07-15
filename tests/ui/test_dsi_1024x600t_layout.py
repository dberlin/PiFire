from tests.conftest import dsi_layout_out_path, load_json

OUT = dsi_layout_out_path("dsi_1024x600t")

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


def test_dash_background_is_bespoke_ember_background():
    assert load_json(OUT)["metadata"]["dash_background"].endswith("background_ember_1024x600.png")


def test_profile_1_dash_is_bespoke_ember_layout():
    d = load_json(OUT)
    dash = d["profile_1"]["dash"]
    assert [obj["name"] for obj in dash] == EMBER_DASH_OBJECT_NAMES
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
    d = load_json(OUT)
    names = [obj["name"] for obj in d["profile_2"]["dash"]]
    assert "primary_gauge" in names
    assert d["profile_2"]["dash"][0]["type"] == "gauge"
    assert "header_bar" not in names
