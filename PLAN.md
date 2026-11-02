# zeptrionAir for Home Assistant: full plan

## Status (2026-11-02)

| Milestone | Status |
|---|---|
| M0b fixtures, protocol.md | ✅ committed |
| M1 library core | ✅ committed. 112 tests, 96 % coverage. Verified on hardware: WebSocket confirms in 30–50 ms; **the device doesn't answer WebSocket pings** (liveness via HTTP). |
| M2 CLI + CI/release workflows | ✅ committed. **Not pushed, not released.** |
| M3/M4 integration | ✅ committed. 33 tests, 94 % coverage, hassfest clean. The live smoke test passed against all 8 devices: setup and a light toggled in 124 ms. Covers were not moved on real hardware. |
| M5 release | ⏳ waiting for the user: push both repos, tag `zeptrionair-api` `v0.1.0rc1` → `v0.1.0`, then tag `ha-zeptrionair` `v0.1.0`. |

Deviations from the plan below, decided while building (each checked with Context7):
- **Devices:**
  - One hub device per zApp (Wi-Fi signal, keys, temperature), plus **one device per channel** named after the channel and with the channel's `group` as suggested area.
  - HA 2026.7 replaced `via_device` with `via_device_id`; both are supported.
  - HA 2026 prefixes entity IDs with the area (e.g. `cover.kitchen_kitchen_shade`). This is documented in the README.
- **Minimum HA version 2025.2** (relocated `ServiceInfo` imports, `AddConfigEntryEntitiesCallback`). Uses `voluptuous`, which is still importable now that probatio has replaced it (HA 2026.9).
- **Key events** use the event types `press`/`release`; `ButtonEventType` only exists from HA 2026.7.
- **Brand icon** ships locally in `custom_components/zeptrionair/brand/` (supported from HA 2026.3). It's an original icon, not Feller's logo.
- **`scripts/capture_fixtures.py` → `python -m zeptrionair_api.capture` / `zeptrion dump`.**
- **Dependabot** can't edit `manifest.json`. It bumps `requirements_test.txt`, and CI (`scripts/check_versions.py`) fails until `manifest.json` has the same pin.
- **HA tests** run in a Linux container (podman, `python:3.14-slim`), because HA 2026.10 requires Python ≥ 3.14.2.

Workspace: `C:\Dev\zeptrionair`

| Folder | Content |
|---|---|
| `C:\Dev\zeptrionair\zeptrionair-api` | Async Python library and CLI (`zeptrionair-api` on PyPI) |
| `C:\Dev\zeptrionair\ha-zeptrionair` | HACS custom integration (HA domain `zeptrionair`) |

Both repos are public on GitHub and already checked out by the user:
- `https://github.com/diaznet/zeptrionair-api`
- `https://github.com/diaznet/ha-zeptrionair`

The owner is `diaznet`. **The agent never commits or pushes; the user does.** One-time setup is done: PyPI and TestPyPI pending publishers, the `testpypi`/`pypi` environments, and `v*` tag rulesets. Everything is written to be **redistributed**: nothing may be specific to one installation.

---

## 1. Goals and non-goals

**Goals**
- Local control of Feller zeptrionAir devices (zApp WLAN modules 3340-2-A/B and 3340-4-A/B) from Home Assistant, with real-time push state.
- A reusable async library with no Home Assistant concepts in it, plus a CLI for scripting and debugging.
- Discovery with no configuration (mDNS and DHCP), with manual host entry as a fallback.
- Lights (on/off and dimmable), covers (blinds, shades, awnings) with live movement state and optional position estimation, scene recall, wall-key events, Smartbutton events, and diagnostics.
- Translated into EN, DE, FR and IT from the first release.

**Non-goals for v1**
- Network provisioning endpoints (`/zrap/net`, `/zrap/netscan`), factory reset (`/zrap/sys`), and the device's scheduler, NTP and location endpoints.
- Programming Smartbuttons (`/zapi/smartbt/prgm|prgn|prgs`).
- Custom Lovelace cards.
- Writing Smartfront LED colours. This may come in v2, and only the `bg` attribute is safe to change.

---

## 2. Verified facts (milestone 0, run 2026-11-02 against 8 real devices)

Sources: the Feller PDF `zeptrionair-api/docs/10ZEPAPIE1612.pdf` and live probing. The "zApp API 01.08.18" PDF in heldchen's repo turned out to be byte-identical to ours. **The WebSocket is not documented anywhere official**; heldchen probably found it in the Android app. Feller has discontinued the product line, so no newer document is expected. The maintained reference is **`zeptrionair-api/docs/protocol.md`**, and where it differs from this section, protocol.md wins.

