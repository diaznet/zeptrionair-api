# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1]

### Changed
- `testing.FakeZeptrionDevice`: dimmers now behave as real dimmers are reported to: the API reports `100` while lit (whatever the brightness) and `0` when off. The actual level is in `FakeChannel.level`; `dimmer_reports_level=True` restores the old behaviour.
- `docs/protocol.md`: dimmer reporting and full dim time (marked [UNVERIFIED]); Smartbutton programming paths verified (only `/zapi/smartbt` works).

### Added
- `FakeZeptrionDevice.set_level()` and `reported_value()`.

## [0.1.0]

### Added
- `ZeptrionDevice`: async client for one zApp device:
  - identification, channels, states, RSSI and MAC;
  - Smartfront key layout, sensors and LEDs;
  - capability probing;
  - channel commands (single and multi-channel), with validation;
  - channel descriptor updates;
  - LED background colours.
- Event stream:
  - WebSocket `/zrap/ws`, with `chnotify` long-poll fallback;
  - automatic reconnection with backoff;
  - HTTP liveness checks (the device doesn't answer WebSocket pings);
  - decoded key events.
- Lenient XML/JSON parsing that tolerates the quirks in the official documentation.
- mDNS discovery (`_zapp._tcp`, plus `_http._tcp` filtered to `zapp-*`).
- `zeptrion` CLI: `discover`, `info`, `channels`, `state`, `on`/`off`/`toggle`, `open`/`close`/`stop`, `move`, `dim`, `scene`, `send`, `rename`, `watch`, `dump`.
- Anonymised fixture capture (`zeptrion dump`, `python -m zeptrionair_api.capture`).
- Simulated device for tests (`zeptrionair_api.testing.FakeZeptrionDevice`).
- Protocol reference `docs/protocol.md`.

[Unreleased]: https://github.com/diaznet/zeptrionair-api/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/diaznet/zeptrionair-api/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/diaznet/zeptrionair-api/releases/tag/v0.1.0
