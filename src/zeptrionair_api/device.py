"""HTTP client for one zeptrionAir device."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from types import TracebackType
from typing import Any, Literal, Self

import aiohttp

from . import parsing
from .const import (
    CHANNEL_FIELD_LIMITS,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    DEFAULT_REQUEST_TIMEOUT,
    Command,
    TimedAction,
    scene_command,
    timed_command,
    validate_command,
)
from .exceptions import (
    ZeptrionConnectionError,
    ZeptrionError,
    ZeptrionNotFoundError,
    ZeptrionResponseError,
    ZeptrionTimeoutError,
)
from .models import (
    Capabilities,
    Channel,
    DeviceInfo,
    Event,
    KeyFunction,
    LedState,
    SensorValues,
    SmartfrontInfo,
)

_LOGGER = logging.getLogger(__name__)

_GET_RETRIES = 2
_RETRY_DELAY = 0.5


class ZeptrionDevice:
    """Client for one zApp device.

    ``host`` is a hostname or IP address, optionally with ``:port``. Pass an existing
    ``aiohttp.ClientSession`` to share connections (recommended in Home Assistant). If no session
    is given, one is created and closed by :meth:`close` / ``async with``.
    """

    def __init__(
        self,
        host: str,
        *,
        session: aiohttp.ClientSession | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
    ) -> None:
        host = host.strip().removeprefix("http://").rstrip("/")
        if not host:
            raise ValueError("host must not be empty")
        self._host = host
        self._session = session
        self._own_session = session is None
        self._timeout = request_timeout
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

    # ------------------------------------------------------------------ basics

    @property
    def host(self) -> str:
        return self._host

    @property
    def base_url(self) -> str:
        return f"http://{self._host}"

    @property
    def websocket_url(self) -> str:
        return f"ws://{self._host}/zrap/ws"

    def __repr__(self) -> str:
        return f"ZeptrionDevice({self._host!r})"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the HTTP session if it was created by this object."""
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    # ---------------------------------------------------------------- requests

    async def request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        data: Mapping[str, str] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
        limit_concurrency: bool = True,
    ) -> str:
        """Send a request and return the response body.

        GET requests are retried on connection errors and 5xx responses. POST requests are never
        retried, because commands such as ``toggle`` are not idempotent. A 302 answer to a POST
        is the device's normal success response.
        """
        attempts = 1 + (_GET_RETRIES if method == "GET" else 0)
        for attempt in range(attempts):
            try:
                if limit_concurrency:
                    async with self._semaphore:
                        return await self._request_once(method, path, data, json_body, timeout)
                return await self._request_once(method, path, data, json_body, timeout)
            except (ZeptrionConnectionError, ZeptrionResponseError) as err:
                retryable = isinstance(err, ZeptrionConnectionError) or (
                    isinstance(err, ZeptrionResponseError) and err.status >= 500
                )
                if not retryable or attempt == attempts - 1:
                    raise
                _LOGGER.debug("%s %s failed (%s), retrying", method, path, err)
                await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover

    async def _request_once(
        self,
        method: str,
        path: str,
        data: Mapping[str, str] | None,
        json_body: Any,
        timeout: float | None,
    ) -> str:
        url = f"{self.base_url}{path}"
        client_timeout = aiohttp.ClientTimeout(total=timeout or self._timeout)
        try:
            async with self.session.request(
                method,
                url,
                data=dict(data) if data is not None else None,
                json=json_body,
                allow_redirects=False,
                timeout=client_timeout,
            ) as response:
                body = await response.text(errors="replace")
                status = response.status
        except TimeoutError as err:
            raise ZeptrionTimeoutError(f"timeout talking to {self._host} ({path})") from err
        except aiohttp.ClientError as err:
            raise ZeptrionConnectionError(f"cannot reach {self._host}: {err}") from err

        if status == 404:
            raise ZeptrionNotFoundError(status, path, body)
        if status >= 400 or (method == "GET" and status != 200):
            raise ZeptrionResponseError(status, path, body)
        return body

    async def _get(self, path: str) -> str:
        return await self.request("GET", path)

    async def _get_optional(self, path: str) -> str | None:
        try:
            return await self._get(path)
        except ZeptrionNotFoundError:
            return None

    # ------------------------------------------------------------------- reads

    async def identify(self) -> DeviceInfo:
        """Read the device identification (``/zrap/id``)."""
        return parsing.parse_device_info(await self._get("/zrap/id"))

    async def channels(self) -> list[Channel]:
        """Read all channel descriptors (``/zrap/chdes``), including unused channels."""
        return parsing.parse_channels(await self._get("/zrap/chdes"))

    async def states(self) -> dict[int, int | None]:
        """Read the value of every channel (``/zrap/chscan``)."""
        return parsing.parse_states(await self._get("/zrap/chscan"))

    async def state(self, channel: int) -> int | None:
        """Read the value of one channel."""
        self._check_channel(channel)
        states = parsing.parse_states(await self._get(f"/zrap/chscan/ch{channel}"))
        return states.get(channel)

    async def rssi(self) -> int:
        """Read the Wi-Fi signal strength in dBm (``/zrap/rssi``)."""
        return parsing.parse_rssi(await self._get("/zrap/rssi"))

    async def mac(self) -> str:
        """Read the device MAC address (``/zrap/net/mac`` only, never the full ``/zrap/net``)."""
        return parsing.parse_mac(await self._get("/zrap/net/mac"))

    async def smartfront(self) -> SmartfrontInfo | None:
        """Read the Smartfront identification, or ``None`` if no Smartfront is fitted."""
        body = await self._get_optional("/zapi/smartfront/id")
        return parsing.parse_smartfront(body) if body is not None else None

    async def sensors(self) -> SensorValues | None:
        """Read the Smartfront sensors, or ``None`` if not available."""
        body = await self._get_optional("/zapi/smartfront/sensors")
        if body is None:  # the official documentation uses the singular form
            body = await self._get_optional("/zapi/smartfront/sensor")
        return parsing.parse_sensors(body) if body is not None else None

    async def leds(self) -> list[LedState] | None:
        """Read the Smartfront LEDs, or ``None`` if no Smartfront is fitted."""
        body = await self._get_optional("/zapi/smartfront/led")
        return parsing.parse_leds(body) if body is not None else None

    async def probe(self) -> Capabilities:
        """Detect optional features: WebSocket, Smartfront, Smartbutton programming, sensors."""
        smartfront = await self._get_optional("/zapi/smartfront/id") is not None
        smartbuttons = smartfront and await self._get_optional("/zapi/smartbt/prgm") is not None
        sensors = False
        if smartfront:
            values = await self.sensors()
            sensors = values is not None and any(
                value is not None
                for value in (values.temperature, values.illuminance, values.humidity)
            )
        return Capabilities(
            websocket=await self.supports_websocket(),
            smartfront=smartfront,
            smartbuttons=smartbuttons,
            sensors=sensors,
        )

    async def supports_websocket(self) -> bool:
        """Return ``True`` if the device accepts a WebSocket connection on ``/zrap/ws``."""
        try:
            async with self.session.ws_connect(
                self.websocket_url, timeout=aiohttp.ClientWSTimeout(ws_close=2)
            ) as ws:
                await ws.close()
        except aiohttp.WSServerHandshakeError:
            return False
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ZeptrionConnectionError(f"cannot reach {self._host}: {err}") from err
        return True

    # ------------------------------------------------------------------ writes

    @staticmethod
    def _check_channel(channel: int) -> None:
        if not 1 <= channel <= 8:
            raise ValueError(f"invalid channel number {channel}")

    async def send(self, channel: int, command: Command | str) -> None:
        """Send one command to one channel (``POST /zrap/chctrl/chN``)."""
        self._check_channel(channel)
        cmd = validate_command(str(command))
        await self.request("POST", f"/zrap/chctrl/ch{channel}", data={"cmd": cmd})

    async def send_many(self, commands: Mapping[int, Command | str]) -> None:
        """Send commands to several channels in one request (``POST /zrap/chctrl``)."""
        if not commands:
            return
        data = {}
        for channel, command in sorted(commands.items()):
            self._check_channel(channel)
            data[f"cmd{channel}"] = validate_command(str(command))
        await self.request("POST", "/zrap/chctrl", data=data)

    async def set_channel_description(self, channel: int, **fields: str) -> None:
        """Update channel descriptor fields (``name``, ``group``, ``icon``, ``type``, ``cat``)."""
        self._check_channel(channel)
        if not fields:
            return
        for key, value in fields.items():
            limit = CHANNEL_FIELD_LIMITS.get(key)
            if limit is None:
                raise ValueError(f"unknown channel field {key!r}")
            if len(value.encode("utf-8")) > limit:
                raise ValueError(f"{key} is longer than {limit} bytes (UTF-8)")
        await self.request("POST", f"/zrap/chdes/ch{channel}", data=fields)

    async def set_led_backgrounds(self, colors: Mapping[int, str]) -> None:
        """Set Smartfront LED background colours (``{led_id: "#rrggbb"}``)."""
        payload = [{"id": led, "bg": color} for led, color in sorted(colors.items())]
        await self.request("POST", "/zapi/smartfront/led", json_body=payload)

    # ------------------------------------------------------------- convenience

    async def turn_on(self, channel: int) -> None:
        await self.send(channel, Command.ON)

    async def turn_off(self, channel: int) -> None:
        await self.send(channel, Command.OFF)

    async def toggle(self, channel: int) -> None:
        await self.send(channel, Command.TOGGLE)

    async def stop(self, channel: int) -> None:
        await self.send(channel, Command.STOP)

    async def open(self, channel: int) -> None:
        await self.send(channel, Command.OPEN)

    async def close_cover(self, channel: int) -> None:
        await self.send(channel, Command.CLOSE)

    async def move_open(self, channel: int, milliseconds: int | None = None) -> None:
        await self._timed(channel, "move_open", Command.MOVE_OPEN, milliseconds)

    async def move_close(self, channel: int, milliseconds: int | None = None) -> None:
        await self._timed(channel, "move_close", Command.MOVE_CLOSE, milliseconds)

    async def dim_up(self, channel: int, milliseconds: int | None = None) -> None:
        await self._timed(channel, "dim_up", Command.DIM_UP, milliseconds)

    async def dim_down(self, channel: int, milliseconds: int | None = None) -> None:
        await self._timed(channel, "dim_down", Command.DIM_DOWN, milliseconds)

    async def recall_scene(self, channel: int, scene: int) -> None:
        await self.send(channel, scene_command("recall", scene))

    async def store_scene(self, channel: int, scene: int) -> None:
        await self.send(channel, scene_command("store", scene))

    async def delete_scene(self, channel: int, scene: int) -> None:
        await self.send(channel, scene_command("delete", scene))

    async def _timed(
        self, channel: int, action: TimedAction, plain: Command, milliseconds: int | None
    ) -> None:
        command = plain.value if milliseconds is None else timed_command(action, milliseconds)
        await self.send(channel, command)

    # ------------------------------------------------------------------ events

    def events(
        self,
        *,
        transport: Literal["auto", "websocket", "chnotify"] = "auto",
        keys: Sequence[KeyFunction] | None = None,
        reconnect: bool = True,
        liveness_interval: float = 60.0,
    ) -> AsyncIterator[Event]:
        """Stream events from the device until the iterator is closed.

        See :class:`zeptrionair_api.events.EventStream` for details.
        """
        from .events import EventStream

        return EventStream(
            self,
            transport=transport,
            keys=keys,
            reconnect=reconnect,
            liveness_interval=liveness_interval,
        ).__aiter__()


__all__ = ["ZeptrionDevice", "ZeptrionError"]