### 2.1 Transport
- HTTP on port 80, no authentication. Base URL: `http://<host>`.
- Reads use `GET` and return XML (`text/xml`) on a single line. The encoding is US-ASCII or UTF-8, as declared in the XML header.
- Writes use `POST` with an `application/x-www-form-urlencoded` body (UTF-8, percent-encoded). The device answers **302**, which must not be followed.
- `/zapi/*` endpoints use JSON.
- The XML examples in the PDF are malformed (`</hnotify>`, `<chdes>` used as a closing tag). Parse leniently and never trust the documentation's XML.
- Responses arrive in about 30–330 ms.

### 2.2 Endpoints used
| Endpoint | Method | Use |
|---|---|---|
| `/zrap/id` | GET | `hw`, `sn` (serial number, used as unique ID), `sys`, `type`, `oen`, `sw` (for example `01.08.28 (Jun  1 2018 09:13:39)`), `boot` |
| `/zrap/chdes[/chN[/attr]]` | GET/POST | Per channel: `name` (32 bytes), `group` (32), `icon` (24), `type` (4), `cat` (4) |
| `/zrap/chscan[/chN]` | GET | `<chN><val>` with a value from 0–100 or -1 |
| `/zrap/chnotify` | GET (long poll, up to 30 s) | Same format as `chscan`. Fallback push channel. |
| `/zrap/chctrl/chN` | POST `cmd=…` | Control one channel |
| `/zrap/chctrl` | POST `cmd1=…&cmd2=…` | Control several channels in one request |
| `/zrap/rssi` | GET | `<rssi><dbm>-64</dbm></rssi>` |
| `/zrap/ws` | WebSocket | Push events (see 2.4) |
| `/zapi/smartfront/id` | GET (JSON) | Smartfront `btfu`, `sw`, `hw`, `sys`, `sid`, `type`. Returns 404 if there is no Smartfront. |
| `/zapi/smartfront/led` | GET (JSON) | 8 LEDs: `id`, `on`, `rgb`, `effect`, `bg`. Read-only in v1. |
| `/zapi/smartfront/sensors` | GET (JSON) | **Plural.** The PDF's `/sensor` returns 404. `{"temp":"35.44C","lux":"none","hum":"none"}`: every front tested reports `temp` (warmed by the module itself); `"none"` means the sensor is absent. |
| `/zrap/net/mac` | GET | MAC address, used for HA DHCP `registered_devices`. Never GET `/zrap/net` as a whole. |

### 2.3 `chctrl` commands
`on`, `off`, `toggle`, `stop`, `dim_up`, `dim_down`, `open`, `close`, `move_open`, `move_close`, `recall_s1..4`, `store_s1..4`, `delete_s1..4`, and timed variants with t from 100 to 32000 ms: `dim_up_<t>`, `dim_down_<t>`, `move_open_<t>`, `move_close_<t>`, `dim_<t>`.

### 2.4 WebSocket `/zrap/ws` (firmware 01.08.28)
- One JSON object per text frame. Several clients can connect to the same device at once, and each receives the same messages.
- `{"eid1":{"ch":N,"val":V}}` means a channel's state changed.
  - Lights: `val` 0 or 100. Dimmers: presumably 0–100 (no hardware available to check).
  - **Covers: `val` = 100 while the motor runs and 0 when it stops.** This was observed, and it contradicts the PDF's "always -1". The cover also stops by itself at its end position (one shade: ~7 s).
- `{"eid2":{"bta":"......P.."}}` is a key event. The string has 9 characters: `P` = pressed, `.` = released, and a string of all dots follows on release. Position *i* (0-based) corresponds to Smartfront key *i+1*. The meaning of position 8 is unknown.
- Order of messages for a wall press: `eid2` press, `eid2` release, then `eid1` within about 100–300 ms.
- No keep-alive messages were seen during 150 s of idle time. A reconnect timeout must therefore not be shorter than the idle period; use a long read timeout and reconnect on close or error.

### 2.5 Channel categories (`chdes/cat`)
| cat | Meaning | HA platform |
|---|---|---|
| 1 | On/off light | `light` (ColorMode.ONOFF) |
| 3 | Dimmer | `light` (ColorMode.BRIGHTNESS) |
| 5 | Blind/shade | `cover` (default device_class `shade`; the user can change it via "Show as") |
| 6 | Awning (Markise) | `cover` (device_class `awning`) |
| `00`, `-1`, other | Unused or unknown | No entity. Logged and included in diagnostics. |

