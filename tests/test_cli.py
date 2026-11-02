"""CLI tests. The fake device runs in a background thread because the CLI calls asyncio.run()."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from zeptrionair_api import DiscoveredDevice
from zeptrionair_api.cli import app
from zeptrionair_api.testing import FakeChannel, FakeZeptrionDevice

runner = CliRunner()


class ThreadedFake:
    def __init__(self, fake: FakeZeptrionDevice) -> None:
        self.fake = fake
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self) -> FakeZeptrionDevice:
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self.fake.start(), self.loop).result(5)
        return self.fake

    def __exit__(self, *args: object) -> None:
        asyncio.run_coroutine_threadsafe(self.fake.stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)
        self.loop.close()

    def call(self, coro: object) -> None:
        asyncio.run_coroutine_threadsafe(coro, self.loop).result(5)  # type: ignore[arg-type]


@pytest.fixture
def threaded() -> Iterator[ThreadedFake]:
    fake = FakeZeptrionDevice(
        channels=[
            FakeChannel("1", name="Light 1", group="Room A"),
            FakeChannel("5", name="Shade 1"),
            FakeChannel("3", name="Dimmer 1"),
            FakeChannel("-1"),
        ],
        time_scale=0.01,
    )
    wrapper = ThreadedFake(fake)
    with wrapper:
        yield wrapper


@pytest.fixture
def host(threaded: ThreadedFake) -> str:
    return threaded.fake.host


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def test_version() -> None:
    code, output = invoke("--version")
    assert code == 0
    assert output.strip()


def test_info(host: str) -> None:
    code, output = invoke("info", host)
    assert code == 0, output
    assert "99990001" in output
    assert "websocket=yes" in output
    assert "key 5: ch1 up (8501)" in output
    assert "temperature=24.5" in output

    code, output = invoke("info", host, "--json")
    assert code == 0
    data = json.loads(output)
    assert data["device"]["serial"] == "99990001"
    assert data["smartfront"]["keys"][4]["channels"] == [1]


def test_channels_and_state(host: str, threaded: ThreadedFake) -> None:
    code, output = invoke("channels", host)
    assert code == 0, output
    assert "ch1  onoff" in output
    assert "[Room A]" in output
    assert "ch4" not in output
    code, output = invoke("channels", host, "--all")
    assert "unused(-1)" in output
    code, output = invoke("channels", host, "--json")
    assert json.loads(output)[1]["category"] == 5

    threaded.fake.channels[0].value = 100
    code, output = invoke("state", host)
    assert "ch1=100" in output
    code, output = invoke("state", host, "--json")
    assert json.loads(output)["ch1"] == 100


def test_commands(host: str, threaded: ThreadedFake) -> None:
    fake = threaded.fake
    assert invoke("on", host, "1")[0] == 0
    assert fake.channels[0].value == 100
    assert invoke("off", host, "1")[0] == 0
    assert invoke("toggle", host, "1")[0] == 0
    assert fake.channels[0].value == 100
    assert invoke("open", host, "2")[0] == 0
    assert invoke("stop", host, "2")[0] == 0
    assert invoke("close", host, "2")[0] == 0
    assert invoke("move", host, "2", "open", "--ms", "500")[0] == 0
    assert invoke("move", host, "2", "close")[0] == 0
    assert invoke("dim", host, "3", "up", "--ms", "200")[0] == 0
    assert invoke("dim", host, "3", "down")[0] == 0
    assert invoke("scene", host, "2", "store", "1")[0] == 0
    assert invoke("send", host, "2", "recall_s1")[0] == 0
    posted = [body for method, _, body in fake.requests if method == "POST"]
    assert posted == [
        "cmd=on",
        "cmd=off",
        "cmd=toggle",
        "cmd=open",
        "cmd=stop",
        "cmd=close",
        "cmd=move_open_500",
        "cmd=move_close",
        "cmd=dim_up_200",
        "cmd=dim_down",
        "cmd=store_s1",
        "cmd=recall_s1",
    ]


def test_rename(host: str, threaded: ThreadedFake) -> None:
    code, output = invoke("rename", host, "1", "--name", "Küche", "--group", "EG")
    assert code == 0, output
    assert threaded.fake.channels[0].name == "Küche"
    assert invoke("rename", host, "1")[0] == 2


def test_errors(host: str, threaded: ThreadedFake) -> None:
    assert invoke("send", host, "1", "reboot")[0] == 2
    assert invoke("on", host, "9")[0] == 2  # rejected by typer's range check
    threaded.fake.fail_next = [400]
    code, output = invoke("on", host, "1")
    assert code == 1
    assert "HTTP 400" in output


def test_unreachable_host() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    code, output = invoke("state", f"127.0.0.1:{port}")
    assert code == 1
    assert "Error" in output


def test_watch(host: str, threaded: ThreadedFake) -> None:
    code, output = invoke("watch", host, "--count", "3")
    assert code == 0, output
    lines = output.strip().splitlines()
    assert "connected via websocket" in lines[0]
    assert lines[1].endswith("ch1=0")

    code, output = invoke("watch", host, "--count", "2", "--json")
    events = [json.loads(line) for line in output.strip().splitlines()]
    assert events[0]["type"] == "connection"
    assert events[1] == {**events[1], "type": "channel", "channel": 1, "value": 0}


def test_watch_key_events(host: str, threaded: ThreadedFake) -> None:
    async def press_later() -> None:
        await asyncio.sleep(0.5)
        await threaded.fake.press_key(5)

    future = asyncio.run_coroutine_threadsafe(press_later(), threaded.loop)
    # connection + 4 channels + press + release + ch1 change
    code, output = invoke("watch", host, "--count", "8")
    future.result(5)
    assert code == 0, output
    assert "key5 pressed (channel ch1 up)" in output
    assert "key5 released" in output

    future = asyncio.run_coroutine_threadsafe(press_later(), threaded.loop)
    code, output = invoke("watch", host, "--count", "7", "--json")
    future.result(5)
    key = [json.loads(line) for line in output.strip().splitlines()][-1]
    assert key["type"] == "key"
    assert key["function"]["channels"] == [1]


def test_watch_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from zeptrionair_api import device as device_module

    def fail(*args: object, **kwargs: object) -> object:
        raise device_module.ZeptrionConnectionError("boom")

    monkeypatch.setattr(device_module.ZeptrionDevice, "events", fail)
    code, output = invoke("watch", "127.0.0.1:1")
    assert code == 1
    assert "boom" in output


def test_dump(host: str, tmp_path: Path) -> None:
    code, output = invoke("dump", str(tmp_path), host)
    assert code == 0, output
    assert "device_01: 3340-2-B" in output
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["device_01"]["categories"] == ["1", "5", "3", "-1"]


def test_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    found = [
        DiscoveredDevice(
            name="zapp-99990001",
            host="zapp-99990001.local",
            addresses=("192.0.2.10",),
            port=80,
            serial="99990001",
            type="3340-2-B",
            sw_version="01.08.28",
        )
    ]

    async def fake_discover(timeout: float = 5.0) -> list[DiscoveredDevice]:
        return found

    monkeypatch.setattr("zeptrionair_api.discovery.discover", fake_discover)
    code, output = invoke("discover", "--timeout", "0.1")
    assert code == 0
    assert "zapp-99990001.local" in output
    assert "192.0.2.10" in output
    code, output = invoke("discover", "--json")
    assert json.loads(output)[0]["serial"] == "99990001"

    found.clear()
    code, output = invoke("discover")
    assert "No devices found" in output
