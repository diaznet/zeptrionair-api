"""ZeptrionDevice against the simulated device."""

from __future__ import annotations

import asyncio
import socket

import aiohttp
import pytest

from zeptrionair_api import (
    Category,
    Command,
    ZeptrionConnectionError,
    ZeptrionDevice,
    ZeptrionNotFoundError,
    ZeptrionResponseError,
    ZeptrionTimeoutError,
)
from zeptrionair_api.testing import FakeChannel, FakeZeptrionDevice


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def wait_for(condition: object, timeout: float = 2.0) -> None:
    async def poll() -> None:
        while not condition():  # type: ignore[operator]
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def test_reads(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    info = await client.identify()
    assert info.serial == "99990001"
    assert info.firmware == (1, 8, 28)

    channels = await client.channels()
    assert [(c.number, c.category, c.name) for c in channels] == [
        (1, Category.ONOFF, "Light 1"),
        (2, Category.ONOFF, "zApp-99990001 (2)"),
    ]
    assert channels[0].group == "Room A"
    assert await client.states() == {1: 0, 2: 0}
    fake.channels[1].value = 100
    assert await client.state(2) == 100
    assert await client.rssi() == -60
    assert await client.mac() == "02:00:00:00:00:01"

    front = await client.smartfront()
    assert front is not None
    assert front.key(5).direction == "up"  # type: ignore[union-attr]
    sensors = await client.sensors()
    assert sensors is not None
    assert sensors.temperature == pytest.approx(24.5)
    leds = await client.leds()
    assert leds is not None
    assert len(leds) == 8


async def test_probe(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    caps = await client.probe()
    assert caps.websocket
    assert caps.smartfront
    assert caps.smartbuttons
    assert caps.sensors

    fake.websocket_enabled = False
    fake.smartbuttons = False
    fake.sensors = {"temp": "none", "lux": "none", "hum": "none"}
    caps = await client.probe()
    assert not caps.websocket
    assert caps.smartfront
    assert not caps.smartbuttons
    assert not caps.sensors


async def test_without_smartfront() -> None:
    async with FakeZeptrionDevice(btfu=None) as fake, ZeptrionDevice(fake.host) as client:
        assert await client.smartfront() is None
        assert await client.sensors() is None
        assert await client.leds() is None
        caps = await client.probe()
        assert not caps.smartfront
        assert not caps.sensors
        # the singular, documented path is tried as a fallback
        assert ("GET", "/zapi/smartfront/sensor", "") in fake.requests


async def test_light_commands(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    await client.turn_on(1)
    assert fake.channels[0].value == 100
    assert fake.requests[-1] == ("POST", "/zrap/chctrl/ch1", "cmd=on")
    await client.toggle(1)
    assert fake.channels[0].value == 0
    await client.send(2, Command.ON)
    await client.turn_off(2)
    assert fake.channels[1].value == 0


async def test_send_many(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    await client.send_many({2: "on", 1: Command.ON})
    assert fake.requests[-1] == ("POST", "/zrap/chctrl", "cmd1=on&cmd2=on")
    assert [c.value for c in fake.channels] == [100, 100]
    count = len(fake.requests)
    await client.send_many({})
    assert len(fake.requests) == count


async def test_invalid_commands_are_not_sent(
    client: ZeptrionDevice, fake: FakeZeptrionDevice
) -> None:
    with pytest.raises(ValueError, match="invalid channel command"):
        await client.send(1, "reboot")
    with pytest.raises(ValueError, match="channel number"):
        await client.send(9, "on")
    with pytest.raises(ValueError, match="duration"):
        await client.move_open(1, 50)
    with pytest.raises(ValueError, match="scene"):
        await client.recall_scene(1, 5)
    assert not [r for r in fake.requests if r[0] == "POST"]


async def test_device_rejects_command(client: ZeptrionDevice) -> None:
    # "open" is valid syntax but the fake (like a device) rejects it for a light channel.
    with pytest.raises(ZeptrionResponseError) as err:
        await client.open(1)
    assert err.value.status == 400


async def test_cover_commands(fake_cover: FakeZeptrionDevice) -> None:
    async with ZeptrionDevice(fake_cover.host) as client:
        channel = fake_cover.channels[0]
        await client.close_cover(1)
        assert channel.value == 100  # motor running
        await wait_for(lambda: channel.value == 0)
        assert channel.position == 0.0

        await client.move_open(1, 2000)
        await wait_for(lambda: channel.value == 0)
        assert channel.position == pytest.approx(20.0, abs=1.0)

        await client.move_open(1)
        await asyncio.sleep(0.02)
        await client.stop(1)
        assert channel.value == 0
        assert 20 < channel.position < 100

        await client.store_scene(1, 1)
        await client.open(1)
        await wait_for(lambda: channel.value == 0)
        assert channel.position == 100.0
        await client.recall_scene(1, 1)
        await wait_for(lambda: channel.value == 0)
        assert 20 < channel.position < 100
        await client.delete_scene(1, 1)
        await client.move_close(1, 100)
        await wait_for(lambda: channel.value == 0)


async def test_dimmer_commands() -> None:
    async with (
        FakeZeptrionDevice(channels=[FakeChannel("3", travel_time=1)], time_scale=0.1) as fake,
        ZeptrionDevice(fake.host) as client,
    ):
        channel = fake.channels[0]
        await client.dim_up(1, 500)
        await wait_for(lambda: channel.direction is None)
        assert channel.value == pytest.approx(50, abs=6)
        await client.dim_down(1)
        await asyncio.sleep(0.02)
        await client.stop(1)
        assert channel.direction is None
        await client.send(1, "dim_1000")
        await wait_for(lambda: channel.direction is None)
        await client.dim_up(1)
        await client.turn_on(1)
        assert channel.value == 100


async def test_channel_description(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    await client.set_channel_description(1, name="Küche <Spots & Lampe>", group="EG")
    assert fake.channels[0].name == "Küche <Spots & Lampe>"
    channels = await client.channels()
    assert channels[0].name == "Küche <Spots & Lampe>"
    assert channels[0].group == "EG"
    await client.set_channel_description(1)
    with pytest.raises(ValueError, match="longer than 32 bytes"):
        await client.set_channel_description(1, name="ü" * 17)
    with pytest.raises(ValueError, match="unknown channel field"):
        await client.set_channel_description(1, colour="red")


async def test_led_backgrounds(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    await client.set_led_backgrounds({4: "#002200", 2: "#220000"})
    method, path, sent = fake.requests[-1]
    assert (method, path) == ("POST", "/zapi/smartfront/led")
    assert sent == '[{"id": 2, "bg": "#220000"}, {"id": 4, "bg": "#002200"}]'


async def test_not_found(client: ZeptrionDevice) -> None:
    with pytest.raises(ZeptrionNotFoundError) as err:
        await client.request("GET", "/zrap/nothing")
    assert err.value.status == 404
    assert "404" in err.value.body


async def test_get_is_retried_on_server_error(
    client: ZeptrionDevice, fake: FakeZeptrionDevice
) -> None:
    fake.fail_next = [500, 503]
    assert await client.rssi() == -60
    fake.fail_next = [500, 500, 500]
    with pytest.raises(ZeptrionResponseError):
        await client.rssi()
    fake.fail_next = [400]
    with pytest.raises(ZeptrionResponseError):
        await client.rssi()


async def test_post_is_never_retried(client: ZeptrionDevice, fake: FakeZeptrionDevice) -> None:
    fake.fail_next = [500]
    with pytest.raises(ZeptrionResponseError):
        await client.toggle(1)
    assert len([r for r in fake.requests if r[0] == "POST"]) == 1
    assert fake.channels[0].value == 0


async def test_connection_errors() -> None:
    async with ZeptrionDevice(f"127.0.0.1:{_free_port()}") as client:
        with pytest.raises(ZeptrionConnectionError):
            await client.identify()
        with pytest.raises(ZeptrionConnectionError):
            await client.supports_websocket()


async def test_timeout(fake: FakeZeptrionDevice) -> None:
    fake.delay = 0.5
    async with ZeptrionDevice(fake.host, request_timeout=0.05) as client:
        with pytest.raises(ZeptrionTimeoutError):
            await client.turn_on(1)


async def test_session_handling(fake: FakeZeptrionDevice) -> None:
    async with aiohttp.ClientSession() as session:
        client = ZeptrionDevice(f"http://{fake.host}/", session=session)
        assert client.host == fake.host
        assert client.base_url == f"http://{fake.host}"
        assert client.websocket_url == f"ws://{fake.host}/zrap/ws"
        assert repr(client) == f"ZeptrionDevice({fake.host!r})"
        await client.identify()
        await client.close()
        assert not session.closed  # a shared session is never closed by the client

    owned = ZeptrionDevice(fake.host)
    await owned.identify()
    session = owned.session
    await owned.close()
    assert session.closed
    await owned.close()

    with pytest.raises(ValueError, match="host"):
        ZeptrionDevice("  ")