### 2.6 Smartfront key functions (`btfu`), inferred from 4 devices
`btfu` is a list of 8 comma-separated codes, one per key 1–8:
- `0` = unused key.
- `1000` = Smartbutton, which sends its programmed HTTP request(s). This was observed: a Smartbutton on one device switched a light on another device.
- `XYZZ`, where X is a channel bitmask (8 = ch1, 4 = ch2; presumably 2 = ch3 and 1 = ch4) and Y is 5 for up/on or 6 for down/off. Examples: `8501` = ch1 up/on, `8602` = ch1 down/off, `4501`, `4602`. The meaning of ZZ is unknown.
- **The decoding is heuristic.** Unknown codes go through as raw key events, and a missing `btfu` means "direction unknown".

### 2.7 Discovery
- mDNS: firmware 01.08 and later announces `_zapp._tcp`, and also `_http._tcp` for backward compatibility. Older firmware only announces `_http._tcp`. The instance and host name is `zapp-<serial>`, and the TXT records include `type` and `sw`.
- **mDNS is unreliable:** one of 8 devices never announced itself in 30 s, even though it answered HTTP. DHCP hostname discovery and manual entry are therefore required.
- `/zapi/smartbt/prgm` returns 404 on some fronts (type `…ZZ`). Smartbutton support therefore has to be probed, not assumed.

---

## 3. Library: `zeptrionair-api`

### 3.1 Tooling
- `uv` (project and virtual environment), `hatchling` build, src layout.
- Python `>=3.12`. CI runs 3.12, 3.13 and 3.14. Check HA's current minimum with Context7 at the start.
- Runtime dependencies: `aiohttp` only. Optional extras: `[cli]` adds `typer` and `rich`; `[discovery]` adds `zeroconf`.
- Dev: `ruff` (lint and format), `mypy --strict`, `pytest`, `pytest-asyncio`, `pytest-cov` (target ≥ 90 %), `pre-commit`.
- Ships `py.typed`. MIT licence.
- **Context7:** look up current docs before writing code that uses aiohttp (client and WebSocket), zeroconf, typer and hatchling.

### 3.2 Layout
```
zeptrionair-api/
  pyproject.toml  README.md  LICENSE  CHANGELOG.md
  docs/10ZEPAPIE1612.pdf  docs/protocol.md        # protocol.md = maintained protocol reference
  scripts/capture_fixtures.py                      # anonymised read-only capture (basis for `zeptrion dump`)
  src/zeptrionair_api/
    __init__.py          # public exports, __version__
    const.py             # enums: Category, Command, defaults
    models.py            # dataclasses
    exceptions.py
    parsing.py           # lenient XML/JSON parsing, btfu/bta decoding
    device.py            # ZeptrionDevice
    events.py            # WebSocket + chnotify listener
    discovery.py         # zeroconf discovery (optional dependency)
    cli.py               # typer app (optional dependency)
    testing/
      __init__.py
      fake_device.py     # aiohttp fake zApp, reused by ha-zeptrionair tests
  tests/
    fixtures/            # anonymised real responses + WebSocket sequences
    test_parsing.py  test_device.py  test_events.py  test_cli.py  test_fake_device.py
```

### 3.3 Public API (draft)
```python
class Category(IntEnum): ONOFF=1; DIMMER=3; BLIND=5; AWNING=6   # + UNKNOWN handling
class Command(StrEnum): ON, OFF, TOGGLE, STOP, DIM_UP, DIM_DOWN, OPEN, CLOSE, MOVE_OPEN, MOVE_CLOSE, ...
  # helper builders: Command.timed("move_open", ms), Command.scene("recall", n)

@dataclass(frozen=True) class DeviceInfo: serial, type, hw_version, sw_version, boot_version, sys, oen
@dataclass(frozen=True) class Channel: number, name, group, icon, type, category: Category | None, raw_cat: str
@dataclass(frozen=True) class SmartfrontInfo: type, sw_version, hw_version, sid, keys: tuple[KeyFunction, ...]
@dataclass(frozen=True) class KeyFunction: index, raw: str, kind: Literal["unused","smartbutton","channel","unknown"],
                                            channels: frozenset[int], direction: Literal["up","down"] | None
@dataclass(frozen=True) class ChannelUpdate: channel: int, value: int | None   # None == -1/unknown
@dataclass(frozen=True) class KeyEvent: index: int, pressed: bool, function: KeyFunction | None, raw: str
@dataclass(frozen=True) class Capabilities: websocket: bool, smartfront: bool, smartbuttons: bool, sensors: bool

class ZeptrionDevice:
    def __init__(self, host: str, *, session: aiohttp.ClientSession | None = None,
                 request_timeout: float = 10, max_concurrent_requests: int = 2): ...
    async def __aenter__/__aexit__   # closes the session only if it created one
    async def identify(self) -> DeviceInfo
    async def channels(self) -> list[Channel]              # all channels, incl. unused
    async def states(self) -> dict[int, int | None]
    async def rssi(self) -> int
    async def smartfront(self) -> SmartfrontInfo | None    # None on 404
    async def sensors(self) -> dict[str, float | None] | None
    async def probe(self) -> Capabilities                  # WS handshake test + 404 probes
    async def send(self, channel: int, command: Command | str) -> None
    async def send_many(self, commands: Mapping[int, Command | str]) -> None
    async def set_channel_description(self, channel: int, **fields: str) -> None  # validates byte limits
    # convenience: turn_on/off/toggle, open/close/stop, move_open/close(ms=None), dim_up/down(ms=None), recall_scene(n)
    def events(self, *, prefer: Literal["auto","websocket","chnotify"] = "auto",
               reconnect: bool = True) -> AsyncIterator[ChannelUpdate | KeyEvent | ConnectionEvent]

async def discover(timeout: float = 5.0) -> list[DiscoveredDevice]   # _zapp._tcp + _http._tcp name zapp-*
```

