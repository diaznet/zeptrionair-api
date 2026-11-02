"""Parser tests against real (anonymised) device responses and documented quirks."""

from __future__ import annotations

import pytest
from conftest import DEVICE_NAMES, body, device_responses, ws_frames

from zeptrionair_api import Category, ZeptrionParseError
from zeptrionair_api.const import scene_command, timed_command, validate_command
from zeptrionair_api.models import DeviceInfo, version_tuple
from zeptrionair_api.parsing import (
    KeyStateTracker,
    parse_btfu,
    parse_category,
    parse_channels,
    parse_device_info,
    parse_key_function,
    parse_leds,
    parse_mac,
    parse_rssi,
    parse_sensors,
    parse_smartfront,
    parse_states,
    parse_value,
    parse_ws_frame,
    parse_xml,
)


@pytest.mark.parametrize("name", DEVICE_NAMES)
def test_real_device_responses_parse(name: str) -> None:
    info = parse_device_info(body(name, "/zrap/id"))
    assert info.serial.startswith("9999")
    assert info.type == "3340-2-B"
    assert info.firmware == (1, 8, 28)
    assert info.channel_count == 2

    channels = parse_channels(body(name, "/zrap/chdes"))
    assert [c.number for c in channels] == [1, 2]
    assert channels[0].category in (Category.ONOFF, Category.BLIND)

    states = parse_states(body(name, "/zrap/chscan"))
    assert set(states) == {1, 2}
    assert all(value in (0, 100) for value in states.values())

    assert -100 < parse_rssi(body(name, "/zrap/rssi")) < 0
    assert parse_mac(body(name, "/zrap/net/mac")).startswith("02:00:00:00:00:")

    front = parse_smartfront(body(name, "/zapi/smartfront/id"))
    assert len(front.keys) == 8
    assert front.key(3) is not None
    assert front.key(3).kind == "smartbutton"  # type: ignore[union-attr]
    assert front.key(99) is None

    sensors = parse_sensors(body(name, "/zapi/smartfront/sensors"))
    assert sensors.temperature is not None
    assert sensors.illuminance is None
    assert sensors.humidity is None

    leds = parse_leds(body(name, "/zapi/smartfront/led"))
    assert [led.id for led in leds] == list(range(1, 9))
    assert all(led.background.startswith("#") for led in leds)


def test_unused_channels() -> None:
    by_raw = {}
    for name in DEVICE_NAMES:
        for channel in parse_channels(body(name, "/zrap/chdes")):
            by_raw[channel.raw_category] = channel
    assert by_raw["-1"].category is None
    assert by_raw["00"].category is None
    assert not by_raw["00"].in_use
    assert by_raw["5"].in_use


def test_singular_sensor_endpoint_is_404_in_fixtures() -> None:
    responses = device_responses(DEVICE_NAMES[0])
    assert responses["/zapi/smartfront/sensor"]["status"] == 404
    assert responses["/zapi/smartfront/sensors"]["status"] == 200


def test_malformed_examples_from_documentation() -> None:
    # Section 3.5.4 of the PDF closes <chnotify> with </hnotify>.
    states = parse_states(
        '<?xml version="1.0" encoding="US-ASCII"?>\n<chnotify>\n  <ch1>\n    <val>0</val>\n'
        "  </ch1>\n</hnotify>"
    )
    assert states == {1: 0}
    # Section 3.7.4 "closes" <chdes> with another <chdes>.
    channels = parse_channels(
        "<chdes><ch1><name>Ceiling Lamp</name><group>Living Room</group><icon> picture34.png"
        "</icon><type>3452</type><cat>3209</cat></ch1><ch2><name>Spots</name><group>Kitchen"
        "</group><icon>picture01.jpg</icon><type>1234</type><cat>34</cat></ch2><chdes>"
    )
    assert [c.name for c in channels] == ["Ceiling Lamp", "Spots"]
    assert channels[0].icon == "picture34.png"
    assert channels[0].category is None


def test_entities_are_unescaped() -> None:
    node = parse_xml(
        '<?xml version="1.0" encoding="UTF-8"?><name>\n  Küche &lt;Deckenspots &amp; '
        "Wandlampe&gt;\n</name>"
    )
    assert node.text == "Küche <Deckenspots & Wandlampe>"


def test_single_channel_roots() -> None:
    assert parse_states("<ch2><val>100</val></ch2>") == {2: 100}
    channels = parse_channels("<ch2><name>Spots</name><cat>1</cat></ch2>")
    assert channels[0].number == 2
    assert channels[0].category is Category.ONOFF


def test_self_closing_and_comments() -> None:
    node = parse_xml("<!-- c --><rssi><dbm/><x>1</x></rssi>")
    assert node.findtext("dbm") == ""
    assert node.findtext("x") == "1"
    assert node.findtext("missing", "d") == "d"


@pytest.mark.parametrize(
    ("document", "parser"),
    [
        ("no xml at all", parse_xml),
        ("<id><hw>1</hw></id>", parse_device_info),
        ("<rssi><dbm>strong</dbm></rssi>", parse_rssi),
        ("<mac>not-a-mac</mac>", parse_mac),
        ("<other/>", parse_states),
        ("<other/>", parse_channels),
        ("<chscan/>", parse_device_info),
        ("{bad json", parse_smartfront),
        ("[1, 2]", parse_smartfront),
        ("[1, 2]", parse_sensors),
        ('{"a": 1}', parse_leds),
        ("[1]", parse_ws_frame),
    ],
)
def test_parse_errors(document: str, parser: object) -> None:
    with pytest.raises(ZeptrionParseError):
        parser(document)  # type: ignore[operator]


