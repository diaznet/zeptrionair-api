"""Data models returned by zeptrionair_api."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .const import Category

KeyKind = Literal["unused", "smartbutton", "channel", "unknown"]
Direction = Literal["up", "down"]
Transport = Literal["websocket", "chnotify"]

_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")


def version_tuple(version: str) -> tuple[int, int, int] | None:
    """Parse the leading ``MM.mm.bb`` of a zApp version string (``"01.08.28 (Jun 1 2018)"``)."""
    match = _VERSION_RE.match(version)
    if match is None:
        return None
    return int(match[1]), int(match[2]), int(match[3])


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Device identification from ``/zrap/id``."""

    serial: str
    type: str
    hw_version: str
    sw_version: str
    boot_version: str
    sys: str
    oen: str

    @property
    def firmware(self) -> tuple[int, int, int] | None:
        """Firmware version as a comparable tuple, e.g. ``(1, 8, 28)``."""
        return version_tuple(self.sw_version)

    @property
    def model(self) -> str:
        """Product type, e.g. ``3340-2-B``."""
        return self.type

    @property
    def channel_count(self) -> int | None:
        """Number of channels implied by the product type (``3340-<n>-x``)."""
        match = re.match(r"^\d+-(\d+)-", self.type)
        return int(match[1]) if match else None


@dataclass(frozen=True, slots=True)
class Channel:
    """One channel descriptor from ``/zrap/chdes``."""

    number: int
    name: str
    group: str
    icon: str
    type: str
    raw_category: str
    category: Category | None

    @property
    def in_use(self) -> bool:
        """``True`` if the category is known (light, dimmer, blind or awning)."""
        return self.category is not None


@dataclass(frozen=True, slots=True)
class KeyFunction:
    """Function of one Smartfront key, decoded from ``btfu``.

    The decoding is heuristic (see ``docs/protocol.md``); ``raw`` always holds the original code.
    """

    key: int
    raw: str
    kind: KeyKind
    channels: frozenset[int] = frozenset()
    direction: Direction | None = None


@dataclass(frozen=True, slots=True)
class SmartfrontInfo:
    """Smartfront identification from ``/zapi/smartfront/id``."""

    type: str
    sw_version: str
    hw_version: str
    sid: str
    btfu: str
    keys: tuple[KeyFunction, ...]

    def key(self, number: int) -> KeyFunction | None:
        """Return the function of key ``number`` (1-based), if known."""
        for function in self.keys:
            if function.key == number:
                return function
        return None


@dataclass(frozen=True, slots=True)
class SensorValues:
    """Smartfront sensor values from ``/zapi/smartfront/sensors``. ``None`` means not present."""

    temperature: float | None
    illuminance: float | None
    humidity: float | None


@dataclass(frozen=True, slots=True)
class LedState:
    """One Smartfront LED from ``/zapi/smartfront/led``."""

    id: int
    on: bool
    rgb: str
    effect: str
    background: str


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Optional features detected on a device by :meth:`ZeptrionDevice.probe`."""

    websocket: bool
    smartfront: bool
    smartbuttons: bool
    sensors: bool


@dataclass(frozen=True, slots=True)
class ChannelUpdate:
    """A channel changed its value.

    ``value`` is 0-100 for lights. For covers, 100 means the motor is running and 0 means it
    stopped. ``None`` means unknown (``-1``).
    """

    channel: int
    value: int | None


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """A Smartfront key was pressed or released (``eid2``)."""

    key: int
    pressed: bool
    function: KeyFunction | None = None
    raw: str = ""


@dataclass(frozen=True, slots=True)
class ConnectionEvent:
    """The event stream connected or lost its connection."""

    connected: bool
    transport: Transport | None = None
    error: str | None = None


Event = ChannelUpdate | KeyEvent | ConnectionEvent


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    """A device found by mDNS discovery."""

    name: str
    host: str
    addresses: tuple[str, ...]
    port: int
    serial: str | None
    type: str | None = None
    sw_version: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