Exceptions: `ZeptrionError`, with subclasses `ZeptrionConnectionError`, `ZeptrionTimeoutError`, `ZeptrionResponseError` (status code, body), `ZeptrionParseError` and `ZeptrionUnsupportedError`.

### 3.4 Behaviour details
- **HTTP:**
  - At most 2 requests in flight per device (semaphore), because the device is a small embedded web server.
  - `allow_redirects=False`; a 302 on POST counts as success.
  - Retry with backoff on connection errors and 5xx, for idempotent GETs only. POSTs are never retried automatically: a repeated `toggle` would undo itself.
- **Parsing:**
  - Use the standard library `xml.etree` (no xmltodict dependency).
  - Pre-sanitise known firmware quirks, and fall back to a regex extractor when XML parsing fails.
  - Unescape entities.
  - Treat `-1`, empty values and `none` as `None`.
- **`events()`:**
  - In `auto` mode, try `ws://host/zrap/ws` first. If the handshake fails, use the `chnotify` long-poll loop.
  - Reconnect with exponential backoff (1 s up to 60 s, with jitter).
  - Emit `ConnectionEvent(connected: bool)` so consumers can mark entities unavailable.
  - No receive timeout shorter than about 5 minutes, because the device sends nothing while idle. Use aiohttp WebSocket heartbeats only if the device answers pings; test this in milestone 1 and fall back to a periodic `chscan` liveness GET.
  - De-duplicate the all-dots `bta` release frames into `KeyEvent(pressed=False)`.
- **`btfu` decoding** lives in `parsing.py`, with table-driven tests from real fixtures.
- **Input validation:** channel number between 1 and the channel count, timed-command ms between 100 and 32000, scene 1–4, and `chdes` field byte lengths (UTF-8 aware).

### 3.5 CLI (`zeptrion`, via `pip install zeptrionair-api[cli]`)
```
zeptrion discover [--timeout 5] [--json]
zeptrion info HOST [--json]                 # id + capabilities + smartfront + rssi
zeptrion channels HOST [--all] [--json]
zeptrion state HOST [--json]
zeptrion on|off|toggle HOST CH
zeptrion open|close|stop HOST CH
zeptrion move HOST CH --open/--close [--ms N]
zeptrion dim HOST CH --up/--down [--ms N]
zeptrion scene HOST CH {recall|store|delete} N
zeptrion rename HOST CH [--name ..] [--group ..]
zeptrion watch HOST [HOST...] [--raw] [--json]   # live events, multi-device
zeptrion dump HOST [--anonymise] OUTDIR          # capture fixtures for bug reports
```
- Exit codes: 0 = success, 1 = device or communication error, 2 = usage error.
- Machine-readable output with `--json`. Logs go to stderr.
- `dump --anonymise` replaces serial numbers, hostnames and IPs. Users of other hardware run this to contribute fixtures.

### 3.6 Fake device (`zeptrionair_api.testing.FakeZeptrionDevice`)
- An aiohttp test server, configurable with channel count, channel categories, Smartfront on/off, `btfu`, WebSocket on/off (for testing the `chnotify` fallback), sensors, and firmware string.
- Simulates:
  - lights;
  - dimmers (value ramps over time);
  - covers (motor running = 100, automatic stop after travel time, opposite command = stop);
  - `eid2` key events through a `press_key(i)` helper.
- Injectable faults: malformed XML, slow responses, 500 errors, dropped WebSocket.
- Used by the library tests, the CLI tests, and the `ha-zeptrionair` tests.

### 3.7 Release
- SemVer, starting at `0.1.0`. Reach `1.0.0` once the integration is stable.
- The PyPI name `zeptrionair-api` was confirmed free (2026-11-02).
- The version comes from the git tag (`hatch-vcs`), so there's no manual version bump in `pyproject.toml`.
- The CI and publish workflows are described in **section 5**.

