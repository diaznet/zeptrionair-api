"""Real-time event stream for one device (WebSocket with ``chnotify`` long-poll fallback)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import TYPE_CHECKING, Literal

import aiohttp

from . import parsing
from .exceptions import ZeptrionConnectionError, ZeptrionError, ZeptrionParseError
from .models import ChannelUpdate, ConnectionEvent, Event, KeyFunction, Transport

if TYPE_CHECKING:
    from .device import ZeptrionDevice

_LOGGER = logging.getLogger(__name__)

BACKOFF_MIN = 1.0
BACKOFF_MAX = 60.0
CHNOTIFY_TIMEOUT = 45.0  # the device answers after at most 30 s


class _DisconnectedError(Exception):
    """Internal: the current transport stopped working."""


class EventStream:
    """Async iterator over :class:`~zeptrionair_api.models.Event` objects.

    - ``transport="auto"`` uses the WebSocket ``/zrap/ws``. If the device refuses the WebSocket
      handshake (older firmware), it switches to the ``/zrap/chnotify`` long poll.
    - When a connection is established, a :class:`ConnectionEvent` (``connected=True``) is
      emitted, followed by a :class:`ChannelUpdate` for every channel (fresh ``chscan``), so
      consumers can resynchronise.
    - When the connection is lost, ``ConnectionEvent(connected=False)`` is emitted. With
      ``reconnect=True`` (default), it reconnects with exponential backoff (1 s to 60 s, with
      jitter); otherwise the iterator ends.
    - The device sends nothing while idle, so if no frame arrives for ``liveness_interval``
      seconds, a cheap HTTP request checks that the device is still there.
    - Key events are decoded with ``keys`` (from :meth:`ZeptrionDevice.smartfront`); if not
      given, the Smartfront is read once when the stream starts.
    """

    def __init__(
        self,
        device: ZeptrionDevice,
        *,
        transport: Literal["auto", "websocket", "chnotify"] = "auto",
        keys: Sequence[KeyFunction] | None = None,
        reconnect: bool = True,
        liveness_interval: float = 60.0,
    ) -> None:
        self._device = device
        self._mode = transport
        self._keys = keys
        self._reconnect = reconnect
        self._liveness = liveness_interval
        self._tracker = parsing.KeyStateTracker(keys or ())
        self._states: dict[int, int | None] = {}

    def __aiter__(self) -> AsyncIterator[Event]:
        return self._run()

    async def _run(self) -> AsyncGenerator[Event]:
        backoff = BACKOFF_MIN
        use_websocket = self._mode != "chnotify"
        keys_loaded = self._keys is not None
        while True:
            transport: Transport = "websocket" if use_websocket else "chnotify"
            connected = False
            try:
                if not keys_loaded:
                    keys_loaded = await self._load_keys()
                stream = self._websocket() if use_websocket else self._chnotify()
                async with contextlib.aclosing(stream):
                    async for event in stream:
                        if isinstance(event, ConnectionEvent):
                            connected = True
                            backoff = BACKOFF_MIN
                        yield event
            except _WebSocketUnsupportedError:
                if self._mode == "websocket":
                    raise ZeptrionConnectionError(
                        f"{self._device.host} does not support the WebSocket"
                    ) from None
                _LOGGER.info("%s: WebSocket not supported, using chnotify", self._device.host)
                use_websocket = False
                continue
            except _DisconnectedError as err:
                if connected:
                    yield ConnectionEvent(False, transport, str(err.__cause__ or err))
                elif not self._reconnect:
                    raise ZeptrionConnectionError(str(err.__cause__ or err)) from err
                _LOGGER.debug("%s: %s stream lost: %s", self._device.host, transport, err)
            if not self._reconnect:
                return
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
            backoff = min(BACKOFF_MAX, backoff * 2)

    async def _load_keys(self) -> bool:
        try:
            smartfront = await self._device.smartfront()
        except ZeptrionError as err:
            raise _DisconnectedError("cannot read Smartfront") from err
        if smartfront is not None:
            self._tracker.set_keys(smartfront.keys)
        return True

    async def _snapshot(self) -> list[ChannelUpdate]:
        try:
            states = await self._device.states()
        except ZeptrionError as err:
            raise _DisconnectedError("cannot read channel states") from err
        self._states = dict(states)
        return [ChannelUpdate(channel, value) for channel, value in sorted(states.items())]

    async def _websocket(self) -> AsyncGenerator[Event]:
        device = self._device
        try:
            ws = await device.session.ws_connect(
                device.websocket_url, timeout=aiohttp.ClientWSTimeout(ws_close=5)
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (400, 404, 405, 426, 501):
                raise _WebSocketUnsupportedError from err
            raise _DisconnectedError("WebSocket handshake failed") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _DisconnectedError("cannot connect WebSocket") from err

        try:
            self._tracker.reset()
            yield ConnectionEvent(True, "websocket")
            for update in await self._snapshot():
                yield update
            while True:
                try:
                    message = await ws.receive(timeout=self._liveness)
                except TimeoutError:
                    await self._check_alive()
                    continue
                if message.type == aiohttp.WSMsgType.TEXT:
                    for event in self._decode(message.data):
                        yield event
                elif message.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    raise _DisconnectedError(f"WebSocket closed ({message.type.name})")
        finally:
            await ws.close()

    async def _check_alive(self) -> None:
        try:
            await self._device.rssi()
        except ZeptrionError as err:
            raise _DisconnectedError("device stopped answering") from err

    def _decode(self, frame: str) -> list[Event]:
        try:
            data = parsing.parse_ws_frame(frame)
        except ZeptrionParseError:
            _LOGGER.debug("%s: ignoring frame %r", self._device.host, frame)
            return []
        events: list[Event] = []
        eid1 = data.get("eid1")
        if isinstance(eid1, dict) and "ch" in eid1:
            try:
                channel = int(eid1["ch"])
            except (TypeError, ValueError):
                return []
            value = parsing.parse_value(str(eid1.get("val", "")))
            self._states[channel] = value
            events.append(ChannelUpdate(channel, value))
        eid2 = data.get("eid2")
        if isinstance(eid2, dict) and isinstance(eid2.get("bta"), str):
            events.extend(self._tracker.update(eid2["bta"]))
        return events

    async def _chnotify(self) -> AsyncGenerator[Event]:
        yield ConnectionEvent(True, "chnotify")
        for update in await self._snapshot():
            yield update
        while True:
            try:
                body = await self._device.request(
                    "GET", "/zrap/chnotify", timeout=CHNOTIFY_TIMEOUT, limit_concurrency=False
                )
                states = parsing.parse_states(body)
            except ZeptrionParseError as err:
                _LOGGER.debug("%s: bad chnotify answer: %s", self._device.host, err)
                await self._check_alive()
                continue
            except ZeptrionError as err:
                raise _DisconnectedError("chnotify failed") from err
            for channel, value in sorted(states.items()):
                if self._states.get(channel, -2) != value:
                    self._states[channel] = value
                    yield ChannelUpdate(channel, value)


class _WebSocketUnsupportedError(Exception):
    """Internal: the device answered the WebSocket handshake with an error status."""
