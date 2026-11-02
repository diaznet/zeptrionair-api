"""Capture anonymised, read-only fixtures from zApp devices.

Used by ``zeptrion dump`` and runnable directly::

    python -m zeptrionair_api.capture OUTDIR HOST [HOST ...]

Only GET requests are sent. Anonymisation:

- serial numbers -> 99990001, 99990002, ... (in the order the hosts are given)
- MAC addresses  -> 02:00:00:00:00:NN
- channel names  -> generic names derived from the category ("Light 1", "Shade 1", ...)
- channel groups -> "Room A", "Room B", ...

Hostnames and IP addresses are never written.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import aiohttp

ENDPOINTS = (
    "/zrap/id",
    "/zrap/chdes",
    "/zrap/chscan",
    "/zrap/rssi",
    "/zrap/net/mac",
    "/zapi/smartfront/id",
    "/zapi/smartfront/led",
    "/zapi/smartfront/sensor",
    "/zapi/smartfront/sensors",
    "/zapi/smartbt/prgm",
    "/zrap/does-not-exist",
)

CATEGORY_NAMES = {"1": "Light", "3": "Dimmer", "5": "Shade", "6": "Awning"}
_MAC_RE = re.compile(r"(?i)\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b")


class Anonymiser:
    """Replaces identifying values consistently across all captured devices."""

    def __init__(self) -> None:
        self.serials: dict[str, str] = {}
        self.macs: dict[str, str] = {}
        self.groups: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def serial(self, real: str) -> str:
        if real not in self.serials:
            self.serials[real] = f"9999{len(self.serials) + 1:04d}"
        return self.serials[real]

    def _mac(self, match: re.Match[str]) -> str:
        real = match.group(0).lower()
        if real not in self.macs:
            self.macs[real] = f"02:00:00:00:00:{len(self.macs) + 1:02x}"
        return self.macs[real]

    def group(self, real: str) -> str:
        if not real:
            return real
        if real not in self.groups:
            self.groups[real] = f"Room {chr(ord('A') + len(self.groups) % 26)}"
        return self.groups[real]

    def channel_name(self, category: str, real: str) -> str:
        kind = CATEGORY_NAMES.get(category.strip())
        if kind is None:
            # Unused channels keep the firmware default "zApp-<serial> (<n>)"; the serial
            # inside is replaced by text().
            return real
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return f"{kind} {self._counters[kind]}"

    def chdes(self, body: str) -> str:
        def fix_channel(match: re.Match[str]) -> str:
            inner = match.group(2)
            category = re.search(r"<cat>(.*?)</cat>", inner)
            cat = category.group(1) if category else ""
            inner = re.sub(
                r"<name>(.*?)</name>",
                lambda m: f"<name>{self.channel_name(cat, m.group(1))}</name>",
                inner,
            )
            inner = re.sub(
                r"<group>(.*?)</group>", lambda m: f"<group>{self.group(m.group(1))}</group>", inner
            )
            return f"<ch{match.group(1)}>{inner}</ch{match.group(1)}>"

        return re.sub(r"<ch(\d+)>(.*?)</ch\1>", fix_channel, body, flags=re.S)

    def text(self, body: str) -> str:
        for real, fake in self.serials.items():
            body = body.replace(real, fake)
        return _MAC_RE.sub(self._mac, body)


async def _fetch(session: aiohttp.ClientSession, host: str, path: str) -> dict[str, Any]:
    try:
        async with session.get(f"http://{host}{path}", allow_redirects=False) as response:
            return {
                "status": response.status,
                "content_type": response.headers.get("Content-Type"),
                "body": (await response.read()).decode("utf-8", errors="replace"),
            }
    except (aiohttp.ClientError, TimeoutError) as err:
        return {"status": None, "error": type(err).__name__}


async def capture_device(
    host: str, anonymiser: Anonymiser, session: aiohttp.ClientSession
) -> dict[str, dict[str, Any]]:
    """Fetch every endpoint of one device (sequentially) and anonymise the bodies."""
    raw = {path: await _fetch(session, host, path) for path in ENDPOINTS}
    serial = re.search(r"<sn>(.*?)</sn>", str(raw["/zrap/id"].get("body", "")))
    if serial is None:
        raise ValueError(f"{host}: no serial number in /zrap/id; is this a zApp device?")
    anonymiser.serial(serial.group(1))
    for path, response in raw.items():
        if "body" not in response:
            continue
        body = str(response["body"])
        if path == "/zrap/chdes" and response["status"] == 200:
            body = anonymiser.chdes(body)
        response["body"] = anonymiser.text(body)
    return raw


def summarise(responses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def body(path: str) -> str:
        response = responses.get(path, {})
        return str(response.get("body", "")) if response.get("status") == 200 else ""

    def tag(path: str, name: str) -> str | None:
        match = re.search(rf"<{name}>(.*?)</{name}>", body(path))
        return match.group(1) if match else None

    front: dict[str, Any] = {}
    if body("/zapi/smartfront/id"):
        try:
            front = json.loads(body("/zapi/smartfront/id"))
        except json.JSONDecodeError:
            front = {}
    return {
        "type": tag("/zrap/id", "type"),
        "sw": tag("/zrap/id", "sw"),
        "categories": re.findall(r"<cat>(.*?)</cat>", body("/zrap/chdes")),
        "smartfront_type": front.get("type"),
        "btfu": front.get("btfu"),
        "status": {path: response.get("status") for path, response in responses.items()},
    }


async def capture(outdir: Path, hosts: list[str]) -> dict[str, Any]:
    """Capture all ``hosts`` into ``outdir`` and return the index."""
    anonymiser = Anonymiser()
    index: dict[str, Any] = {}
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for number, host in enumerate(hosts, start=1):
            responses = await capture_device(host, anonymiser, session)
            name = f"device_{number:02d}"
            folder = outdir / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "responses.json").write_text(
                json.dumps(responses, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            index[name] = summarise(responses)
    (outdir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return index


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    index = asyncio.run(capture(Path(args[0]), args[1:]))
    for name, summary in index.items():
        print(f"{name}: {summary['type']} categories={summary['categories']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