---

## 4. Integration: `ha-zeptrionair`

### 4.1 Tooling
- Layout based on `ludeeus/integration_blueprint`: devcontainer, `scripts/develop`, ruff.
- Tests: `pytest-homeassistant-custom-component`. **Run in WSL2 or the devcontainer**, because HA test tooling isn't reliable on native Windows.
- CI: `hassfest`, `hacs/action` validation, ruff, pytest, and a translation key-completeness check.
- **Context7:** look up current docs before writing each part (config flow, zeroconf and DHCP discovery, DataUpdateCoordinator, `entry.runtime_data`, light/cover/event/button/sensor entities, diagnostics, translations, the quality scale rules).

### 4.2 Layout
```
ha-zeptrionair/
  hacs.json  README.md  LICENSE  CHANGELOG.md  info.md
  custom_components/zeptrionair/
    __init__.py  manifest.json  const.py
    config_flow.py
    coordinator.py
    entity.py          # base entity: device_info, unique_id, availability
    light.py  cover.py  button.py  event.py  sensor.py
    diagnostics.py
    services.yaml      # only if needed (v1: none planned)
    icons.json
    translations/en.json  de.json  fr.json  it.json
    brand/icon.png (if supported for custom integrations, verify) 
  tests/
    conftest.py  test_config_flow.py  test_init.py  test_light.py  test_cover.py
    test_event.py  test_button.py  test_sensor.py  test_diagnostics.py
```

### 4.3 manifest.json
```json
{
  "domain": "zeptrionair",
  "name": "Feller zeptrionAir",
  "codeowners": ["@diaznet"],
  "config_flow": true,
  "documentation": "https://github.com/diaznet/ha-zeptrionair",
  "issue_tracker": "https://github.com/diaznet/ha-zeptrionair/issues",
  "integration_type": "device",
  "iot_class": "local_push",
  "loggers": ["zeptrionair_api"],
  "requirements": ["zeptrionair-api==0.x.y"],
  "version": "0.1.0",
  "zeroconf": [
    "_zapp._tcp.local.",
    {"type": "_http._tcp.local.", "name": "zapp-*"}
  ],
  "dhcp": [{"hostname": "zapp-*"}, {"registered_devices": true}]
}
```
Checked with Context7:
- `_http._tcp` is a generic service type, and the HA docs require a `name` or `properties` filter for such types.
- DHCP matchers use fnmatch patterns.
- `registered_devices: true` only works if the device is registered with `CONNECTION_NETWORK_MAC`. The library therefore reads **`GET /zrap/net/mac`**, that single attribute only. The full `/zrap/net` response can include the Wi-Fi password while the device is in AP mode, so it's never fetched. The MAC goes into `DeviceInfo.connections`.
- Still to check on real DHCP traffic: the case of the hostname (`zapp-*` vs `zApp-*`). If needed, use the pattern `[zZ][aA][pP][pP]-*`.

### 4.4 Config flow (one config entry per zApp device)
- **Steps:**
  - `user`: enter a host (hostname or IP).
  - `zeroconf`: from mDNS discovery, then `confirm`.
  - `dhcp`: from DHCP discovery, then `confirm`.
- **Unique ID:** the serial number from `/zrap/id`.
  - Discovery flows call `_abort_if_unique_id_configured(updates={CONF_HOST: ...})`, which updates the host when its IP changes.
  - The user step calls `async_set_unique_id(serial, raise_on_progress=False)`.
- **Validation:** `identify()` and `probe()`. Errors: `cannot_connect`, `not_zeptrion`, `unknown`.
- **Title:** the device type plus the first channel's name, falling back to `zApp-<serial>`.
- **Reconfigure flow:** change the host.
- **Options flow:**
  - Per cover channel: optional position estimation (on/off), travel time up and travel time down (in seconds).
  - Per dimmer channel: full-range dim time in ms (default 5000, marked experimental).
  - **Decision (checked with Context7):** an **options flow with a menu** (choose a channel, then edit its settings), not config subentries. Subentry flows can only be started by the user (`user`/`reconfigure` steps) and are meant for things the user adds. Our channels are discovered from the device and fixed.

### 4.5 Runtime
- `async_setup_entry`:
  1. Create a `ZeptrionDevice` using HA's shared aiohttp session.
  2. Load `identify`, `channels`, `states`, `smartfront` and `probe`, raising `ConfigEntryNotReady` on failure.
  3. Store the result in `entry.runtime_data`.
