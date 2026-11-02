# zeptrionAir local protocol

This document describes the local HTTP and WebSocket interface of Feller zeptrionAir WLAN devices ("zApp"). It combines:

- **[PDF]** Feller's official "zApp API" document `10.ZEPAPI-E.1612` (Version 1.0), in this folder as `10ZEPAPIE1612.pdf`;
- **[OBS]** behaviour observed on real devices (3340-2-B, firmware 01.08.28, captured 2026-11-02), with fixtures in `tests/fixtures/`;
- **[HEUR]** inferences drawn from those observations. These may not hold on other hardware.

The WebSocket endpoint and its message format are **not documented by Feller** at all; everything about them is [OBS].

> Feller has discontinued zeptrion/zeptrionAIR. No newer official API document is known.

---

## 1. Devices

| Product | Type | Channels | Notes |
|---|---|---|---|
| WLAN-Nebenstelle 4K | 3340-4-A / 3340-4-B (V2) | 4 | untested |
| WLAN-Zwischenmodul 2K | 3340-2-A / 3340-2-B (V2) | 2 | 3340-2-B tested |

[PDF] A Smartfront (front plate with keys, RGB LEDs and optional sensors) can be fitted on the 3340-2-B.

## 2. Transport

- HTTP on port 80, **no authentication**, no TLS.
- Base paths: `/zrap/…` (XML) and `/zapi/…` (JSON).
- **Reads:** `GET`, response `text/xml`. [OBS] The whole document is on a single line, with an XML declaration of `encoding="US-ASCII"` or `"UTF-8"`. [PDF] Special characters are escaped as XML entities (`&amp;`, `&lt;`, …).
- **Writes:** `POST` with an `application/x-www-form-urlencoded` body, percent-encoded UTF-8 (`name=K%C3%BCche`). [PDF] Success is **HTTP 302**; clients must not follow the redirect.
- **Errors:** [PDF] HTTP status ≥ 400. [OBS] An unknown path returns 404 with a small HTML page.
- [OBS] Responses take about 30–330 ms. The device is a small embedded server, so keep concurrent requests low (≤ 2 per device recommended).
- [PDF] The examples in the PDF contain malformed XML (`</hnotify>`, `<chdes>` used as a closing tag). [OBS] Real responses were well-formed, but parsers should still be lenient.

## 3. Discovery

- **mDNS:** [PDF] firmware ≥ 01.08.00 registers `_zapp._tcp`, and for backward compatibility also `_http._tcp`. Older firmware registers `_http._tcp` only.
  - Instance and host name: `zapp-<serial>` (`zapp-YYWWNNNN.local`, where YY = production year, WW = production week, NNNN = sequence number).
  - [OBS] TXT records: `type=3340-2-B`, `sw=01.08.28`, and an empty `path`.
  - [OBS] **Unreliable:** one of 8 devices never answered an mDNS browse within 30 s, although it answered HTTP. Always provide manual host entry.
- [PDF] The hostname equals the factory SSID (`zApp-<serial>`), but case-insensitive.
- [PDF] In access-point mode (factory default) the device is at `192.168.0.1`.

## 4. Endpoints

### 4.1 `GET /zrap/id`: device identification
```xml
<id><hw>01.05.02</hw><sn>99990001</sn><sys>ZEPTRION</sys><type>3340-2-B</type><oen>zApp</oen>
<sw>01.08.28 (Jun  1 2018 09:13:39)</sw><boot>01.04.02 (Apr 13 2018 10:08:29)</boot></id>
```
- `sn` is the serial number, unique per device, and matches the hostname.
- Version format: `MM.mm.bb<other text>`, each part 2 digits; higher numbers are newer. A single attribute can be read with `/zrap/id/<attr>`.

### 4.2 `GET|POST /zrap/chdes[/chN[/attr]]`: channel descriptors
```xml
<chdes><ch1><name>Light 1</name><group>Room A</group><icon>1492</icon><type>00</type><cat>1</cat></ch1>…</chdes>
```
| Attribute | Max bytes (UTF-8) | Meaning |
|---|---|---|
| `name` | 32 | channel name |
| `group` | 32 | room / group |
| `icon` | 24 | icon ID used by the Feller app |
| `type` | 4 | free use |
| `cat` | 4 | **channel category**, see below |

