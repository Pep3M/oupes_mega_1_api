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

1. Find the device:
   ```
   python3 discover.py
   ```
   It broadcasts first, then falls back to a unicast sweep of your local /24.
   That fallback is there because plenty of consumer APs drop broadcast towards
   WiFi clients — on those networks broadcast discovery never works and only
   the sweep finds anything.

2. Copy the sample config:
   ```
   cp .env.example .env
   ```
   Auto-detection runs at startup, so you can leave `MEGA_HOST` unset. Setting
   it is the reliable option and skips discovery entirely — recommended if
   discovery was slow or flaky for you, or if you have more than one device.

   Note that a sweep takes ~30s per subnet, so on a first start the backend can
   be up and answering `/status` with `connected: false` for a while before it
   finds anything. `docker compose logs -f oupes-api` shows the progress.

3. Bring the backend up:
   ```
   docker compose up -d --build
   ```

4. Open <http://localhost:8765> for the live dashboard.

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

Copy `ha_integration/custom_components/oupes_local/` into your HA
`config/custom_components/`, restart, then add **Oupes (local backend)** from
Settings → Devices & Services. It asks for the backend's base URL, which
defaults to `http://localhost:8765` — change the host if HA runs on a different
machine from the backend.

The integration talks to this backend, not to the device directly. For a
direct-to-device integration, see [widewing/ha-oupes](https://github.com/widewing/ha-oupes).

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