- **`ZeptrionCoordinator` (DataUpdateCoordinator):**
  - Holds the `states` dict, connectivity, the last key event and the RSSI.
  - A background task (`entry.async_create_background_task`) consumes `device.events()`:
    - a `ChannelUpdate` updates the state and calls `async_set_updated_data`;
    - a `KeyEvent` is sent to the event entities through the dispatcher;
    - a `ConnectionEvent` sets availability, and on reconnect a fresh `states()` resync runs.
  - A safety poll every 5 minutes (`chscan` + `rssi`), and every 30 s if push isn't available.
  - Shutdown cancels the background task (`async_on_unload`).
- **Device registry:**
  - One device per zApp: identifiers `(DOMAIN, serial)`, manufacturer "Feller", model = `type`, `sw_version`, `hw_version`, `configuration_url=http://host`, and `suggested_area` taken from the first channel's `group`.
  - Optional per-channel sub-devices with `via_device`, so each channel's area can follow its own `group`. Decide during milestone 3. The simpler option, one device with entities, is the default.

### 4.6 Entities (`has_entity_name=True`, unique_id = `<serial>_ch<N>_<kind>`)
- **`light`** (cat 1 and 3):
  - cat 1: on/off.
  - cat 3: brightness reported from `val`.
  - `turn_on(brightness)` on a dimmer:
    1. If it's off, switch it on.
    2. Compute Δ = target − current.
    3. Send `dim_up_<t>` or `dim_down_<t>` with t = |Δ|/100 × full dim time (clamped to 100–32000 ms).
    4. After the push update, make one correction pass if the error is above 10 %.
  - Marked **experimental** until verified on hardware.
  - Entity name = the channel name from `chdes`.
- **`cover`** (cat 5 and 6):
  - `open_cover` → `open`, `close_cover` → `close`, `stop_cover` → `stop`.
  - `is_opening`/`is_closing` = motor running (`val` 100) combined with the known direction. The direction comes from the last HA command or a decoded key event (`btfu`); if neither is known, the cover is shown as moving with no direction.
  - **Position** (optional, per channel): estimated from travel time.
    - When the motor stops on its own after ≥ 95 % of the travel time, the position snaps to 0 or 100.
    - A movement in an unknown direction makes the position `None` until the next full open or close.
    - `set_cover_position` uses `move_open_<t>`/`move_close_<t>`.
    - `RestoreEntity` keeps the position across restarts.
  - Without position estimation, the cover uses `assumed_state=True` and supports open/close/stop only.
  - device_class: cat 5 → `shade` and cat 6 → `awning`. The user can change it with "Show as".
- **`button`** (light and cover channels): `recall_scene_1..4`, disabled by default (`translation_key`).
- **`event`**:
  - One per Smartfront key that is in use (`btfu` ≠ 0), only on devices with a Smartfront.
  - Event types: `pressed` and `released`, plus attributes `function` (`smartbutton`, `channel_up`, `channel_down`, `unknown`) and the channel(s).
  - `translation_key` = `smartbutton` or `key`, with the key number in `translation_placeholders`.
- **`sensor`** (diagnostic):
  - Wi-Fi signal (dBm, `SIGNAL_STRENGTH`, disabled by default).
  - Smartfront temperature, illuminance and humidity from `/zapi/smartfront/sensors`, created only for values that aren't `"none"`. Temperature is disabled by default and labelled "device temperature", because it's warmed by the module itself (~35 °C observed).
- **Availability:** all entities become unavailable when the coordinator reports disconnected for more than 60 s.

### 4.7 Diagnostics
Includes the anonymised `id`, `chdes`, `chscan`, smartfront `id`/`led`, capabilities, the last 50 raw events, and the options. The serial number, host and IP are redacted.

### 4.8 Translations (EN, DE, FR, IT)
- `translations/en.json` is the source of truth. `de.json`, `fr.json` and `it.json` are kept complete, and CI fails on a missing key.
- Covered: the config, options and reconfigure flows (steps, data, data_description, errors, aborts); entity names via `translation_key`; event types and attributes; and exception messages (`exceptions` + `translation_key` on `HomeAssistantError`).
- Channel names from the device aren't translated.
- **Checked with Context7:** custom integrations must **not** use `strings.json` or `[%key:…%]` placeholders. Those only work at build time in HA Core. Ship full, flat text in `translations/en.json`, `de.json`, `fr.json` and `it.json`, and validate them with hassfest.

### 4.9 Quality targets
- Aim for Bronze/Silver rules of the HA integration quality scale, even though custom integrations aren't graded:
  - config flow test coverage of 100 %;
  - `runtime_data`;
  - unique IDs;
  - entity translations;
  - diagnostics;
  - reconfigure flow;
  - discovery updating the host;
  - handling of unavailable devices;
  - log-once on unavailable.
- Test coverage of at least 90 % overall.