- [PDF] These are free-form fields for client apps.
- [OBS] The Feller app stores its channel configuration in them, and `cat` is the field that reliably identifies what is connected:

| `cat` | Meaning | Source |
|---|---|---|
| `1` | on/off light | OBS |
| `3` | dimmer | HEUR (from other projects, untested) |
| `5` | blind / shade / shutter | OBS |
| `6` | awning (Markise) | HEUR (from other projects, untested) |
| `00`, `-1` | unused channel | OBS; the name defaults to `zApp-<serial> (<n>)` |

To write: `POST /zrap/chdes/ch1` with body `name=Kitchen&group=First+floor`.

### 4.3 `GET /zrap/chscan[/chN]`: channel state
```xml
<chscan><ch1><val>0</val></ch1><ch2><val>100</val></ch2></chscan>
```
- Lights: 0 = off, 100 = on. Dimmers: 0–100 (HEUR).
- [PDF] says "-1 for blinds". [OBS] On 01.08.28, covers report **0 = motor stopped, 100 = motor running**. See section 5.

### 4.4 `GET /zrap/chnotify`: long poll
- [PDF] Same format as `chscan` (root element `<chnotify>`).
- The request is held open until a channel changes, or for at most 30 s; then the client re-requests.
- Use it as a fallback push channel when the WebSocket isn't available.

### 4.5 `POST /zrap/chctrl/chN` and `POST /zrap/chctrl`: control
Single channel: `POST /zrap/chctrl/ch1` with `cmd=on`.
Several channels in one request: `POST /zrap/chctrl` with `cmd1=on&cmd2=off`.

| `cmd` | Effect |
|---|---|
| `on`, `off`, `toggle` | light on (100 %), off, or toggle |
| `dim_up`, `dim_down` | start dimming (`stop` to hold) |
| `open`, `close` | move the cover fully open / closed |
| `move_open`, `move_close` | start moving the cover (`stop` to hold) |
| `stop` | stop dimming or moving |
| `recall_s1` … `recall_s4` | recall scene 1–4 |
| `store_s1` … `store_s4` | store the current state as scene 1–4 |
| `delete_s1` … `delete_s4` | delete scene 1–4 |
| `dim_up_<t>`, `dim_down_<t>`, `dim_<t>` | dim for *t* ms, then stop (t = 100–32000) |
| `move_open_<t>`, `move_close_<t>` | move for *t* ms, then stop (t = 100–32000) |

### 4.6 `GET /zrap/rssi`: Wi-Fi signal
`<rssi><dbm>-64</dbm></rssi>`. [PDF] Below −75 dBm the connection is unreliable.

### 4.7 `GET /zrap/net/mac`: MAC address
`<mac>02:00:00:00:00:01</mac>`

> ⚠️ **Never request `/zrap/net` as a whole.** [PDF] It includes the Wi-Fi password `pw` while the device is in access-point mode. Read only `/zrap/net/mac`.

### 4.8 Smartfront: `/zapi/smartfront/*` (JSON)
Present only when a Smartfront is fitted; otherwise the endpoints return 404.

**`GET /zapi/smartfront/id`**
```json
{"btfu":"0,0,1000,1000,8501,8602,4501,4602","sw":"01.01.08","hw":"01.00.00","sys":"ZEPTRION","sid":"1","type":"920-3302.36.ZU"}
```
`btfu` describes the key functions; see section 6.

**`GET /zapi/smartfront/sensors`**: [OBS] the path is **plural**. The PDF's `/zapi/smartfront/sensor` returns 404.
```json
{"temp":"35.44C","lux":"none","hum":"none"}
```
- Values are strings with units; `"none"` means the sensor isn't present.
- [OBS] Every front tested reports `temp`. [PDF] The temperature is affected by how the device is installed. [OBS] Readings around 35 °C suggest it measures the module's own heat.

