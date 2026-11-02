"""mDNS discovery (zeroconf is replaced by fakes)."""

from __future__ import annotations

from typing import Any

import pytest
import zeroconf
import zeroconf.asyncio

from zeptrionair_api.discovery import discover, is_zapp_name, serial_from_name


@pytest.mark.parametrize(
    ("name", "serial"),
    [
        ("zapp-20050094._zapp._tcp.local.", "20050094"),
        ("zApp-20050094.local.", "20050094"),
        ("zapp-20050094", "20050094"),
        ("printer._http._tcp.local.", None),
        ("zapp-abc.local.", None),
    ],
)
def test_serial_from_name(name: str, serial: str | None) -> None:
    assert serial_from_name(name) == serial
    assert is_zapp_name(name) is (serial is not None)


class FakeInfo:
    registry: dict[str, dict[str, Any]] = {}

    def __init__(self, service_type: str, name: str) -> None:
        self.name = name
        data = self.registry.get(name, {})
        self.server = data.get("server")
        self.port = data.get("port", 80)
        self.properties = data.get("properties")
        self._addresses = data.get("addresses", [])
        self._ok = data.get("ok", True)

    async def async_request(self, zc: Any, timeout: int) -> bool:
        return bool(self._ok)

    def parsed_addresses(self) -> list[str]:
        return list(self._addresses)


class FakeBrowser:
    announcements: list[tuple[str, str, zeroconf.ServiceStateChange]] = []

    def __init__(self, zc: Any, types: list[str], handlers: list[Any]) -> None:
        for service_type, name, change in self.announcements:
            for handler in handlers:
                handler(zeroconf=zc, service_type=service_type, name=name, state_change=change)

    async def async_cancel(self) -> None:
        return None


class FakeAsyncZeroconf:
    closed = False

    def __init__(self, zc: Any = None) -> None:
        self.zeroconf = zc or object()

    async def async_close(self) -> None:
        FakeAsyncZeroconf.closed = True


@pytest.fixture(autouse=True)
def fake_zeroconf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zeroconf.asyncio, "AsyncServiceBrowser", FakeBrowser)
    monkeypatch.setattr(zeroconf.asyncio, "AsyncServiceInfo", FakeInfo)
    monkeypatch.setattr(zeroconf.asyncio, "AsyncZeroconf", FakeAsyncZeroconf)


async def test_discover() -> None:
    added = zeroconf.ServiceStateChange.Added
    FakeBrowser.announcements = [
        ("_http._tcp.local.", "zapp-99990001._http._tcp.local.", added),
        ("_zapp._tcp.local.", "zapp-99990001._zapp._tcp.local.", added),
        ("_http._tcp.local.", "zapp-99990001._http._tcp.local.", added),
        ("_http._tcp.local.", "printer._http._tcp.local.", added),
        ("_http._tcp.local.", "zapp-99990002._http._tcp.local.", added),
        ("_zapp._tcp.local.", "zapp-99990003._zapp._tcp.local.", added),
        (
            "_zapp._tcp.local.",
            "zapp-99990004._zapp._tcp.local.",
            zeroconf.ServiceStateChange.Removed,
        ),
    ]
    FakeInfo.registry = {
        "zapp-99990001._zapp._tcp.local.": {
            "server": "zapp-99990001.local.",
            "addresses": ["192.0.2.1"],
            "properties": {b"type": b"3340-2-B", b"sw": b"01.08.28", b"path": None},
        },
        "zapp-99990001._http._tcp.local.": {"server": "zapp-99990001.local."},
        "zapp-99990002._http._tcp.local.": {"server": None, "addresses": ["192.0.2.2"]},
        "zapp-99990003._zapp._tcp.local.": {"ok": False},
    }
    devices = await discover(0)
    assert [d.serial for d in devices] == ["99990001", "99990002"]
    first, second = devices
    assert first.host == "zapp-99990001.local"
    assert first.type == "3340-2-B"
    assert first.sw_version == "01.08.28"
    assert first.properties["path"] == ""
    assert first.addresses == ("192.0.2.1",)
    assert second.host == "zapp-99990002.local"
    assert second.type is None
    assert FakeAsyncZeroconf.closed


async def test_discover_with_shared_zeroconf() -> None:
    FakeBrowser.announcements = []
    FakeAsyncZeroconf.closed = False
    assert await discover(0, zeroconf=object()) == []  # type: ignore[arg-type]
    assert not FakeAsyncZeroconf.closed
