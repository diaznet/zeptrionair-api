"""A simulated zApp device for tests (an aiohttp web server on localhost).

Example::

    async with FakeZeptrionDevice(channels=[FakeChannel(category="1"), FakeChannel("5")]) as fake:
        device = ZeptrionDevice(fake.host)
        await device.turn_on(1)
        assert fake.channels[0].value == 100

Behaviour follows what was observed on real devices (docs/protocol.md):

- lights: ``on``/``off``/``toggle``; dimmers ramp their value while dimming;
- covers: the value is 100 while the motor runs and 0 when it stops. The motor stops by itself
  after ``travel_time`` (scaled by ``time_scale``), and an opposite key press while moving stops
  it;
- every change is pushed to WebSocket clients as ``eid1``, and :meth:`press_key` sends ``eid2``;
- ``chnotify`` answers on the next change, or after ``notify_timeout`` seconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self
from xml.sax.saxutils import escape

from aiohttp import WSMsgType, web

from ..parsing import parse_btfu

_XML_ASCII = '<?xml version="1.0" encoding="US-ASCII"?>'
_XML_UTF8 = '<?xml version="1.0" encoding="UTF-8"?>'
_NOT_FOUND = (
    '<!DOCTYPE html>\n<html>\n<body><center><h1>404 - file not found</h1><h3>Go <a href="/">'
    "here</a> instead.</h3></center></body>\n</html>"
)
_TIMED_RE = re.compile(r"^(dim_up|dim_down|dim|move_open|move_close)_(\d+)$")
_SCENE_RE = re.compile(r"^(recall|store|delete)_s([1-4])$")


@dataclass
class FakeChannel:
    """One simulated channel."""

    category: str = "1"
    name: str = ""
    group: str = ""
    icon: str = "00"
    type: str = "00"
    value: int = 0
    #: Cover position (0 = closed, 100 = open). Not visible through the API, used by tests.
    position: float = 100.0
    #: Seconds for a full cover travel or a full 0-100 dim (before ``time_scale``).
    travel_time: float = 10.0
    scenes: dict[int, float] = field(default_factory=dict)
    direction: str | None = None  # "up"/"down" while a cover moves or a dimmer ramps

    @property
    def is_cover(self) -> bool:
        return self.category in ("5", "6")

    @property
    def is_dimmer(self) -> bool:
        return self.category == "3"


class FakeZeptrionDevice:
    """Simulated zApp device. Use as an async context manager or call start()/stop()."""

    def __init__(
        self,
        *,
        serial: str = "99990001",
        type: str = "3340-2-B",
        sw_version: str = "01.08.28 (Jun  1 2018 09:13:39)",
        channels: list[FakeChannel] | None = None,
        btfu: str | None = "0,0,1000,1000,8501,8602,4501,4602",
        smartfront_type: str = "920-3305.36.ZU",
        smartbuttons: bool = True,
        sensors: dict[str, str] | None = None,
        websocket: bool = True,
        mac: str = "02:00:00:00:00:01",
        rssi: int = -60,
        time_scale: float = 1.0,
        notify_timeout: float = 30.0,
    ) -> None:
        self.serial = serial
        self.type = type
        self.sw_version = sw_version
        self.channels = channels if channels is not None else [FakeChannel(), FakeChannel()]
        for number, channel in enumerate(self.channels, start=1):
            if not channel.name:
                channel.name = f"zApp-{serial} ({number})"
        self.btfu = btfu
        self.smartfront_type = smartfront_type
        self.smartbuttons = smartbuttons
        self.sensors = (
            sensors if sensors is not None else {"temp": "24.50C", "lux": "none", "hum": "none"}
        )
        self.websocket_enabled = websocket
        self.mac = mac
        self.rssi = rssi
        self.time_scale = time_scale
        self.notify_timeout = notify_timeout

        #: Requests received: (method, path, body).
        self.requests: list[tuple[str, str, str]] = []
        #: Fault injection.
        self.fail_next: list[int] = []  # HTTP status codes to return for the next requests
        self.delay: float = 0.0
        self.malformed_xml: bool = False
        self.offline: bool = False

        self._clients: set[web.WebSocketResponse] = set()
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._changed = asyncio.Condition()
        self._runner: web.AppRunner | None = None
        self.port: int = 0

    # ------------------------------------------------------------- lifecycle

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def start(self, port: int = 0) -> None:
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._handle)
        self._runner = web.AppRunner(app, handle_signals=False)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", port)
        await site.start()
        server = site._server
        assert server is not None
        sockets = getattr(server, "sockets", None) or []
        self.port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        await self.drop_websockets()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def drop_websockets(self) -> None:
        """Close all WebSocket connections (simulates a network glitch)."""
        for ws in list(self._clients):
            await ws.close()
        self._clients.clear()

    # ----------------------------------------------------------- simulation

    def channel(self, number: int) -> FakeChannel:
        return self.channels[number - 1]

    async def set_value(self, number: int, value: int) -> None:
        """Change a channel value and notify listeners (only if it changed)."""
        channel = self.channel(number)
        if channel.value == value:
            return
        channel.value = value
        await self._broadcast({"eid1": {"ch": number, "val": value}})
        async with self._changed:
            self._changed.notify_all()

    async def press_key(self, key: int, *, hold: float = 0.0) -> None:
        """Press and release Smartfront key ``key`` (1-based), like a person at the wall."""
        bta = ["."] * 9
        bta[key - 1] = "P"
        await self._broadcast({"eid2": {"bta": "".join(bta)}})
        if hold:
            await asyncio.sleep(hold * self.time_scale)
        await self._broadcast({"eid2": {"bta": "." * 9}})
        if self.btfu:
            keys = parse_btfu(self.btfu)
            function = keys[key - 1] if key <= len(keys) else None
            if function is not None and function.kind == "channel" and function.direction:
                for number in sorted(function.channels):
                    if number <= len(self.channels):
                        await self._key_action(number, function.direction)

    async def _key_action(self, number: int, direction: str) -> None:
        channel = self.channel(number)
        if channel.is_cover:
            if channel.direction is not None and channel.direction != direction:
                await self._stop_motion(number)  # opposite key while moving = stop
            else:
                await self._start_cover(number, direction, None)
        else:
            await self._command(number, "on" if direction == "up" else "off")

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        frame = json.dumps(payload, separators=(",", ":"))
        for ws in list(self._clients):
            try:
                await ws.send_str(frame)
            except ConnectionError:
                self._clients.discard(ws)

    async def _command(self, number: int, cmd: str) -> bool:
        channel = self.channel(number)
        if cmd in ("on", "off", "toggle") and not channel.is_cover:
            await self._stop_motion(number, notify=False)
            on = {"on": True, "off": False, "toggle": channel.value == 0}[cmd]
            await self.set_value(number, 100 if on else 0)
        elif cmd == "stop":
            await self._stop_motion(number)
        elif cmd in ("open", "move_open", "close", "move_close") and channel.is_cover:
            await self._start_cover(number, "up" if "open" in cmd else "down", None)
        elif cmd in ("dim_up", "dim_down") and channel.is_dimmer:
            self._start_dim(number, "up" if cmd == "dim_up" else "down", None)
        elif match := _TIMED_RE.match(cmd):
            action, milliseconds = match[1], int(match[2])
            seconds = milliseconds / 1000
            if action.startswith("move") and channel.is_cover:
                await self._start_cover(number, "up" if "open" in action else "down", seconds)
            elif action.startswith("dim") and channel.is_dimmer:
                direction = {"dim_up": "up", "dim_down": "down"}.get(action)
                if direction is None:  # "dim_<t>": continue in the opposite direction
                    direction = "down" if channel.value >= 100 else "up"
                self._start_dim(number, direction, seconds)
            else:
                return False
        elif match := _SCENE_RE.match(cmd):
            action, scene = match[1], int(match[2])
            current = channel.position if channel.is_cover else float(channel.value)
            if action == "store":
                channel.scenes[scene] = current
            elif action == "delete":
                channel.scenes.pop(scene, None)
            elif scene in channel.scenes:
                target = channel.scenes[scene]
                if channel.is_cover:
                    direction = "up" if target > channel.position else "down"
                    seconds = abs(target - channel.position) / 100 * channel.travel_time
                    await self._start_cover(number, direction, seconds)
                else:
                    await self.set_value(number, round(target))
        else:
            return False
        return True

    async def _stop_motion(self, number: int, *, notify: bool = True) -> None:
        task = self._tasks.pop(number, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        channel = self.channel(number)
        was_moving = channel.direction is not None
        channel.direction = None
        if notify and channel.is_cover:
            await self.set_value(number, 0)
        elif notify and was_moving:
            await self._notify(number)

    async def _notify(self, number: int) -> None:
        """Push the current value of a channel, even if it was already updated silently."""
        await self._broadcast({"eid1": {"ch": number, "val": self.channel(number).value}})
        async with self._changed:
            self._changed.notify_all()

    async def _start_cover(self, number: int, direction: str, seconds: float | None) -> None:
        await self._stop_motion(number, notify=False)
        channel = self.channel(number)
        channel.direction = direction
        await self.set_value(number, 100)
        self._tasks[number] = asyncio.create_task(self._run_cover(number, direction, seconds))

    async def _run_cover(self, number: int, direction: str, seconds: float | None) -> None:
        channel = self.channel(number)
        step = 0.05
        elapsed = 0.0
        sign = 1 if direction == "up" else -1
        try:
            while True:
                await asyncio.sleep(step * self.time_scale)
                elapsed += step
                channel.position = min(
                    100.0, max(0.0, channel.position + sign * step / channel.travel_time * 100)
                )
                at_end = channel.position in (0.0, 100.0)
                if at_end or (seconds is not None and elapsed >= seconds - 1e-9):
                    break
        except asyncio.CancelledError:
            return
        self._tasks.pop(number, None)
        channel.direction = None
        await self.set_value(number, 0)

    def _start_dim(self, number: int, direction: str, seconds: float | None) -> None:
        task = self._tasks.pop(number, None)
        if task is not None:
            task.cancel()
        self.channel(number).direction = direction
        self._tasks[number] = asyncio.create_task(self._run_dim(number, direction, seconds))

    async def _run_dim(self, number: int, direction: str, seconds: float | None) -> None:
        """Ramp the value silently; the final value is pushed when dimming ends."""
        channel = self.channel(number)
        step = 0.05
        elapsed = 0.0
        level = float(channel.value)
        sign = 1 if direction == "up" else -1
        try:
            while True:
                await asyncio.sleep(step * self.time_scale)
                elapsed += step
                level = min(100.0, max(0.0, level + sign * step / channel.travel_time * 100))
                channel.value = round(level)
                if level in (0.0, 100.0) or (seconds is not None and elapsed >= seconds - 1e-9):
                    break
        except asyncio.CancelledError:
            return
        self._tasks.pop(number, None)
        channel.direction = None
        await self._notify(number)

    # ------------------------------------------------------------- responses

    def _xml(self, body: str, *, utf8: bool = False) -> web.Response:
        if self.malformed_xml:
            body = body.replace("</", "<", 1)
        header = _XML_UTF8 if utf8 else _XML_ASCII
        return web.Response(text=header + body, content_type="text/xml")

    def _json(self, data: Any) -> web.Response:
        return web.Response(
            text=json.dumps(data, separators=(",", ":")), content_type="application/json"
        )

    @staticmethod
    def _not_found() -> web.Response:
        return web.Response(status=404, text=_NOT_FOUND, content_type="text/html")

    def _id_xml(self) -> str:
        return (
            f"<id><hw>01.05.02</hw><sn>{self.serial}</sn><sys>ZEPTRION</sys>"
            f"<type>{self.type}</type><oen>zApp</oen><sw>{self.sw_version}</sw>"
            "<boot>01.04.02 (Apr 13 2018 10:08:29)</boot></id>"
        )

    def _chdes_xml(self, numbers: list[int]) -> str:
        parts = []
        for number in numbers:
            ch = self.channel(number)
            parts.append(
                f"<ch{number}><name>{escape(ch.name)}</name><group>{escape(ch.group)}</group>"
                f"<icon>{escape(ch.icon)}</icon><type>{escape(ch.type)}</type>"
                f"<cat>{escape(ch.category)}</cat></ch{number}>"
            )
        return "".join(parts)

    def _values_xml(self, root: str, numbers: list[int]) -> str:
        inner = "".join(f"<ch{n}><val>{self.channel(n).value}</val></ch{n}>" for n in numbers)
        return f"<{root}>{inner}</{root}>"

    def _channel_number(self, text: str) -> int | None:
        match = re.fullmatch(r"ch(\d+)", text)
        if match is None:
            return None
        number = int(match[1])
        return number if 1 <= number <= len(self.channels) else None

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        path = "/" + request.match_info["path"]
        body = await request.text() if request.can_read_body else ""
        self.requests.append((request.method, path, body))
        if self.offline:
            raise web.HTTPServiceUnavailable
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_next:
            return web.Response(status=self.fail_next.pop(0), text="error")

        if path == "/zrap/ws":
            if not self.websocket_enabled:
                return self._not_found()
            return await self._websocket(request)

        if request.method == "GET":
            return await self._get(path)
        if request.method == "POST":
            return await self._post(request, path, body)
        return web.Response(status=405)

    async def _get(self, path: str) -> web.Response:
        all_channels = list(range(1, len(self.channels) + 1))
        parts = path.strip("/").split("/")
        match parts:
            case ["zrap", "id"]:
                return self._xml(self._id_xml())
            case ["zrap", "chdes"]:
                return self._xml(f"<chdes>{self._chdes_xml(all_channels)}</chdes>", utf8=True)
            case ["zrap", "chdes", ch] if (n := self._channel_number(ch)) is not None:
                return self._xml(f"<chdes>{self._chdes_xml([n])}</chdes>", utf8=True)
            case ["zrap", "chscan"]:
                return self._xml(self._values_xml("chscan", all_channels))
            case ["zrap", "chscan", ch] if (n := self._channel_number(ch)) is not None:
                return self._xml(f"<ch{n}><val>{self.channel(n).value}</val></ch{n}>")
            case ["zrap", "chnotify"]:
                async with self._changed:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._changed.wait(), self.notify_timeout)
                return self._xml(self._values_xml("chnotify", all_channels))
            case ["zrap", "rssi"]:
                return self._xml(f"<rssi><dbm>{self.rssi}</dbm></rssi>")
            case ["zrap", "net", "mac"]:
                return self._xml(f"<mac>{self.mac}</mac>")
            case ["zapi", "smartfront", "id"] if self.btfu is not None:
                return self._json(
                    {
                        "btfu": self.btfu,
                        "sw": "01.01.08",
                        "hw": "01.00.00",
                        "sys": "ZEPTRION",
                        "sid": "1",
                        "type": self.smartfront_type,
                    }
                )
            case ["zapi", "smartfront", "sensors"] if self.btfu is not None:
                return self._json(self.sensors)
            case ["zapi", "smartfront", "led"] if self.btfu is not None:
                return self._json(
                    [
                        {
                            "id": i,
                            "on": False,
                            "rgb": "#000000",
                            "effect": "#fffff",
                            "bg": "#000020",
                        }
                        for i in range(1, 9)
                    ]
                )
            case ["zapi", "smartbt", "prgm"] if self.btfu is not None and self.smartbuttons:
                return self._json({"on": False, "ntm": 60})
        return self._not_found()

    async def _post(self, request: web.Request, path: str, body: str) -> web.Response:
        form = await request.post() if body and "json" not in request.content_type else {}
        parts = path.strip("/").split("/")
        match parts:
            case ["zrap", "chctrl", ch] if (n := self._channel_number(ch)) is not None:
                if not await self._command(n, str(form.get("cmd", ""))):
                    return web.Response(status=400, text="bad command")
            case ["zrap", "chctrl"]:
                for key, value in form.items():
                    number = self._channel_number(key.replace("cmd", "ch", 1))
                    if number is None or not await self._command(number, str(value)):
                        return web.Response(status=400, text="bad command")
            case ["zrap", "chdes", ch] if (n := self._channel_number(ch)) is not None:
                channel = self.channel(n)
                for key in ("name", "group", "icon", "type"):
                    if key in form:
                        setattr(channel, key, str(form[key]))
                if "cat" in form:
                    channel.category = str(form["cat"])
            case ["zapi", "smartfront", "led"] if self.btfu is not None:
                pass
            case _:
                return self._not_found()
        raise web.HTTPFound("/")

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._clients.add(ws)
        try:
            async for message in ws:
                if message.type == WSMsgType.ERROR:
                    break
        finally:
            self._clients.discard(ws)
        return ws