### 4.10 Documentation
- README:
  - supported hardware matrix: tested (3340-2-B, fw 01.08.28, on/off + blind) vs untested (A models, 4-channel, dimmers, awnings, older firmware, sensor fronts);
  - installation through HACS (custom repository, later the default store);
  - discovery and manual setup;
  - options;
  - troubleshooting, including an mDNS note and DHCP;
  - "how to contribute fixtures" (`zeptrion dump --anonymise`).
- Migration guides:
  - from YAML `rest`/`rest_command`/template entities: remove the YAML, then rename the new entity IDs to the old ones, and note that a `switch.*` becomes a `light.*`;
  - from `heldchen/ha-zeptrion-air`: the domains differ, so both can run during migration.
- Attribution to heldchen/ha-zeptrion-air (MIT) for protocol knowledge, plus a disclaimer that the project isn't affiliated with Feller AG.

---

## 5. CI/CD (GitHub Actions)

Both repositories are on GitHub. Actions are pinned to a commit SHA, Dependabot updates the GitHub Actions and pip dependencies, and the `main` branch is protected with required checks.

### 5.1 `zeptrionair-api`
**`.github/workflows/ci.yml`**, on push and PR:
- `lint`: ruff check, ruff format --check, mypy --strict.
- `test`: a matrix of Python 3.12/3.13/3.14 × ubuntu/windows, running `uv run pytest --cov`, with a coverage gate of ≥ 90 %.
- `build`: `uv build`, then `twine check` on the sdist and wheel, and a smoke test that installs the wheel and runs `zeptrion --help`.

**`.github/workflows/release.yml`**: publishes to PyPI with **Trusted Publishing (OIDC)**. No API tokens are stored anywhere.
- Trigger: a pushed tag `v*.*.*`. Tags are protected, so only maintainers can create them.
- Jobs:
  1. `build`: check out with `fetch-depth: 0` (hatch-vcs needs the tags), run `uv build`, and upload `dist/` as an artifact.
  2. `publish-testpypi`: needs `build`, uses `environment: testpypi` and `permissions: id-token: write` **at job level only**, and runs `pypa/gh-action-pypi-publish@release/v1` with `repository-url: https://test.pypi.org/legacy/`.
  3. `verify-testpypi`: in a fresh virtual environment, `pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ zeptrionair-api==<tag>`, then import the package and run `zeptrion --help`.
  4. `publish-pypi`: needs `verify-testpypi`, uses `environment: pypi` (with a **required reviewer**, i.e. manual approval) and `id-token: write`, and runs `pypa/gh-action-pypi-publish@release/v1`. Optionally adds attestations.
  5. `github-release`: creates a GitHub Release with the changelog section and the dist files attached.
