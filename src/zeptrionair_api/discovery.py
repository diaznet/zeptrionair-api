"""mDNS discovery of zApp devices (requires the ``zeroconf`` extra)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .const import HOSTNAME_PREFIX, MDNS_SERVICE_HTTP, MDNS_SERVICE_ZAPP
from .models import DiscoveredDevice

if TYPE_CHECKING:
    from zeroconf import Zeroconf


def serial_from_name(name: str) -> str | None:
    """Extract the serial from an mDNS instance or host name (``zapp-12345678...``)."""
    label = name.split(".", 1)[0].lower()
    if not label.startswith(HOSTNAME_PREFIX):
        return None
    serial = label[len(HOSTNAME_PREFIX) :]
    return serial if serial.isdigit() else None


def is_zapp_name(name: str) -> bool:
    return serial_from_name(name) is not None


async def discover(
    timeout: float = 5.0, *, zeroconf: Zeroconf | None = None
) -> list[DiscoveredDevice]:
    """Browse for zApp devices for ``timeout`` seconds.

    Both ``_zapp._tcp`` (firmware 01.08 and later) and ``_http._tcp`` (older firmware, filtered
    to ``zapp-*`` names) are browsed. mDNS is not always reliable, so a device that doesn't
    show up may still be reachable by hostname or IP.
    """
    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import (
            AsyncServiceBrowser,
            AsyncServiceInfo,
            AsyncZeroconf,
        )
    except ImportError as err:  # pragma: no cover - depends on installed extras
        raise RuntimeError(
            "discovery needs the 'zeroconf' package: pip install zeptrionair-api[discovery]"
        ) from err

    found: dict[str, str] = {}  # instance name -> service type

    def on_change(
        zeroconf: Any, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if state_change is ServiceStateChange.Removed or not is_zapp_name(name):
            return
        # Prefer the dedicated service type when a device announces both.
        if found.get(name) != MDNS_SERVICE_ZAPP:
            found[name] = service_type

    aiozc = AsyncZeroconf(zc=zeroconf) if zeroconf is not None else AsyncZeroconf()
    try:
        browser = AsyncServiceBrowser(
            aiozc.zeroconf, [MDNS_SERVICE_ZAPP, MDNS_SERVICE_HTTP], handlers=[on_change]
        )
        await asyncio.sleep(timeout)
        await browser.async_cancel()

        devices: dict[str, DiscoveredDevice] = {}
        for name, service_type in found.items():
            info = AsyncServiceInfo(service_type, name)
            if not await info.async_request(aiozc.zeroconf, 3000):
                continue
            properties = {
                key.decode(errors="replace"): (value or b"").decode(errors="replace")
                for key, value in (info.properties or {}).items()
            }
            instance = name.split(".", 1)[0]
            host = (info.server or f"{instance}.local").rstrip(".")
            serial = serial_from_name(host) or serial_from_name(name)
            device = DiscoveredDevice(
                name=name.split(".", 1)[0],
                host=host,
                addresses=tuple(info.parsed_addresses()),
                port=info.port or 80,
                serial=serial,
                type=properties.get("type") or None,
                sw_version=properties.get("sw") or None,
                properties=properties,
            )
            devices[serial or host] = device
        return sorted(devices.values(), key=lambda device: device.name)
    finally:
        if zeroconf is None:
            await aiozc.async_close()