def test_json_trailing_commas_from_documentation() -> None:
    sensors = parse_sensors('{ "temp" :"24.50C", "lux"  :"none", "hum"  :"none", }')
    assert sensors.temperature == pytest.approx(24.5)
    leds = parse_leds('[{"id":2,"bg":"#220000",},{"id":4,"bg":"#002200",}]')
    assert [led.background for led in leds] == ["#220000", "#002200"]
    assert parse_leds('[{"id":1,"effects":"#fff"}, 5]')[0].effect == "#fff"


@pytest.mark.parametrize(
    ("text", "expected"), [("0", 0), ("100", 100), ("-1", None), ("", None), ("x", None)]
)
def test_parse_value(text: str, expected: int | None) -> None:
    assert parse_value(text) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", Category.ONOFF),
        ("3", Category.DIMMER),
        ("5", Category.BLIND),
        ("6", Category.AWNING),
        ("00", None),
        ("-1", None),
        ("2", None),
        ("", None),
    ],
)
def test_parse_category(raw: str, expected: Category | None) -> None:
    assert parse_category(raw) is expected


def test_category_helpers() -> None:
    assert Category.DIMMER.is_light
    assert not Category.DIMMER.is_cover
    assert Category.AWNING.is_cover


@pytest.mark.parametrize(
    ("code", "kind", "channels", "direction"),
    [
        ("0", "unused", set(), None),
        ("", "unused", set(), None),
        ("1000", "smartbutton", set(), None),
        ("8501", "channel", {1}, "up"),
        ("8602", "channel", {1}, "down"),
        ("4501", "channel", {2}, "up"),
        ("4602", "channel", {2}, "down"),
        ("2501", "channel", {3}, "up"),
        ("1602", "channel", {4}, "down"),
        ("C501", "unknown", set(), None),
        ("8701", "unknown", set(), None),
        ("0501", "unknown", set(), None),
    ],
)
def test_key_function_decoding(
    code: str, kind: str, channels: set[int], direction: str | None
) -> None:
    function = parse_key_function(1, code)
    assert function.kind == kind
    assert set(function.channels) == channels
    assert function.direction == direction
    assert function.raw == code


def test_parse_btfu_layouts() -> None:
    keys = parse_btfu("0,0,1000,1000,0,0,8501,8602")
    assert [k.key for k in keys] == list(range(1, 9))
    assert keys[6].direction == "up"
    assert keys[7].direction == "down"
    assert parse_btfu("") == ()


def _key_events(fixture: str, btfu: str) -> list[tuple[int, bool, str | None]]:
    tracker = KeyStateTracker(parse_btfu(btfu))
    out = []
    for line in ws_frames(fixture):
        frame = parse_ws_frame(line["frame"])
        if "eid2" in frame:
            for event in tracker.update(frame["eid2"]["bta"]):
                direction = event.function.direction if event.function else None
                out.append((event.key, event.pressed, direction))
    return out


def test_key_tracker_on_recorded_cover_presses() -> None:
    events = _key_events("cover_wall_down_up_selfstop", "0,0,1000,1000,0,0,8501,8602")
    assert events == [
        (8, True, "down"),
        (8, False, "down"),
        (7, True, "up"),
        (7, False, "up"),
        (7, True, "up"),
        (7, False, "up"),
    ]


def test_key_tracker_on_recorded_light_presses() -> None:
    events = _key_events("light_wall_keys", "0,0,1000,1000,8501,8602,4501,4602")
    pressed = [(key, direction) for key, is_pressed, direction in events if is_pressed]
    assert pressed == [(5, "up"), (3, None), (6, "down"), (8, "down"), (4, None)]


def test_key_tracker_multiple_and_reset() -> None:
    tracker = KeyStateTracker()
    first = tracker.update("P.P......")
    assert [(e.key, e.pressed) for e in first] == [(1, True), (3, True)]
    assert first[0].function is None
    assert [(e.key, e.pressed) for e in tracker.update("..P......")] == [(1, False)]
    tracker.reset()
    assert [(e.key, e.pressed) for e in tracker.update("..P......")] == [(3, True)]


def test_versions() -> None:
    assert version_tuple("01.08.28 (Jun  1 2018 09:13:39)") == (1, 8, 28)
    assert version_tuple("unknown") is None
    info = DeviceInfo("1", "weird", "", "x", "", "", "")
    assert info.firmware is None
    assert info.channel_count is None
    assert info.model == "weird"


def test_commands() -> None:
    assert timed_command("move_open", 1500) == "move_open_1500"
    assert scene_command("recall", 2) == "recall_s2"
    for valid in ("on", "stop", "recall_s4", "dim_100", "move_close_32000"):
        assert validate_command(valid) == valid
    for invalid in ("", "ON", "jump", "recall_s5", "move_open_99", "dim_up_40000", "on;reboot"):
        with pytest.raises(ValueError, match=r"."):
            validate_command(invalid)
    with pytest.raises(ValueError, match="scene"):
        scene_command("store", 0)
