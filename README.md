# zeptrionair-api

[![CI](https://github.com/diaznet/zeptrionair-api/actions/workflows/ci.yml/badge.svg)](https://github.com/diaznet/zeptrionair-api/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/zeptrionair-api)](https://pypi.org/project/zeptrionair-api/)

Async Python library and command-line tool for **Feller zeptrionAir** WLAN devices (zApp: 3340-2-x and 3340-4-x), using their local HTTP and WebSocket interface. No cloud and no account needed.

It is the library behind the Home Assistant integration [ha-zeptrionair](https://github.com/diaznet/ha-zeptrionair).

## Features

- Read device info, channels (with their category: light, dimmer, blind, awning) and states.
- Control lights, dimmers and covers: on/off/toggle, open/close/stop, timed moves and dims, and scenes.
- **Real-time events** over the device WebSocket: channel changes, plus wall-key presses with their decoded function. Falls back to the `chnotify` long poll, and reconnects automatically.
- Smartfront: key layout, temperature/light/humidity sensors, LED background colours.
- mDNS discovery.
- `zeptrion` CLI for scripting and debugging.
- A simulated device (`zeptrionair_api.testing`) for your own tests.

## Install

```bash
pip install zeptrionair-api          # library only
pip install "zeptrionair-api[cli]"   # + the zeptrion command and mDNS discovery
```

Requires Python 3.12 or newer.

## Library

```python
import asyncio
from zeptrionair_api import ChannelUpdate, KeyEvent, ZeptrionDevice


async def main() -> None:
    async with ZeptrionDevice("zapp-12345678.local") as device:
        info = await device.identify()
        print(info.serial, info.type, info.sw_version)

        for channel in await device.channels():
            if channel.in_use:
                print(channel.number, channel.category, channel.name)

        await device.turn_on(1)  # light
        await device.close_cover(2)  # blind: close fully
        await device.move_open(2, 1500)  # blind: move up for 1.5 s

        async for event in device.events():  # runs until you break
            if isinstance(event, ChannelUpdate):
                print("channel", event.channel, "=", event.value)
            elif isinstance(event, KeyEvent) and event.pressed:
                print("key", event.key, event.function)


asyncio.run(main())
```

Notes:
- For covers, a channel value of **100 means the motor is running and 0 means it stopped**. The devices don't report position.
- Pass your own `aiohttp.ClientSession` with `ZeptrionDevice(host, session=...)` to share connections.
- At most 2 HTTP requests run at a time per device, because the device is a small embedded web server. GET requests are retried; commands (POST) never are.

## CLI

```text
zeptrion discover                         # find devices (mDNS)
zeptrion info zapp-12345678.local         # identification, features, key layout, sensors
zeptrion channels zapp-12345678.local
zeptrion state zapp-12345678.local
zeptrion on|off|toggle HOST CH
zeptrion open|close|stop HOST CH
zeptrion move HOST CH open|close [--ms 1500]
zeptrion dim HOST CH up|down [--ms 800]
zeptrion scene HOST CH recall|store|delete N
zeptrion send HOST CH move_open_2000      # any valid raw command
zeptrion rename HOST CH --name "Kitchen" --group "Ground floor"
zeptrion watch HOST [HOST ...] [--json]   # live channel changes and key presses
zeptrion dump OUTDIR HOST [HOST ...]      # anonymised fixtures for bug reports
```

Most commands accept `--json`. Exit codes: 0 = success, 1 = device or network error, 2 = invalid input.

## Documentation

- [docs/protocol.md](docs/protocol.md): the maintained protocol reference, including the WebSocket (which Feller never documented) and Smartfront key decoding.
- [docs/10ZEPAPIE1612.pdf](docs/10ZEPAPIE1612.pdf): Feller's original API document (v1.0).

## Supported hardware

| | Status |
|---|---|
| 3340-2-B, firmware 01.08.28, on/off lights and blinds, Smartfront | tested on real devices |
| 3340-2-A, 3340-4-A/B, dimmers, awnings, sensor fronts, firmware before 01.08 | expected to work, **untested** |

If you have untested hardware, please capture anonymised fixtures and open an issue or PR:

```bash
zeptrion dump captures/ <host> [<host> ...]
# or, without the CLI extra:
python -m zeptrionair_api.capture captures/ <host> [<host> ...]
```

Only GET requests are sent. Serial numbers, MAC addresses, channel and room names are replaced, and hostnames and IPs are never written. Please still review the files before sharing them.

## Development

```bash
uv sync
uv run pytest --cov
uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

## Disclaimer

Not affiliated with or endorsed by Feller AG. "zeptrion" and "zeptrionAIR" are trademarks of Feller AG. The WebSocket endpoint was first used by [heldchen/ha-zeptrion-air](https://github.com/heldchen/ha-zeptrion-air) (MIT).

## License

MIT
