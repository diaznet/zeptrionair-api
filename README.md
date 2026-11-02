# zeptrionair-api

Async Python library and command-line tool for **Feller zeptrionAir** WLAN devices (zApp, 3340-2-x / 3340-4-x), using their local HTTP and WebSocket interface. No cloud and no account needed.

> 🚧 **Work in progress.** Nothing has been published yet. See [PLAN.md](PLAN.md).

## Planned features

- Read device info, channels and states. Control lights, dimmers and covers (blinds, shades, awnings), and recall scenes.
- Real-time events over the device WebSocket: channel changes and wall-key presses, with a long-poll fallback.
- Decoding of Smartfront key functions, plus Smartfront temperature, light and humidity values where available.
- mDNS discovery.
- `zeptrion` CLI: `discover`, `info`, `state`, `on`/`off`, `open`/`close`/`stop`, `watch`, `dump`, …
- Used by the Home Assistant integration [ha-zeptrionair](https://github.com/diaznet/ha-zeptrionair).

## Documentation

- [docs/protocol.md](docs/protocol.md): the maintained protocol reference, including the undocumented WebSocket.
- [docs/10ZEPAPIE1612.pdf](docs/10ZEPAPIE1612.pdf): Feller's original API document (v1.0).

## Contributing fixtures

If you own zeptrionAir hardware that isn't covered yet (A models, 4-channel, dimmers, awnings, sensor fronts, older firmware), you can capture anonymised, read-only fixtures:

```
python scripts/capture_fixtures.py captures/ <host> [<host> ...]
```

See [tests/fixtures/README.md](tests/fixtures/README.md).

## Disclaimer

Not affiliated with or endorsed by Feller AG. "zeptrion" and "zeptrionAIR" are trademarks of Feller AG. Protocol knowledge partly comes from [heldchen/ha-zeptrion-air](https://github.com/heldchen/ha-zeptrion-air) (MIT).

## License

MIT
