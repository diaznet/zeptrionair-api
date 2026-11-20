"""Smartbutton programming, and the 0.2 fake-device behaviour (scenes, slats, hold, LEDs)."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_device import wait_for

from zeptrionair_api import (
    SmartbuttonProgramTooLongError,
    ZeptrionDevice,
    ZeptrionNotSupportedError,
    channel_request,
)
from zeptrionair_api.smartbutton import compact, encode, merge, request_size
from zeptrionair_api.testing import FakeChannel, FakeZeptrionDevice


def test_channel_request() -> None:
    assert channel_request("192.168.1.164", 1, "recall_s1") == {
        "req": "POST",
        "loc": "192.168.1.164",
        "pth": "/zrap/chctrl",
        "bdy": "cmd1=recall_s1",
    }
    assert channel_request("10.0.0.2:8080", 2, "on")["prt"] == "8080"
    assert "prt" not in channel_request("http://zapp-1.local:80/", 2, "on")
    with pytest.raises(ValueError, match="invalid channel command"):
        channel_request("x", 1, "reboot")
    with pytest.raises(ValueError, match="channel number"):
        channel_request("x", 9, "on")


def test_merge_and_compact_follow_the_pdf_example() -> None:
    requests = [
        channel_request("192.168.1.164", 1, "recall_s1"),
        channel_request("192.168.1.185", 2, "on"),
        channel_request("192.168.1.185", 3, "off"),
    ]
    merged = merge(requests)
    assert [r["bdy"] for r in merged] == ["cmd1=recall_s1", "cmd2=on&cmd3=off"]
    # PDF 5.2.4.3 "equal but shorter": constant attributes left out
    assert compact(merged) == [
        {"req": "POST", "loc": "192.168.1.164", "pth": "/zrap/chctrl", "bdy": "cmd1=recall_s1"},
        {"loc": "192.168.1.185", "bdy": "cmd2=on&cmd3=off"},
    ]
    # Same channel twice is not merged (would be ambiguous).
    twice = merge([channel_request("h", 1, "on"), channel_request("h", 1, "off")])
    assert len(twice) == 2
    # An identical request keeps its body.
    same = compact([channel_request("h", 1, "on"), channel_request("h", 1, "on")])
    assert same[1] == {"bdy": "cmd1=on"}


def test_raw_requests_are_not_compacted_into_wrong_inheritance() -> None:
    hue = {"typ": "application/json", "req": "PUT", "loc": "h", "pth": "/x", "bdy": "{}"}
    after = channel_request("z", 1, "on")
    result = compact([hue, after])
    assert result[1] == after  # lacks "typ": written in full
    with pytest.raises(ValueError, match="unknown"):
        merge([{"loc": "h", "foo": "bar"}])
    with pytest.raises(ValueError, match="at least one"):
        encode([])


def test_size_limit() -> None:
    body = encode([channel_request(f"zapp-1234567{i}.local", 1, "recall_s1") for i in range(3)])
    assert request_size("zapp-12345678.local", body) < 730
    many = [channel_request(f"zapp-123456{i:02d}.local", 1, "move_close_12345") for i in range(25)]
    assert request_size("zapp-12345678.local", encode(many)) > 730
    assert json.loads(body)[0]["req"] == "POST"


async def test_program_smartbutton() -> None:
    async with (
        FakeZeptrionDevice(time_scale=0.05) as fake,
        ZeptrionDevice(fake.host) as client,
    ):
        assert await client.supports_smartbutton_programming()

        async def press_soon() -> None:
            await wait_for(lambda: fake.programming["on"])
            await fake.press_key(3)

        presser = asyncio.create_task(press_soon())
        requests = [channel_request("10.0.0.5", 1, "toggle"), channel_request("10.0.0.5", 2, "on")]
        assert await client.program_smartbutton(requests, wait=30)
        await presser
        assert fake.smartbutton_programs[3] == [
            {"req": "POST", "loc": "10.0.0.5", "pth": "/zrap/chctrl", "bdy": "cmd1=toggle&cmd2=on"}
        ]
        assert fake.programming["on"] is False  # programming mode switched off again

        # Nobody presses: False, nothing stored.
        assert not await client.program_smartbutton(requests, wait=10)
        assert set(fake.smartbutton_programs) == {3}


async def test_program_smartbutton_errors() -> None:
    async with (
        FakeZeptrionDevice(smartbuttons=False) as fake,
        ZeptrionDevice(fake.host) as client,
    ):
        requests = [channel_request("10.0.0.5", 1, "toggle")]
        with pytest.raises(ZeptrionNotSupportedError):
            await client.program_smartbutton(requests)
        too_long = [
            channel_request(f"zapp-123456{i:02d}.local", 1, "move_close_12345") for i in range(25)
        ]
        with pytest.raises(SmartbuttonProgramTooLongError):
            await client.program_smartbutton(too_long)
        with pytest.raises(ValueError, match="wait"):
            await client.program_smartbutton(requests, wait=5)
        assert not [r for r in fake.requests if r[0] == "POST"]  # nothing was sent


async def test_fake_ignores_commands_towards_the_end_stop() -> None:
    async with (
        FakeZeptrionDevice(channels=[FakeChannel("5", travel_time=1)], time_scale=0.1) as fake,
        ZeptrionDevice(fake.host) as client,
    ):
        await client.open(1)  # already fully open
        await asyncio.sleep(0.05)
        assert fake.channels[0].value == 0
        assert fake.channels[0].direction is None


async def test_fake_scene_replay_from_the_stored_end() -> None:
    async with (
        FakeZeptrionDevice(channels=[FakeChannel("5", travel_time=1)], time_scale=0.1) as fake,
        ZeptrionDevice(fake.host) as client,
    ):
        channel = fake.channels[0]
        await client.move_close(1, 400)  # from the top: 40 % down
        await wait_for(lambda: channel.value == 0)
        await client.store_scene(1, 2)
        assert channel.cover_scenes[2] == ("up", pytest.approx(0.4, abs=0.02))
        await client.open(1)
        await wait_for(lambda: channel.value == 0 and channel.position == 100.0)
        # From the stored end stop: a single run for the net time.
        await client.recall_scene(1, 2)
        await wait_for(lambda: channel.value == 100)
        await wait_for(lambda: channel.value == 0)
        assert channel.position == pytest.approx(60, abs=3)
        # A command cancels a running replay.
        await client.close_cover(1)
        await wait_for(lambda: channel.value == 0)
        await client.recall_scene(1, 2)
        await asyncio.sleep(0.01)
        await client.stop(1)
        await asyncio.sleep(0.1)
        assert 2 not in fake._scene_tasks


async def test_fake_slats_turn_first() -> None:
    async with (
        FakeZeptrionDevice(
            channels=[FakeChannel("5", travel_time=10, tilt_time=1)], time_scale=0.1
        ) as fake,
        ZeptrionDevice(fake.host) as client,
    ):
        channel = fake.channels[0]
        await client.move_close(1, 500)  # half of the tilt time: slats half closed, no travel
        await wait_for(lambda: channel.value == 0)
        assert channel.tilt == pytest.approx(50, abs=5)
        assert channel.position == 100.0
        await client.move_close(1, 1500)  # rest of the tilt, then 1 s of travel = 10 %
        await wait_for(lambda: channel.value == 0)
        assert channel.tilt == 0.0
        assert channel.position == pytest.approx(90, abs=2)
        await client.move_open(1, 300)  # change of direction: slats open first
        await wait_for(lambda: channel.value == 0)
        assert channel.tilt == pytest.approx(30, abs=5)
        assert channel.position == pytest.approx(90, abs=2)


async def test_fake_hold_to_move() -> None:
    async with FakeZeptrionDevice(
        channels=[FakeChannel("5", travel_time=10)],
        btfu="0,0,1000,1000,0,0,8501,8602",
        hold_to_move=True,
        time_scale=0.1,
    ) as fake:
        channel = fake.channels[0]
        await fake.press_key(8, hold=2)  # held 2 s: moves 20 % down, stops on release
        assert channel.value == 0
        assert channel.position == pytest.approx(80, abs=3)


async def test_fake_leds() -> None:
    async with FakeZeptrionDevice() as fake, ZeptrionDevice(fake.host) as client:
        await client.set_led_backgrounds({3: "#ff0000"})
        leds = await client.leds()
        assert leds is not None
        assert leds[2].background == "#ff0000"
