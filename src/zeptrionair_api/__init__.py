"""Async client for Feller zeptrionAir devices (local HTTP and WebSocket API)."""

from __future__ import annotations

from .const import (
    Category,
    Command,
    scene_command,
    timed_command,
    validate_command,
)
from .device import ZeptrionDevice
from .events import EventStream
from .exceptions import (
    ZeptrionConnectionError,
    ZeptrionError,
    ZeptrionNotFoundError,
    ZeptrionParseError,
    ZeptrionResponseError,
    ZeptrionTimeoutError,
)
from .models import (
    Capabilities,
    Channel,
    ChannelUpdate,
    ConnectionEvent,
    DeviceInfo,
    DiscoveredDevice,
    Event,
    KeyEvent,
    KeyFunction,
    LedState,
    SensorValues,
    SmartfrontInfo,
)

try:
    from ._version import __version__
except ImportError:  # pragma: no cover - only when running from a source tree without a build
    __version__ = "0.0.0"

__all__ = [
    "Capabilities",
    "Category",
    "Channel",
    "ChannelUpdate",
    "Command",
    "ConnectionEvent",
    "DeviceInfo",
    "DiscoveredDevice",
    "Event",
    "EventStream",
    "KeyEvent",
    "KeyFunction",
    "LedState",
    "SensorValues",
    "SmartfrontInfo",
    "ZeptrionConnectionError",
    "ZeptrionDevice",
    "ZeptrionError",
    "ZeptrionNotFoundError",
    "ZeptrionParseError",
    "ZeptrionResponseError",
    "ZeptrionTimeoutError",
    "__version__",
    "scene_command",
    "timed_command",
    "validate_command",
]
