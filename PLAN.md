# Plan — Reverse engineering of the Oupes Mega 1 protocol

This document tracks the remaining work to (a) map every observed-but-undocumented attribute and (b) add a **write** path (commands) on top of the read-only stream that already works.

Current observed state (firmware `sv 1.2.0`, hardware `hv 1.2.16`, product_id `O44A5o`):

| ID  | name (from widewing/ha-oupes) | last seen | notes |
|-----|---|---|---|
| 1   | switch_status | 5 | `0b101` — bits documented as "1=unknown, 2=DC, 3=AC" |
| 2   | — | 0 | undocumented; periodic in same frame as 1/3/4/30/32/51/105 |
| 3   | battery_level | 100 |  |
| 4   | total_output_power | ~335 |  |
| 5   | ac_output_power | ~330 |  |
| 6   | dc12_output_power | 0 |  |
| 7   | pd_output_power | 0 |  |
| 8   | usb_output_power | 2–4 | non-zero with nothing plugged → standby? |
| 9   | — | 0 | undocumented; in same frame as 5–8/21–23 |
| 21  | total_input_power | ~40 |  |
| 22  | ac_input_power | ~29 |  |
| 23  | pv_input_power | ~13 | confirmed solar input is active |
| 30  | remaining_time | swings 5940↔18602 | suspicious; needs investigation under load |
| 32  | battery_temp_f_x10 | 1107 (110.7°F / ~43.7°C) |  |
| 51  | — | 0 | undocumented; same frame as 1/3/4/30/32 |
| 84  | — | 1 | arrives in `cmd:3` response frames (not `cmd:10` telemetry) |
| 105 | fast_charging | 0 |  |

## Phase 1 — Map undocumented attributes (read-only)

### Methodology
The device pushes two `cmd:10` frames per second. We log every transition of every attr id and correlate with deliberate state changes on the unit / via the app.

### Tooling to add
- A `recorder.py` script next to `monitor.py` that:
  - subscribes to the backend `/ws`
  - writes a JSONL log (`logs/YYYY-MM-DD.jsonl`) with `{t, attr_id, old, new, label}` for every transition
  - prints a colored diff in the terminal
- A small `label` CLI: `python recorder.py label "ac on"` writes a `{event: "ac on"}` line into the log so we can correlate.

### Experiments
For each, observe which attrs change. Repeat each toggle 3× to filter noise.

1. **AC output switch** (on / off from front panel)
2. **DC 12V switch** (on / off)
3. **USB-A** (plug + unplug a known-wattage load)
4. **USB-C / PD** (plug + unplug a known-wattage load)
5. **Solar input** (unplug / replug the PV cable)
6. **AC input** (unplug / replug the wall plug)
7. **Fast charging toggle** (if exposed in the app)
8. **Light / lamp button** (if present)
9. **App actions** (every toggle in the Cleanergy app, one at a time)
10. **Idle observation** for 30 minutes — see if `30` (remaining_time) settles or keeps oscillating.

### Hypotheses to validate
- `2` → light status (the Mega 1 has a built-in lamp)
- `9` → AC frequency, or anti-grid backfeed flag
- `30` oscillation → it's actually `(seconds remaining) % 65536` or a rolling 16-bit counter unrelated to time. Confirm by running the unit until ~50% battery under a steady load and checking the trend.
- `51` → error / status bitmask
- `84` → ack from a command we didn't send (boot/keepalive heartbeat?)

### Output
A PR back to `widewing/ha-oupes` adding the newly-mapped IDs.

## Phase 2 — Write path (commands)

Read of `widewing/ha-oupes`'s `tcp_client.py` is needed to confirm the write frame shape. From the `cmd:3 / attr:[84] / data:{"84":1}` we already observed, the likely command shape is:

```json
{"cmd": <write_cmd>, "pv": 0, "sn": "<ts>", "msg": {"attr":[<id>], "data": {"<id>": <value>}}}
```

Plan:
1. Read `tcp_client.py` end-to-end and document `cmd` values (we believe `0`=discovery, `10`=telemetry-push, `3`=command-ack; we still need the *issue-command* cmd value).
2. Replay benign commands (toggle USB-A) from a script while watching `cmd:10` frames change.
3. If something looks like authentication is required, capture the very first `cmd:3` exchange with `mitmproxy` on port 5555 (clear text, easy).
4. Add `POST /commands/{name}` to the backend with a whitelist (no arbitrary writes).
5. Add `switch` entities to the HA integration backed by those commands.

## Phase 3 — Stability and polish
- Persist `/history` to SQLite (volume in compose) so reboots don't lose data.
- Add `/metrics` (Prometheus exposition) so Grafana can chart it.
- Detect device drop-out (no frame for >5s → `connected=false`, raise an event).
- Add unit tests around `protocol.py` (golden frames from the captures).

## Out of scope (for now)
- BLE provisioning (we successfully avoided it once WiFi is configured).
- Cloud API. The local protocol is sufficient; the cloud path is unused once you stop using the official app.
- Multi-device. The backend currently assumes one `MEGA_HOST`. Generalize once we own a second unit.
