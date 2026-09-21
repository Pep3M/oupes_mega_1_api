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
| 30  | — (was `remaining_time`) | 244–6379, saturates at 5940 | **not a duration**; corr 0.90 with PV input power, 0.02 with output. Hypothesis: PV voltage in 0.01 V |
| 32  | battery_temp_f_x10 | 1107 (110.7°F / ~43.7°C) |  |
| 51  | — | 1 | undocumented; same frame as 1/3/4/30/32 |
| 53  | `expansion_pv_input_power` | 494 | **appeared with the B2**; the pack's own solar input — see below |
| 54  | — | 10 | undocumented; **appeared with the B2**; flat 9–10 whenever 53 is non-zero, 0 when it is not — not power |
| 78  | — | 5940 | **appeared with the B2**; holds whatever 30 holds — the expansion pack's analogue of the same unknown quantity |
| 79  | `expansion_battery_level` | 82 | **appeared with the B2**; see below |
| 80  | `expansion_battery_temp_f_x10` | 922 (92.2°F / ~33.4°C) | **appeared with the B2**; see below |
| 84  | — | 1 | arrives in `cmd:3` response frames (not `cmd:10` telemetry) |
| 101 | — | 1 | undocumented; **appeared with the B2** |
| 105 | fast_charging | 0 |  |

### The B2 expansion battery (2026-09-19)

Attaching a B2 expansion battery added six ids to the stream — 53, 54, 78, 79,
80 and 101 — none of which appears in the frames captured before it (the two
`cmd:10` frames fixed in `tests/test_protocol.py` carry exactly the documented
set) nor in widewing/ha-oupes. Two of the six were identifiable straight away:

- **79 is a second state of charge.** Sampled at 1 Hz under a 650 W charge it
  sat at 69–70 % while attribute 3 sat at 66–67 %, and the two stepped at
  different seconds. A pack's own charge cannot lead itself by three points.
  Re-checked after a reboot and a long charge, the pair had risen to 81/82 —
  still two independent readings, still converging rather than locked.
- **80 is a second temperature**, in attribute 32's tenths-of-Fahrenheit
  encoding: 915 against 32's 1051, i.e. 91.5°F against 105.1°F, which is what
  a second and cooler pack looks like.

A third fell out on 2026-09-21, when the panels were moved onto the B2:

- **53 is the pack's own solar input.** With nothing on the station's AC, 21
  (the input total) read 493 while 22 and 23 sat flat at 0 and 53 read 494.
  Over ~100 frames at 1 Hz it tracked 21 to within ±3 W in steady state, the
  wider excursions being cloud transients where the two land in different
  frames. In the AC session the day before, 53 was 0 and 22 carried the whole
  646 W. So 23 is the station's PV input and 53 is the pack's, which matches
  the hardware — a B2 takes solar and has no AC input of its own.

  This one surfaced as a visible defect rather than an experiment: the panel
  showed `ENTRADA · 459 W` above two channels both reading 0 W, a total its
  own breakdown could not account for.

54, 78 and 101 stay unmapped. 54 holds a flat 9–10 whenever 53 is non-zero and
0 when it is not, never moving while 53 swings between 155 W and 471 W, so
whatever it counts it is not power. 78 is the interesting one — it tracks the
still-unexplained 30 exactly, so whatever 30 turns out to mean, 78 is the
expansion pack's copy of it, and confirming one confirms both.

**The experiment that would settle this:** unplug the B2 and watch whether
79/80 leave the stream. They should. If they persist, the mapping is wrong and
the names have to be withdrawn — every consumer treats the pair as optional and
renders nothing when it is absent, so nothing breaks either way.

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
10. **Idle observation** for 30 minutes — see whether `30` settles or keeps oscillating.

### Hypotheses to validate
- `2` → light status (the Mega 1 has a built-in lamp)
- `9` → AC frequency, or anti-grid backfeed flag
- `30` → **PV string voltage in centivolts.** Ruled out as a duration on 2026-08-22: sampled at
  1 Hz for 600 frames with the pack pinned at 91%, it took 48 distinct values, oscillated second to
  second (296 → 244 → 267 → 296), saturated at 5940, and correlated 0.90 with PV input power versus
  0.02 with output power. Read as 0.01 V that is 59.4 V under load — a normal MPPT operating point —
  and the previously logged 18602 becomes 186 V, plausible as open-circuit string voltage. Confirm by
  metering the PV connector while watching the attribute, or by comparing against the voltage the
  Cleanergy app reports. Until then it stays unmapped as `attr_30` so no consumer presents it as time.
- **No attribute carries time-to-full or autonomy.** Both have to be derived from
  `total_input_power - total_output_power`, the battery level, and the pack capacity.
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