**`GET|POST /zapi/smartfront/led`**
```json
[{"id":1,"on":false,"rgb":"#000000","effect":"#fffff","bg":"#000020"}, …]
```
- [OBS] The field is named `effect`; the PDF says `effects`.
- [PDF] Only `bg` (background colour) may be changed. `on`, `rgb` and `effect` are managed by the device itself.
- Set: `POST` a JSON array, e.g. `[{"id":2,"bg":"#220000"}]`.

**Smartbutton programming** (`/zapi/smartbt/prgm|prgn|prgs`): [PDF] programs a key to send HTTP requests. [OBS] `GET /zapi/smartbt/prgm` returns `{"on":false,"ntm":60}` on most fronts, but **404 on front type `920-3305.36.ZZ`**, so probe before use.

### 4.9 Other endpoints (not used by this library)
| Endpoint | Purpose |
|---|---|
| `/zrap/sys` | `POST cmd=reboot` / `factory-default` / `network-default`. **Destructive.** |
| `/zrap/net`, `/zrap/netscan` | Wi-Fi provisioning (see the warning in 4.7) |
| `/zrap/loc` | free-form location string |
| `/zrap/date`, `/zrap/ntp` | clock |
| `/zrap/scheduler` | on-device timers |

## 5. WebSocket `ws://<host>/zrap/ws` [OBS]

**Undocumented by Feller.**
- No sub-protocol and no authentication. The server sends JSON text frames; no client messages are needed.
- Several clients can be connected at the same time, and each receives every frame. Two parallel connections were tested.
- No frames were seen while idle for 150 s, so a client can't use silence to detect a dead connection.

### 5.1 `eid1`: channel value changed
```json
{"eid1":{"ch":1,"val":100}}
```
- Lights: 0 / 100.
- **Covers: 100 = motor started, 0 = motor stopped.** This happens when a command arrives, when a wall key is pressed, and when the motor stops by itself at its end position.
- The direction is **not** included. It has to come from the command sent or from the key that was pressed (section 6).

### 5.2 `eid2`: key state
```json
{"eid2":{"bta":"......P.."}}
```
- 9 characters: `P` = pressed, `.` = not pressed.
- When the key is released, a frame with all dots follows.
- Position *i* (0-based) is key *i+1*. The meaning of position 9 is unknown.

### 5.3 Typical sequence for a wall press
```
eid2 bta ".......P."   press key 8
eid2 bta "........."   release (~0.1–0.3 s later)
eid1 ch1 val 100        resulting state change
```
A Smartbutton press produces `eid2` on the device that was pressed, and `eid1` on whatever device its programmed request targets, which may be a different device.

## 6. Key functions (`btfu`) [HEUR]

`btfu` from `/zapi/smartfront/id` is a list of 8 comma-separated codes, one for each key 1–8 (key *k* = `bta` position *k−1*).

| Code | Meaning |
|---|---|
| `0` | key unused |
| `1000` | Smartbutton: sends its programmed HTTP request(s) |
| `XYZZ` | local channel function. **X** = channel bitmask (8 = ch1, 4 = ch2; presumably 2 = ch3, 1 = ch4). **Y** = direction: 5 = up/on, 6 = down/off. **ZZ** = unknown (01 for up/on, 02 for down/off in all samples). |

Observed layouts:

| Device | `btfu` | Keys |
|---|---|---|
| 2× light | `0,0,1000,1000,8501,8602,4501,4602` | 3–4 Smartbutton, 5/6 ch1 on/off, 7/8 ch2 on/off |
| 1× cover | `0,0,1000,1000,0,0,8501,8602` | 3–4 Smartbutton, 7/8 ch1 up/down |
| 2× cover | `0,0,1000,1000,8501,8602,4501,4602` | 3–4 Smartbutton, 5/6 ch1 up/down, 7/8 ch2 up/down |

Behaviour observed on covers: pressing the opposite direction while the motor runs **stops** it, and pressing the same direction again starts moving.

Treat unknown codes as raw key events. Without a Smartfront, wall-key direction is unknown.

## 7. Security notes

- The local API has no authentication: anyone on the network can control the devices and rename channels.
- Never fetch `/zrap/net` (it can contain the Wi-Fi password); never call `/zrap/sys`.
