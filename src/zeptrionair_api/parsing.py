"""Lenient parsers for zApp responses.

The firmware's XML is simple (no attributes, no namespaces), but the official documentation
shows malformed examples, so a small tolerant tokenizer is used instead of a strict XML parser:
- mismatched closing tags close the nearest matching open element (or are ignored);
- unclosed elements are closed at the end of the document;
- entities are unescaped.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .const import Category
from .exceptions import ZeptrionParseError
from .models import (
    Channel,
    DeviceInfo,
    Direction,
    KeyEvent,
    KeyFunction,
    LedState,
    SensorValues,
    SmartfrontInfo,
)

_TOKEN_RE = re.compile(r"<(/?)([A-Za-z_][\w.\-]*)[^>]*?(/?)>|<\?.*?\?>|<!--.*?-->|<![^>]*>", re.S)
_CHANNEL_TAG_RE = re.compile(r"^ch(\d+)$")
_BITMASK_CHANNELS = {8: 1, 4: 2, 2: 3, 1: 4}


@dataclass
class Node:
    """A parsed XML element."""

    tag: str
    text: str = ""
    children: list[Node] = field(default_factory=list)

    def find(self, tag: str) -> Node | None:
        for child in self.children:
            if child.tag == tag:
                return child
        return None

    def findtext(self, tag: str, default: str = "") -> str:
        child = self.find(tag)
        return child.text if child is not None else default

    def channels(self) -> Iterator[tuple[int, Node]]:
        """Yield ``(number, node)`` for each ``chN`` child."""
        for child in self.children:
            match = _CHANNEL_TAG_RE.match(child.tag)
            if match:
                yield int(match[1]), child


def parse_xml(document: str) -> Node:
    """Parse a zApp XML document into a :class:`Node` tree (leniently)."""
    root = Node("#document")
    stack = [root]
    position = 0
    for match in _TOKEN_RE.finditer(document):
        stack[-1].text += document[position : match.start()]
        position = match.end()
        closing, tag, self_closing = match.group(1), match.group(2), match.group(3)
        if tag is None:  # declaration, comment or doctype
            continue
        if closing:
            for depth in range(len(stack) - 1, 0, -1):
                if stack[depth].tag == tag:
                    del stack[depth:]
                    break
            continue
        node = Node(tag)
        stack[-1].children.append(node)
        if not self_closing:
            stack.append(node)
    stack[-1].text += document[position:]

    _finalise(root)
    if not root.children:
        raise ZeptrionParseError(f"no XML element found in {document[:80]!r}")
    return root.children[0]


def _finalise(node: Node) -> None:
    node.text = html.unescape(node.text).strip()
    for child in node.children:
        _finalise(child)


def _root(document: str, *expected: str) -> Node:
    node = parse_xml(document)
    if expected and node.tag not in expected:
        raise ZeptrionParseError(f"expected <{'|'.join(expected)}>, got <{node.tag}>")
    return node


def parse_value(text: str) -> int | None:
    """Parse a channel value. ``-1``, empty and non-numeric values become ``None``."""
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return None if value < 0 else value


def parse_category(raw: str) -> Category | None:
    """Map a raw ``cat`` string to a :class:`Category`, or ``None`` if unused or unknown."""
    try:
        return Category(int(raw.strip()))
    except ValueError:
        return None


def parse_device_info(document: str) -> DeviceInfo:
    node = _root(document, "id")
    serial = node.findtext("sn")
    if not serial:
        raise ZeptrionParseError("device identification has no serial number")
    return DeviceInfo(
        serial=serial,
        type=node.findtext("type"),
        hw_version=node.findtext("hw"),
        sw_version=node.findtext("sw"),
        boot_version=node.findtext("boot"),
        sys=node.findtext("sys"),
        oen=node.findtext("oen"),
    )


def parse_channels(document: str) -> list[Channel]:
    node = parse_xml(document)
    if _CHANNEL_TAG_RE.match(node.tag):  # /zrap/chdes/chN may return the bare channel
        node = Node("chdes", children=[node])
    elif node.tag != "chdes":
        raise ZeptrionParseError(f"expected <chdes>, got <{node.tag}>")
    channels = []
    for number, ch in node.channels():
        raw_category = ch.findtext("cat")
        channels.append(
            Channel(
                number=number,
                name=ch.findtext("name"),
                group=ch.findtext("group"),
                icon=ch.findtext("icon"),
                type=ch.findtext("type"),
                raw_category=raw_category,
                category=parse_category(raw_category),
            )
        )
    return sorted(channels, key=lambda channel: channel.number)


def parse_states(document: str) -> dict[int, int | None]:
    """Parse ``/zrap/chscan`` or ``/zrap/chnotify`` (also a single ``<chN>`` root)."""
    node = parse_xml(document)
    if _CHANNEL_TAG_RE.match(node.tag):
        node = Node("chscan", children=[node])
    elif node.tag not in ("chscan", "chnotify"):
        raise ZeptrionParseError(f"expected <chscan> or <chnotify>, got <{node.tag}>")
    return {number: parse_value(ch.findtext("val")) for number, ch in node.channels()}


def parse_rssi(document: str) -> int:
    node = _root(document, "rssi")
    text = node.findtext("dbm") or node.text
    try:
        return int(text)
    except ValueError as err:
        raise ZeptrionParseError(f"invalid RSSI value {text!r}") from err


def parse_mac(document: str) -> str:
    node = _root(document, "mac")
    mac = node.text.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac):
        raise ZeptrionParseError(f"invalid MAC address {mac!r}")
    return mac


def _json(document: str) -> Any:
    try:
        return json.loads(document)
    except json.JSONDecodeError:
        # The documentation shows trailing commas; tolerate them.
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", document))
        except json.JSONDecodeError as err:
            raise ZeptrionParseError(f"invalid JSON: {document[:80]!r}") from err


def parse_key_function(key: int, code: str) -> KeyFunction:
    """Decode one ``btfu`` entry (heuristic, see docs/protocol.md section 6)."""
    code = code.strip()
    if code in ("", "0"):
        return KeyFunction(key=key, raw=code, kind="unused")
    if code == "1000":
        return KeyFunction(key=key, raw=code, kind="smartbutton")
    if re.fullmatch(r"\d{4}", code):
        mask, direction_digit = int(code[0]), code[1]
        channels = frozenset(ch for bit, ch in _BITMASK_CHANNELS.items() if mask & bit)
        direction: Direction | None = {"5": "up", "6": "down"}.get(direction_digit)  # type: ignore[assignment]
        if channels and direction is not None:
            return KeyFunction(
                key=key, raw=code, kind="channel", channels=channels, direction=direction
            )
    return KeyFunction(key=key, raw=code, kind="unknown")


def parse_btfu(btfu: str) -> tuple[KeyFunction, ...]:
    return tuple(
        parse_key_function(index, code)
        for index, code in enumerate(btfu.split(","), start=1)
        if btfu.strip()
    )


def parse_smartfront(document: str) -> SmartfrontInfo:
    data = _json(document)
    if not isinstance(data, Mapping):
        raise ZeptrionParseError("smartfront id is not a JSON object")
    btfu = str(data.get("btfu", ""))
    return SmartfrontInfo(
        type=str(data.get("type", "")),
        sw_version=str(data.get("sw", "")),
        hw_version=str(data.get("hw", "")),
        sid=str(data.get("sid", "")),
        btfu=btfu,
        keys=parse_btfu(btfu),
    )


def _sensor_value(value: Any) -> float | None:
    if value is None:
        return None
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)", str(value))
    return float(match[1]) if match else None


def parse_sensors(document: str) -> SensorValues:
    data = _json(document)
    if not isinstance(data, Mapping):
        raise ZeptrionParseError("sensors response is not a JSON object")
    return SensorValues(
        temperature=_sensor_value(data.get("temp")),
        illuminance=_sensor_value(data.get("lux")),
        humidity=_sensor_value(data.get("hum")),
    )


def parse_leds(document: str) -> list[LedState]:
    data = _json(document)
    if not isinstance(data, Sequence) or isinstance(data, str):
        raise ZeptrionParseError("LED response is not a JSON array")
    return [
        LedState(
            id=int(item.get("id", 0)),
            on=bool(item.get("on", False)),
            rgb=str(item.get("rgb", "")),
            effect=str(item.get("effect", item.get("effects", ""))),
            background=str(item.get("bg", "")),
        )
        for item in data
        if isinstance(item, Mapping)
    ]


def parse_ws_frame(frame: str) -> dict[str, Any]:
    """Parse a WebSocket text frame into a dict (``{"eid1": {...}}`` or ``{"eid2": {...}}``)."""
    data = _json(frame)
    if not isinstance(data, dict):
        raise ZeptrionParseError(f"unexpected WebSocket frame {frame[:80]!r}")
    return data


class KeyStateTracker:
    """Turns successive ``bta`` strings into individual press/release :class:`KeyEvent` objects."""

    def __init__(self, keys: Sequence[KeyFunction] = ()) -> None:
        self._functions = {function.key: function for function in keys}
        self._pressed: set[int] = set()

    def set_keys(self, keys: Sequence[KeyFunction]) -> None:
        self._functions = {function.key: function for function in keys}

    def reset(self) -> None:
        self._pressed.clear()

    def update(self, bta: str) -> list[KeyEvent]:
        pressed = {index + 1 for index, char in enumerate(bta) if char.upper() == "P"}
        events = [
            KeyEvent(key=key, pressed=False, function=self._functions.get(key), raw=bta)
            for key in sorted(self._pressed - pressed)
        ]
        events += [
            KeyEvent(key=key, pressed=True, function=self._functions.get(key), raw=bta)
            for key in sorted(pressed - self._pressed)
        ]
        self._pressed = pressed
        return events
