"""Capture anonymised read-only fixtures from zeptrionAir devices.

Usage:
    python scripts/capture_fixtures.py OUTDIR HOST [HOST ...]

Only GET requests are sent. Nothing on the device is changed.

Anonymisation:
- serial numbers -> 99990001, 99990002, ... (in the order the hosts are given)
- MAC addresses  -> 02:00:00:00:00:NN
- channel names  -> generic names derived from the category ("Light 1", "Shade 1", ...)
- channel groups -> "Room A", "Room B", ...
Hostnames and IP addresses are never written to the output.

Output: one folder per device (device_01, device_02, ...) containing responses.json,
plus an index.json summarising every device.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import aiohttp

ENDPOINTS = [
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
]

CATEGORY_NAMES = {"1": "Light", "3": "Dimmer", "5": "Shade", "6": "Awning"}
MAC_RE = re.compile(r"(?i)\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b")


class Anonymiser:
    def __init__(self) -> None:
        self.serials: dict[str, str] = {}
        self.macs: dict[str, str] = {}
        self.groups: dict[str, str] = {}
        self.name_counters: dict[str, int] = {}

    def serial(self, real: str) -> str:
        if real not in self.serials:
            self.serials[real] = f"9999{len(self.serials) + 1:04d}"
        return self.serials[real]

    def mac(self, match: re.Match[str]) -> str:
        real = match.group(0).lower()
        if real not in self.macs:
            self.macs[real] = f"02:00:00:00:00:{len(self.macs) + 1:02x}"
        return self.macs[real]

    def group(self, real: str) -> str:
        if not real:
            return real
        if real not in self.groups:
            self.groups[real] = f"Room {chr(ord('A') + len(self.groups))}"
        return self.groups[real]

    def channel_name(self, cat: str, real: str) -> str:
        kind = CATEGORY_NAMES.get(cat)
        if kind is None:
            # Unused channels keep the firmware's default pattern "zApp-<serial> (<n>)";
            # the serial inside is replaced by the generic serial replacement afterwards.
            return real
        self.name_counters[kind] = self.name_counters.get(kind, 0) + 1
        return f"{kind} {self.name_counters[kind]}"

    def chdes(self, body: str) -> str:
        def fix_channel(m: re.Match[str]) -> str:
            inner = m.group(2)
            cat_m = re.search(r"<cat>(.*?)</cat>", inner)
            cat = cat_m.group(1) if cat_m else ""
            inner = re.sub(
                r"<name>(.*?)</name>",
                lambda n: f"<name>{self.channel_name(cat, n.group(1))}</name>",
                inner,
            )
            inner = re.sub(
                r"<group>(.*?)</group>",
                lambda g: f"<group>{self.group(g.group(1))}</group>",
                inner,
            )
            return f"<ch{m.group(1)}>{inner}</ch{m.group(1)}>"

        return re.sub(r"<ch(\d+)>(.*?)</ch\1>", fix_channel, body, flags=re.S)

    def text(self, body: str) -> str:
        for real, fake in self.serials.items():
            body = body.replace(real, fake)
        return MAC_RE.sub(self.mac, body)


async def fetch(session: aiohttp.ClientSession, host: str, path: str) -> dict[str, object]:
    try:
        async with session.get(f"http://{host}{path}", allow_redirects=False) as resp:
            return {
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type"),
                "body": (await resp.read()).decode("utf-8", errors="replace"),
            }
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return {"status": None, "error": type(err).__name__}


async def capture(host: str, anon: Anonymiser) -> dict[str, dict[str, object]]:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Sequential on purpose: the device is a small embedded web server.
        raw = {path: await fetch(session, host, path) for path in ENDPOINTS}

    id_body = str(raw["/zrap/id"].get("body", ""))
    sn = re.search(r"<sn>(.*?)</sn>", id_body)
    if sn is None:
        raise RuntimeError(f"{host}: no serial number in /zrap/id, not a zeptrionAir device?")
    anon.serial(sn.group(1))

    for path, resp in raw.items():
        if "body" not in resp:
            continue
        body = str(resp["body"])
        if path == "/zrap/chdes" and resp["status"] == 200:
            body = anon.chdes(body)
        resp["body"] = anon.text(body)
    return raw


def summarise(responses: dict[str, dict[str, object]]) -> dict[str, object]:
    def body(path: str) -> str:
        resp = responses[path]
        return str(resp.get("body", "")) if resp.get("status") == 200 else ""

    def tag(path: str, name: str) -> str | None:
        m = re.search(rf"<{name}>(.*?)</{name}>", body(path))
        return m.group(1) if m else None

    front: dict[str, object] = {}
    if body("/zapi/smartfront/id"):
        front = json.loads(body("/zapi/smartfront/id"))
    return {
        "type": tag("/zrap/id", "type"),
        "sw": tag("/zrap/id", "sw"),
        "categories": re.findall(r"<cat>(.*?)</cat>", body("/zrap/chdes")),
        "smartfront_type": front.get("type"),
        "btfu": front.get("btfu"),
        "status": {path: resp.get("status") for path, resp in responses.items()},
    }


async def main(outdir: Path, hosts: list[str]) -> None:
    anon = Anonymiser()
    index: dict[str, object] = {}
    for number, host in enumerate(hosts, start=1):
        responses = await capture(host, anon)
        name = f"device_{number:02d}"
        folder = outdir / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "responses.json").write_text(
            json.dumps(responses, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        index[name] = summarise(responses)
        print(f"{name}: {index[name]['type']} categories={index[name]['categories']}")
    (outdir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2:]))