- Pre-release tags (`v0.2.0rc1`) go to TestPyPI only.
- **One-time setup, done by you on the websites (I can't do this):**
  1. On pypi.org, go to *Your projects → Publishing → Add a pending publisher*: project `zeptrionair-api`, owner/repo, workflow `release.yml`, environment `pypi`. A pending publisher lets the first upload create the project, so the name is reserved by the first release.
  2. Do the same on test.pypi.org with environment `testpypi`.
  3. On GitHub, under *Settings → Environments*, create `testpypi` and `pypi`, with yourself as required reviewer on `pypi`.
  4. Add a tag protection rule for `v*`.
- Source: PyPI Trusted Publishing docs, checked with Context7 (`id-token: write` at job level, a GitHub environment strongly recommended, and `repository-url` for TestPyPI).

### 5.2 `ha-zeptrionair`
**`.github/workflows/validate.yml`**, on push, PR and a daily schedule:
- `hassfest`: `home-assistant/actions/hassfest@master` checks the manifest and the `translations/*.json` files.
- `hacs`: `hacs/action@main` with `category: integration`.
- `lint`: ruff.
- `test`: pytest with `pytest-homeassistant-custom-component` on Linux, with a coverage gate.
- `translations`: a script that checks every key in `en.json` exists in `de`, `fr` and `it`, and that none has extra keys.

**`.github/workflows/release.yml`**, triggered by a pushed tag `v*.*.*`:
1. Check that `manifest.json` `version` equals the tag. On a mismatch, fail. The version is bumped in a PR, not by CI.
2. Check that the `requirements` pin `zeptrionair-api==X.Y.Z` exists on PyPI.
3. Build `zeptrionair.zip` from `custom_components/zeptrionair/`. `hacs.json` sets `"zip_release": true, "filename": "zeptrionair.zip"`, which makes HACS installs faster and reproducible.
4. Create a GitHub Release with the changelog notes and the zip attached. HACS picks up new versions from GitHub Releases.

**Release order:** library first (PyPI), then a PR that bumps `requirements` in the integration's manifest, then the integration tag. Dependabot watches `zeptrionair-api` on PyPI and opens that bump PR automatically.

---

## 6. Milestones and acceptance criteria

**M0b: workspace and fixtures**
- Add `.gitignore`, README skeletons and `PLAN.md` to both repos. Add the MIT `LICENSE` to `ha-zeptrionair`; `zeptrionair-api` already has one.
- Capture anonymised fixtures from the live devices. The repos are public, so:
  - real serials are replaced by fakes such as `99990001`;
  - hostnames, IPs and the MAC are removed;
  - channel and room names become generic ("Light 1", "Shade 1", "Room A"). Saved to `zeptrionair-api/tests/fixtures/`:
  - `id`, `chdes`, `chscan`, `rssi`, smartfront `id`/`led`, and the 404 bodies;
  - WebSocket sequences: light on/off, cover down/up/stop/self-stop, Smartbutton press, idle.
- Write `docs/protocol.md` from section 2.
- *Done when:* the fixtures are in place, and no real serials, hostnames, IPs, MACs or room names appear in them (checked with grep). The user commits.
- **Status (2026-11-02): done.** `scripts/capture_fixtures.py`, `tests/fixtures/devices/` (8 devices), `tests/fixtures/ws/` (4 sequences), `docs/protocol.md`, `PLAN.md`, `.gitignore`, and the `ha-zeptrionair` LICENSE. Nothing committed.

**M1: library core**
- `parsing`, `models`, `device` (all GET/POST), `events` (WebSocket + `chnotify` + reconnect), and the fake device.
- *Done when:* tests pass against fixtures and the fake device (coverage ≥ 90 %, mypy strict clean), and an ad-hoc script on real hardware toggles a light and receives its `eid1`.
- Also in M1: WebSocket ping behaviour tested on real hardware, and liveness strategy decided.

**M2: CLI and first release**
- All CLI commands, `discover`, `watch`, and `dump --anonymise`.
- The `ci.yml` and `release.yml` workflows (section 5.1). You set up the pending publishers and the GitHub environments.
- Tag `v0.1.0rc1` (goes to TestPyPI only), then `v0.1.0` (goes to PyPI after your approval).
- *Done when:* `uvx --from zeptrionair-api[cli] zeptrion watch <host>` shows live events, and `discover` finds devices on the LAN.

**M3: integration MVP**
- Config flow (user/zeroconf/dhcp/reconfigure), coordinator with push, `light` (on/off + experimental dimmer), `cover` (open/close/stop + live moving state), and the EN/DE/FR/IT translations.
- *Done when:*
  - HA tests pass in WSL;
  - all test-bench devices are discovered or added;
  - the lights and covers work from HA;
  - wall presses show up in HA within 1 s;
  - unplugging a device makes its entities unavailable, and they recover afterwards.

**M4: covers+, events, buttons, diagnostics**
- Position estimation with options.
- Key `event` entities with `btfu` decoding.
- Scene `button`s.
- RSSI sensor, diagnostics, `RestoreEntity`.
- *Done when:* the estimated position stays within ±10 % after open → 50 % → close cycles on the test bench, and a Smartbutton press triggers an HA automation.

**M5: release**
- The `validate.yml` and `release.yml` workflows (section 5.2) green, README with the hardware matrix and migration guides, brand assets, and a GitHub release `v0.1.0` with `zeptrionair.zip`.
- Optionally, a submission to the HACS default repository list.
- *Done when:* the integration installs from HACS as a custom repository in a clean HA instance.

**M6 (later):** Smartfront sensors (needs hardware or user fixtures), LED background colour as a `light` entity, Smartbutton programming, and calibrating dimmers with community fixtures.

---

## 7. Risks and open questions
| Risk / question | Mitigation |
|---|---|
| No dimmer, awning, 4-channel or A-model hardware to test on | Fake device + clear "untested" labels + `dump --anonymise` fixture contributions |
| `btfu` decoding is inferred from 4 devices | Raw fallback; table-driven tests; collect more `btfu` strings from users |
| WebSocket idle behaviour/keep-alive unknown | Test in M1; liveness GET fallback |
| Embedded web server connection limits | Max 2 concurrent HTTP requests per device, one event stream; verified 2 parallel WebSockets OK |
| mDNS unreliable | DHCP discovery + manual entry + host auto-update |
| `cover` `val` semantics (motor running) seen on blinds only | Verify on awnings via community; keep logic in one place |
| PyPI name taken before the first release | Reserve it through the pending publisher; the first `rc` release creates the project |
| Feller's API PDF (`docs/10ZEPAPIE1612.pdf`) in a public repo | **User decision: commit it.** `docs/protocol.md` remains the maintained reference. |
| HA API drift | Context7 before each HA component; pin a minimum HA version in `hacs.json` |
