"""Constants and command helpers for the zeptrionAir local API."""

from __future__ import annotations

import re
from enum import IntEnum, StrEnum
from typing import Final, Literal

DEFAULT_PORT: Final = 80
DEFAULT_REQUEST_TIMEOUT: Final = 10.0
DEFAULT_MAX_CONCURRENT_REQUESTS: Final = 2

TIMED_MIN_MS: Final = 100
TIMED_MAX_MS: Final = 32000
SCENE_MIN: Final = 1
SCENE_MAX: Final = 4

#: Maximum UTF-8 byte length of each channel descriptor field (``/zrap/chdes``).
CHANNEL_FIELD_LIMITS: Final[dict[str, int]] = {
    "name": 32,
    "group": 32,
    "icon": 24,
    "type": 4,
    "cat": 4,
}

#: mDNS service types announced by zApp devices.
MDNS_SERVICE_ZAPP: Final = "_zapp._tcp.local."
MDNS_SERVICE_HTTP: Final = "_http._tcp.local."
HOSTNAME_PREFIX: Final = "zapp-"


class Category(IntEnum):
    """Channel category (``/zrap/chdes/chN/cat``)."""

    ONOFF = 1
    DIMMER = 3
    BLIND = 5
    AWNING = 6

    @property
    def is_light(self) -> bool:
        return self in (Category.ONOFF, Category.DIMMER)

    @property
    def is_cover(self) -> bool:
        return self in (Category.BLIND, Category.AWNING)


class Command(StrEnum):
    """Simple channel commands for ``/zrap/chctrl``."""

    ON = "on"
    OFF = "off"
    TOGGLE = "toggle"
    STOP = "stop"
    DIM_UP = "dim_up"
    DIM_DOWN = "dim_down"
    OPEN = "open"
    CLOSE = "close"
    MOVE_OPEN = "move_open"
    MOVE_CLOSE = "move_close"


TimedAction = Literal["dim_up", "dim_down", "dim", "move_open", "move_close"]
SceneAction = Literal["recall", "store", "delete"]

_SIMPLE = "|".join(c.value for c in Command)
_COMMAND_RE = re.compile(
    rf"^(?:(?P<simple>{_SIMPLE})"
    r"|(?P<scene>recall|store|delete)_s(?P<n>\d+)"
    r"|(?P<timed>dim_up|dim_down|dim|move_open|move_close)_(?P<ms>\d+))$"
)


def timed_command(action: TimedAction, milliseconds: int) -> str:
    """Build a timed command such as ``move_open_1500``."""
    if not TIMED_MIN_MS <= milliseconds <= TIMED_MAX_MS:
        raise ValueError(
            f"duration must be between {TIMED_MIN_MS} and {TIMED_MAX_MS} ms, got {milliseconds}"
        )
    return f"{action}_{milliseconds}"


def scene_command(action: SceneAction, scene: int) -> str:
    """Build a scene command such as ``recall_s2``."""
    if not SCENE_MIN <= scene <= SCENE_MAX:
        raise ValueError(f"scene must be between {SCENE_MIN} and {SCENE_MAX}, got {scene}")
    return f"{action}_s{scene}"


def validate_command(command: str) -> str:
    """Return ``command`` if it is a valid ``chctrl`` command, otherwise raise ``ValueError``."""
    command = str(command)
    match = _COMMAND_RE.match(command)
    if match is None:
        raise ValueError(f"invalid channel command: {command!r}")
    if match["scene"]:
        scene_command(match["scene"], int(match["n"]))  # type: ignore[arg-type]
    elif match["timed"]:
        timed_command(match["timed"], int(match["ms"]))  # type: ignore[arg-type]
    return command
