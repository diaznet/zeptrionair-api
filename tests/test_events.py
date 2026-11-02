"""Event stream tests (WebSocket, chnotify fallback, reconnection, recorded frames)."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator

import pytest
from conftest import ws_frames

from zeptrionair_api import (
    ChannelUpdate,
    ConnectionEvent,
    Event,
    KeyEvent,
    ZeptrionConnectionError,
    ZeptrionDevice,
)
from zeptrionair_api.events import EventStream
from zeptrionair_api.parsing import parse_btfu
from zeptrionair_api.testing import FakeZeptrionDevice


async def next_matching(stream: AsyncIterator[Event], kind: type, timeout: float = 2.0) -> Event:
    async def find() -> Event:
        async for event in stream:
            if isinstance(event, kind):
                return event
        raise AssertionError("stream ended")

    return await asyncio.wait_for(find(), timeout)


async def take(stream: AsyncIterator[Event], count: int, timeout: float = 2.0) -> list[Event]:
    async def collect() -> list[Event]:
        out: list[Event] = []
        async for event in stream:
            out.append(event)
            if len(out) == count:
                break
        return out

    return await asyncio.wait_for(collect(), timeout)


async def test_websocket_stream(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    stream = client.events()
    try:
        assert await take(stream, 3) == [
            ConnectionEvent(True, "websocket"),
            ChannelUpdate(1, 0),
            ChannelUpdate(2, 0),
        ]
        await client.turn_on(2)
        assert await next_matching(stream, ChannelUpdate) == ChannelUpdate(2, 100)

        await fake.press_key(6)  # btfu 8602: ch1 down/off
        pressed = await next_matching(stream, KeyEvent)
        assert isinstance(pressed, KeyEvent)
        assert pressed.key == 6
        assert pressed.pressed
        assert pressed.function is not None
        assert pressed.function.direction == "down"
        released = await next_matching(stream, KeyEvent)
        assert isinstance(released, KeyEvent)
        assert not released.pressed

        await fake.press_key(5)  # ch1 on
        assert await next_matching(stream, ChannelUpdate) == ChannelUpdate(1, 100)
    finally:
        await stream.aclose()  # type: ignore[attr-defined]


async def test_stream_closes_websocket(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    stream = client.events()
    await take(stream, 1)
    assert len(fake._clients) == 1
    await stream.aclose()  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    assert len(fake._clients) == 0


async def test_reconnect_after_drop(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    stream = client.events()
    try:
        await take(stream, 3)
        await fake.drop_websockets()
        lost = await next_matching(stream, ConnectionEvent)
        assert lost == ConnectionEvent(False, "websocket", lost.error)  # type: ignore[union-attr]
        assert await next_matching(stream, ConnectionEvent) == ConnectionEvent(True, "websocket")
        fake.channels[0].value = 100  # changed while disconnected
        await client.turn_on(2)
        updates = [await next_matching(stream, ChannelUpdate) for _ in range(3)]
        assert ChannelUpdate(2, 100) in updates
    finally:
        await stream.aclose()  # type: ignore[attr-defined]


async def test_chnotify_fallback(fake: FakeZeptrionDevice) -> None:
    fake.websocket_enabled = False
    fake.notify_timeout = 0.2
    async with ZeptrionDevice(fake.host) as client:
        stream = client.events()
        try:
            assert await take(stream, 3) == [
                ConnectionEvent(True, "chnotify"),
                ChannelUpdate(1, 0),
                ChannelUpdate(2, 0),
            ]
            await asyncio.sleep(0.05)  # let the long poll start
            await client.turn_on(1)
            assert await next_matching(stream, ChannelUpdate) == ChannelUpdate(1, 100)
        finally:
            await stream.aclose()  # type: ignore[attr-defined]


async def test_chnotify_tolerates_bad_answers(fake: FakeZeptrionDevice) -> None:
    fake.websocket_enabled = False
    fake.notify_timeout = 0.05
    async with ZeptrionDevice(fake.host) as client:
        stream = client.events(transport="chnotify", keys=())
        try:
            await take(stream, 3)
            fake.malformed_xml = True  # chnotify answers become unparseable for a while
            await asyncio.sleep(0.2)
            fake.malformed_xml = False
            await client.turn_on(2)
            assert await next_matching(stream, ChannelUpdate) == ChannelUpdate(2, 100)
        finally:
            await stream.aclose()  # type: ignore[attr-defined]


async def test_websocket_only_mode_fails_without_websocket(fake: FakeZeptrionDevice) -> None:
    fake.websocket_enabled = False
    async with ZeptrionDevice(fake.host) as client:
        with pytest.raises(ZeptrionConnectionError, match="WebSocket"):
            await take(client.events(transport="websocket"), 1)


async def test_unreachable_without_reconnect() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    async with ZeptrionDevice(f"127.0.0.1:{port}") as client:
        # Windows takes ~2 s to refuse a connection to a closed localhost port (plus retries).
        with pytest.raises(ZeptrionConnectionError):
            await take(client.events(reconnect=False, keys=()), 1, timeout=15)
        with pytest.raises(ZeptrionConnectionError):
            await take(client.events(reconnect=False), 1, timeout=15)


async def test_unreachable_with_reconnect_keeps_trying() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    async with ZeptrionDevice(f"127.0.0.1:{port}") as client:
        stream = client.events()
        with pytest.raises(TimeoutError):
            await take(stream, 1, timeout=0.3)
        await stream.aclose()  # type: ignore[attr-defined]


async def test_liveness_check_detects_dead_device(
    client: ZeptrionDevice, fake: FakeZeptrionDevice
) -> None:
    stream = client.events(liveness_interval=0.05, reconnect=False)
    await take(stream, 3)
    await asyncio.sleep(0.12)  # idle long enough for one successful liveness check
    fake.offline = True
    lost = await next_matching(stream, ConnectionEvent)
    assert isinstance(lost, ConnectionEvent)
    assert not lost.connected
    assert lost.transport == "websocket"
    assert "503" in (lost.error or "")
    with contextlib.suppress(StopAsyncIteration):
        await asyncio.wait_for(stream.__anext__(), 1)


async def test_snapshot_failure_counts_as_disconnect(
    fake: FakeZeptrionDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with ZeptrionDevice(fake.host) as client:

        async def broken_states() -> dict[int, int | None]:
            raise ZeptrionConnectionError("chscan failed")

        monkeypatch.setattr(client, "states", broken_states)
        stream = client.events(keys=(), reconnect=False)
        events = []
        with contextlib.suppress(StopAsyncIteration):
            while True:
                events.append(await asyncio.wait_for(stream.__anext__(), 2))
        assert events[0] == ConnectionEvent(True, "websocket")
        assert events[-1].connected is False  # type: ignore[union-attr]


async def test_recorded_frames_are_decoded() -> None:
    """Replay real WebSocket frames through the decoder."""
    stream = EventStream(ZeptrionDevice("unused"), keys=parse_btfu("0,0,1000,1000,0,0,8501,8602"))
    decoded: list[Event] = []
    for line in ws_frames("cover_wall_down_up_selfstop"):
        decoded.extend(stream._decode(line["frame"]))
    updates = [e.value for e in decoded if isinstance(e, ChannelUpdate)]
    assert updates == [100, 0, 100, 0]  # motor running / stopped
    presses = [
        (e.key, e.function.direction)  # type: ignore[union-attr]
        for e in decoded
        if isinstance(e, KeyEvent) and e.pressed
    ]
    assert presses == [(8, "down"), (7, "up"), (7, "up")]

    assert stream._decode("not json") == []
    assert stream._decode('{"eid1": {"ch": "x", "val": 1}}') == []
    assert stream._decode('{"eid1": {"ch": 1, "val": -1}}') == [ChannelUpdate(1, None)]
    assert stream._decode('{"other": 1}') == []
