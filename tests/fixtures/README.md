# Test fixtures

Real responses captured from zeptrionAir devices, anonymised. **No real serial numbers, MAC addresses, hostnames, IP addresses, channel names or room names may be committed here.**

## `devices/`

Captured with `scripts/capture_fixtures.py` (read-only GET requests only).

- `index.json` summarises each device: type, firmware, channel categories, Smartfront type, `btfu`, and the HTTP status of every probed endpoint.
- `device_NN/responses.json` maps each endpoint path to `{status, content_type, body}`. `body` is the exact response text, so parser tests see the real formatting.

| Device | Type | Firmware | Channels (cat) | Smartfront | Notes |
|---|---|---|---|---|---|
| device_01 | 3340-2-B | 01.08.28 | 1, 1 (2× on/off light) | 920-3302.36.ZU | |
| device_02 | 3340-2-B | 01.08.28 | 5, -1 | 920-3304.24.ZU | unused ch2 reports `cat` `-1` |
| device_03 | 3340-2-B | 01.08.28 | 5, 00 | 920-3304.24.ZU | unused ch2 reports `cat` `00` |
| device_04 | 3340-2-B | 01.08.28 | 5, 5 | 920-3305.36.ZU | |
| device_05 | 3340-2-B | 01.08.28 | 5, 5 | 920-3305.36.ZZ | `/zapi/smartbt/prgm` → 404 |
| device_06 | 3340-2-B | 01.08.28 | 5, 5 | 920-3305.36.ZU | |
| device_07 | 3340-2-B | 01.08.28 | 5, -1 | 920-3304.24.ZU | |
| device_08 | 3340-2-B | 01.08.28 | 5, -1 | 920-3304.24.ZU | never seen via mDNS during capture |

Captured 2026-11-02. On every device `/zapi/smartfront/sensor` (singular, as in the PDF) returns 404, while `/zapi/smartfront/sensors` (plural) returns 200.

## `ws/`

WebSocket `/zrap/ws` frames captured while keys were pressed on the wall. One JSON object per line:

```json
{"t": 1.1, "device": "device_01", "frame": "<raw text frame>"}
```

- `t` is seconds since the first frame of the sequence.
- `device` refers to the folders in `devices/`.
- `frame` is the exact text received.

| File | What happened |
|---|---|
| `light_wall_keys.jsonl` | device_01: key 5 (ch1 on, already on, so no `eid1`), key 3 Smartbutton → ch2 on, key 6 → ch1 off, key 8 → ch2 off, key 4 Smartbutton (no local effect) |
| `cover_wall_down_up_selfstop.jsonl` | device_02: key 8 (down) → motor runs (`val` 100); key 7 (up) while moving → stop (`val` 0); key 7 again → moves up; the motor stops by itself at the end position (`val` 0 with no key event) |
| `cover_long_travel.jsonl` | device_03: down for ~55 s, stop, then up again (the capture ended while it was moving) |
| `smartbutton_remote.jsonl` | Smartbutton key 3 on device_02 switches ch1 on device_01; device_01 reports `eid1` without any key event of its own |

Key numbers are 1-based: `bta` position *i* (0-based) is key *i+1*, and key *k* is described by entry *k* of `btfu`.

## Contributing fixtures from other hardware

```
python scripts/capture_fixtures.py OUTDIR HOST [HOST ...]
```

Check the output, then open a pull request. Fixtures from 3340-x-A models, 4-channel devices, dimmers, awnings, sensor fronts and older firmware are especially welcome.
