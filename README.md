# oupes

[![CI](https://github.com/Pep3M/oupes_mega_1_api/actions/workflows/ci.yml/badge.svg)](https://github.com/Pep3M/oupes_mega_1_api/actions/workflows/ci.yml)

Local-network backend, dashboard, and Home Assistant integration for Oupes
power stations.

The official Cleanergy app routes through an Oupes-controlled cloud and
requires an account. This project skips that entirely and talks straight to the
device on your LAN using the plaintext JSON-over-TCP protocol it already
exposes. No cloud, no account, no internet needed once the device is on WiFi.

Developed against an **Oupes Mega 1** (`pid O44A5o`). The Exodus 1500
(`pid pba1j6`) speaks the same protocol and should work; other models very
likely do too. If yours reports as `Unknown (<pid>)`, open an issue with the
output of `discover.py` and it can be added to the model map.

Read-only for now — see [`PLAN.md`](PLAN.md) for the work on write commands.

## Architecture

```
   ┌──────────────────┐         ┌────────────────────────┐
   │  Oupes device    │◀──TCP──▶│  backend (FastAPI)     │
   │                  │  :5555  │  /status  /raw  /ws    │
   └──────────────────┘  :6095  │  /history /discover    │
            ▲                   └────────────┬───────────┘
            │ UDP discovery                  │
            │                          ┌─────┴────────┐
            │                          │              │
                                  ┌────▼────┐    ┌────▼──────┐
                                  │ HA      │    │ Dashboard │
                                  │         │    │ (HTML/JS) │
                                  └─────────┘    └───────────┘
```

## Requirements

- The device provisioned onto your WiFi (do this once in the Cleanergy app;
  after that the app is not needed).
- A host on the **same subnet** as the device — the protocol is LAN-only and
  will not cross a router or a guest-network boundary.
- Docker, or Python 3.12+ for the standalone scripts.

## Quick start

```
git clone https://github.com/Pep3M/oupes_mega_1_api.git
cd oupes_mega_1_api
docker compose up -d --build
```

Then open <http://localhost:8765> for the live dashboard.

That is the whole happy path. No config file is needed — the backend finds the
device on the LAN by itself, and `docker-compose.yml` supplies a default for
every variable.

Discovery takes a moment, though. It broadcasts first, then falls back to a
unicast sweep of your local /24, which costs ~30s per subnet. So on a first
start `/status` answers with `connected: false` for a while before anything
shows up. Watch it happen:

```
docker compose logs -f oupes-api
```

To pin the address instead of discovering it — faster, and the reliable choice
if you have more than one device or your AP blocks broadcast:

```
cp .env.example .env      # then set MEGA_HOST
```

Prefer no Docker? `python3 monitor.py` gives you a terminal dashboard with no
dependencies beyond the standard library.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MEGA_HOST` | *(auto-detect)* | Device address. Set it to skip discovery. |
| `HISTORY_SIZE` | `600` | Samples in the in-memory `/history` ring (~10 min). |
| `PORT` | `8765` | Port for the API and dashboard. |
| `DISCOVERY_RETRY_S` | `30` | Seconds between retries while no device is found. |
| `LOG_LEVEL` | `INFO` | Backend log verbosity. `DEBUG` to see discovery detail. |

The backend uses `network_mode: host` so UDP discovery reaches the LAN. If you
pin `MEGA_HOST`, you can switch to bridge networking with a published port
instead — only the `/discover` endpoint needs the broadcast domain.

## Security

**The backend has no authentication and is not meant to face the internet.**
Anyone who can reach the port can read your device's telemetry, and once write
commands land (see `PLAN.md`) they would be able to control it. Keep it on a
trusted LAN, behind a reverse proxy with auth, or on a VPN. Do not port-forward
it.

The device protocol itself is unauthenticated plaintext — that is the device's
design, not this project's choice. Anyone already on your network can talk to
it whether or not you run this.

## Endpoints

| Path        | Method | Purpose                                            |
|-------------|--------|----------------------------------------------------|
| `/`         | GET    | Browser dashboard (served from `backend/static/`). |
| `/status`   | GET    | Latest merged state + device info.                 |
| `/raw`      | GET    | Raw `attr_id → value` map.                         |
| `/history`  | GET    | In-memory ring buffer (`?limit=`).                 |
| `/attrs`    | GET    | Attribute map + units.                             |
| `/discover` | GET    | Trigger a fresh LAN discovery.                     |
| `/ws`       | WS     | Live updates. First frame is a `snapshot`.         |

## Home Assistant

The integration lives in [`custom_components/oupes_local/`](custom_components/oupes_local)
and talks to this backend over HTTP, not to the device directly. Run the backend
first (see [Quick start](#quick-start)) and check that `/status` answers before adding it.

### Install via HACS (recommended)

HACS → ⋮ → **Custom repositories** → add `https://github.com/Pep3M/oupes_mega_1_api`
with category **Integration**. Then install **Oupes (local backend)** and restart
Home Assistant.

### Install manually

Copy `custom_components/oupes_local/` into your HA `config/custom_components/`
and restart. The layout has to end up as
`config/custom_components/oupes_local/manifest.json`.

### Configure

Settings → Devices & Services → **Add integration** → **Oupes (local backend)**.
It asks for the backend's base URL.

`http://localhost:8765` only works when Home Assistant and the backend share a
network namespace. In every other setup — HAOS, a supervised install, HA in its
own container — `localhost` is Home Assistant itself and the config flow will
fail with *cannot connect*. Use the backend host's LAN address instead, e.g.
`http://192.168.1.50:8765`.

Entities go unavailable when the backend loses its TCP session to the device
(`connected: false` in `/status`), rather than reporting stale numbers.

For a direct-to-device integration with no backend, see
[widewing/ha-oupes](https://github.com/widewing/ha-oupes).

## Troubleshooting

**Dashboard loads but everything is empty, `/status` says `connected: false`.**
The backend is up and has not found the device. Give the sweep ~30s, then check
the logs. If it keeps retrying, the device is not reachable from this host: run
`python3 discover.py` to confirm, and if that finds nothing either, you are
probably on a different subnet or a guest network. The protocol does not cross
a router.

**`discover.py` finds nothing but the Cleanergy app works.** The app goes
through Oupes' cloud, so it works from anywhere and proves nothing about LAN
reachability. Pass the subnet explicitly — `python3 discover.py 192.168.1` —
in case the interface guess is wrong.

**Your model shows up as `Unknown (<pid>)`.** It will still stream telemetry;
only the display name is missing. Open an issue with the `discover.py` output
and it gets added to the map.

**HA shows the integration as "not loaded".** HA found a
`custom_components/oupes_local/` directory but could not import it — almost
always a wrong layout. `manifest.json` has to sit directly inside that
directory, not one level deeper. Check for the real reason in Settings → System
→ Logs, filtered by `oupes`.

**The config flow fails with *cannot connect*.** The URL is wrong or the backend
is unreachable from HA. See the note about `localhost` above, and verify from
HA's own perspective rather than yours — if HA runs in Docker,
`docker exec homeassistant curl -s http://<host>:8765/status` is the test that
actually matters.

**Entities exist but are all unavailable.** The backend lost its TCP session to
the device (`connected: false`). That is the backend to fix, not the
integration.

## Scripts

`monitor.py` and `probe.py` take a device address as `argv[1]`, else
`$MEGA_HOST`, else auto-detect. `discover.py` takes an optional /24 prefix to
sweep (`python3 discover.py 192.168.1`) instead of guessing from your
interfaces — useful if the device sits on a subnet you are routed to but not
directly attached to.

| File                   | Purpose                                            |
|------------------------|----------------------------------------------------|
| `discover.py`          | Find Oupes devices on the LAN.                     |
| `monitor.py`           | Terminal dashboard, standard library only.         |
| `probe.py`             | Unicast discovery + raw TCP dump, for protocol work. |
| `tools/legacy/scan.py` | BLE advertisement scan. Only useful pre-WiFi-setup; needs `bleak`. |

## Development

```
pip install -r backend/requirements.txt -r requirements-dev.txt
python -m pytest
```

The tests run offline against captured frames and in-process fakes, so no
device is needed.

## Contributing

Protocol knowledge is the bottleneck here, not code. The most useful
contributions are `discover.py` output from a model that is not in the map yet,
and identifications for the undocumented attribute IDs listed in `PLAN.md` —
both are valuable with no code attached.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

Attribute IDs and product mappings started from
[widewing/ha-oupes](https://github.com/widewing/ha-oupes), extended by
first-hand inspection of a Mega 1.

## License

MIT — see [LICENSE](LICENSE).
