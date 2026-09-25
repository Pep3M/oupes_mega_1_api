"""Oupes LAN protocol primitives — UDP discovery + TCP telemetry frames.

Attribute IDs and product mappings derived from widewing/ha-oupes
(custom_components/oupes/const.py) plus first-hand inspection of a
Mega 1 (pid 'O44A5o', sv 1.2.0, hv 1.2.16).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable

_LOGGER = logging.getLogger(__name__)

UDP_PORT = 6095
TCP_PORT = 5555

MODEL_MAPPING: dict[str, str] = {
    "pba1j6": "Oupes Exodus 1500",
    "O44A5o": "Oupes Mega 1",
}

ATTR_MAPPING: dict[int, str] = {
    1:   "switch_status",
    3:   "battery_level",
    4:   "total_output_power",
    5:   "ac_output_power",
    6:   "dc12_output_power",
    7:   "pd_output_power",
    8:   "usb_output_power",
    21:  "total_input_power",
    22:  "ac_input_power",
    23:  "pv_input_power",
    # 30 was mapped as "remaining_time" in minutes. It is not a duration:
    # sampled at 1 Hz over 600 frames with the pack pinned at 91%, it moved
    # through 48 distinct values, oscillating second to second (296 -> 244 ->
    # 267 -> 296), saturating at 5940, and correlating 0.90 with PV input power
    # against 0.02 with output power. A time-remaining field does none of that.
    # Left unmapped so it surfaces as the honest `attr_30` rather than feeding
    # a bogus duration into Home Assistant history. See PLAN.md for the
    # PV-voltage hypothesis and the test that would confirm it.
    32:  "battery_temp_f_x10",
    53:  "expansion_pv_input_power",
    # 78 is the expansion pack's analogue of the still-unexplained 30: it holds
    # the same saturated 5940 whenever 30 does. Left unmapped for the same
    # reason 30 is.
    79:  "expansion_battery_level",
    80:  "expansion_battery_temp_f_x10",
    105: "fast_charging",
}

# 79/80 were identified on 2026-09-19, the day a B2 expansion battery was
# attached to the Mega 1, by watching them against the pack's own 3/32:
#   - 79 sat at 69-70% while 3 sat at 66-67%, and both climbed under a 650 W
#     charge, stepping at different seconds. A pack's state of charge cannot
#     lead itself by three points, so these are two packs.
#   - 80 read 915 against 32's 1051 -- the same tenths-of-Fahrenheit encoding
#     (91.5F vs 105.1F), which is what a second, cooler pack looks like.
# Neither id shows up in any frame captured before that day -- the two fixed in
# tests/test_protocol.py carry exactly the documented set -- which is what rules
# out "these were always here and we simply never looked".
# Neither appears in widewing/ha-oupes either, so this mapping is ours alone, and
# it is inferred from co-observation rather than documentation. Every consumer
# therefore treats the pair as optional and renders nothing when it is absent.
# Unplugging the B2 should make 79/80 leave the stream -- if they persist, this
# reading is wrong and the names have to go.
#
# 53 is the expansion pack's own solar input, identified 2026-09-21 with panels
# feeding the B2 and nothing on the station's AC: 21 (the input total) read 493
# while 22 and 23 sat flat at 0, and 53 read 494. Sampled at 1 Hz for ~100
# frames it tracked 21 to within +/-3 W in steady state, the wider excursions
# being cloud transients where the two land in different frames. In the AC
# charging session the day before, 53 was 0 and 22 carried the whole 646 W.
# So 23 is the station's PV input and 53 is the pack's -- which matches the
# hardware: a B2 takes solar and has no AC input of its own.
#
# 54 stays unmapped. It holds a flat 9-10 whenever 53 is non-zero and 0 when it
# is not, never moving while 53 swings between 155 W and 471 W, so whatever it
# counts it is not power.
EXPANSION_ATTRS: tuple[str, ...] = (
    "expansion_battery_level",
    "expansion_battery_temp_f_x10",
    "expansion_pv_input_power",
)


def has_expansion(attrs: dict[str, Any]) -> bool:
    """Whether the telemetry shows an expansion pack attached.

    Presence of the attribute is the whole test: the device stops reporting
    these ids when nothing is bolted on.
    """
    return attrs.get("expansion_battery_level") is not None

ATTR_UNITS: dict[str, str] = {
    "battery_level": "%",
    "total_output_power": "W",
    "ac_output_power": "W",
    "dc12_output_power": "W",
    "pd_output_power": "W",
    "usb_output_power": "W",
    "total_input_power": "W",
    "ac_input_power": "W",
    "pv_input_power": "W",
    "battery_temp_f_x10": "0.1°F",
    "expansion_battery_level": "%",
    "expansion_battery_temp_f_x10": "0.1°F",
    "expansion_pv_input_power": "W",
}


@dataclass
class DiscoveryResult:
    host: str
    did: str
    dtp: str
    pid: str
    mac: str
    rssi: int
    sv: str
    hv: str
    model: str

    @classmethod
    def from_response(cls, raw: dict, src_ip: str) -> "DiscoveryResult":
        msg = raw.get("msg") or {}
        pid = msg.get("pid", "")
        return cls(
            host=msg.get("ip") or src_ip,
            did=msg.get("did", ""),
            dtp=msg.get("dtp", ""),
            pid=pid,
            mac=msg.get("mac", ""),
            rssi=int(msg.get("rssi", 0)),
            sv=msg.get("sv", ""),
            hv=msg.get("hv", ""),
            model=MODEL_MAPPING.get(pid, f"Unknown ({pid})"),
        )

    def as_dict(self) -> dict:
        return asdict(self)


SWEEP_WORKERS = 64
SWEEP_TIMEOUT_S = 1.0


def _discovery_payload() -> bytes:
    return json.dumps(
        {"cmd": 0, "pv": 0, "sn": str(int(time.time() * 1000)), "msg": {}}
    ).encode()


def primary_ipv4() -> str | None:
    """Best-effort local address of the interface used to reach the LAN.

    Opening a UDP socket towards an off-link address does not send anything;
    it just makes the kernel pick a source address via the routing table.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET-1, guaranteed unrouted
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def local_ipv4s() -> list[str]:
    """Every private IPv4 this host appears to hold, primary interface first."""
    found: list[str] = []
    primary = primary_ipv4()
    if primary:
        found.append(primary)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in found:
                found.append(addr)
    except OSError:
        pass
    keep = []
    for addr in found:
        try:
            ip = ipaddress.IPv4Address(addr)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            keep.append(addr)
    return keep


def local_subnets() -> list[str]:
    """The /24 prefixes worth sweeping, e.g. ['192.168.1'], primary first.

    A /24 assumption is not always right, but sweeping a larger prefix is
    impractical and home LANs are overwhelmingly /24.

    Addresses ending in .1 are skipped: holding the gateway address means the
    network is one we host (a Docker bridge, a VM network) rather than a LAN we
    joined, and no power station is going to be sitting on it. Without this a
    machine running Docker sweeps eight phantom subnets before the real one.
    """
    subnets: list[str] = []
    for addr in local_ipv4s():
        prefix, last = addr.rsplit(".", 1)
        if last == "1":
            continue
        if prefix not in subnets:
            subnets.append(prefix)
    return subnets


def _broadcast_targets(subnets: Iterable[str]) -> list[str]:
    targets = ["<broadcast>", "255.255.255.255"]
    for subnet in subnets:
        directed = f"{subnet}.255"
        if directed not in targets:
            targets.append(directed)
    return targets


def discover(host: str | None = None, timeout: float = 3.0) -> list[DiscoveryResult]:
    """Send UDP discovery (unicast if `host` is given, else broadcast)."""
    payload = _discovery_payload()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    sock.settimeout(0.3)

    if host:
        targets = [host]
    else:
        # Directed broadcast as well as the all-ones form: some stacks with
        # several interfaces only deliver one of the two.
        targets = _broadcast_targets(local_subnets())
    for target in targets:
        try:
            sock.sendto(payload, (target, UDP_PORT))
        except OSError:
            continue

    results: list[DiscoveryResult] = []
    seen: set[str] = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp, src = sock.recvfrom(2048)
        except socket.timeout:
            continue
        try:
            obj = json.loads(resp.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        result = DiscoveryResult.from_response(obj, src[0])
        if result.host in seen:
            continue
        seen.add(result.host)
        results.append(result)
    sock.close()
    return results


def _probe_one(ip: str) -> DiscoveryResult | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(SWEEP_TIMEOUT_S)
    try:
        sock.sendto(_discovery_payload(), (ip, UDP_PORT))
        resp, src = sock.recvfrom(2048)
        return DiscoveryResult.from_response(json.loads(resp.decode()), src[0])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        sock.close()


def sweep(subnet: str) -> list[DiscoveryResult]:
    """Unicast-probe every host in a /24.

    Needed because plenty of consumer APs drop broadcast and multicast frames
    towards WiFi clients, so a wired host never sees a reply to the broadcast
    even though the device answers unicast perfectly well.
    """
    hosts = [f"{subnet}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
        return [r for r in pool.map(_probe_one, hosts) if r is not None]


def discover_all(timeout: float = 3.0, subnets: Iterable[str] | None = None) -> list[DiscoveryResult]:
    """Broadcast discovery, falling back to a unicast sweep of the local /24s.

    Stops at the first subnet that yields anything, so the common case costs one
    sweep rather than one per interface.
    """
    results = discover(timeout=timeout)
    if results:
        return results
    for subnet in (subnets if subnets is not None else local_subnets()):
        found = sweep(subnet)
        if found:
            return found
    return []


HEARTBEAT_INTERVAL_S = 10.0
# The device pushes a frame every few seconds and answers each heartbeat, so a
# silence this long means it is gone, not idle.
READ_TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 10.0


def heartbeat_payload() -> bytes:
    """Same heartbeat shape widewing/ha-oupes uses; keeps the device's stream alive."""
    return (
        json.dumps(
            {
                "cmd": 3,
                "pv": 0,
                "sn": str(int(time.time() * 1000)),
                "msg": {"attr": [84], "data": {"84": 1}},
            }
        )
        + "\n"
    ).encode()


async def _heartbeat_task(writer: asyncio.StreamWriter) -> None:
    while True:
        try:
            writer.write(heartbeat_payload())
            await writer.drain()
        except Exception:
            return
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


async def telemetry_frames(
    host: str,
    port: int = TCP_PORT,
    *,
    read_timeout: float = READ_TIMEOUT_S,
    connect_timeout: float = CONNECT_TIMEOUT_S,
    on_disconnect: Callable[[], Awaitable[None]] | None = None,
    max_failures: int | None = None,
) -> AsyncIterator[dict]:
    """Async generator over telemetry frames from a device.

    Sends an initial heartbeat right after connect (the device seems to gate
    its `cmd:10` stream on at least one inbound message), then keeps a 10s
    heartbeat task running in parallel. Reconnects with backoff on any failure.

    A device that loses power or WiFi never closes its end, so the socket stays
    open and a bare read waits forever. `read_timeout` turns that silence into a
    drop. `on_disconnect` runs after every drop and every failed attempt, so the
    caller can stop presenting the last frame as live. With `max_failures` set,
    the generator ends after that many consecutive attempts without a frame --
    the cue for a caller that auto-detected `host` to go looking again, since
    DHCP does move the device.
    """
    backoff = 1.0
    failures = 0
    while True:
        hb_task: asyncio.Task | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), connect_timeout
            )
            # Kick the stream open with an immediate heartbeat.
            writer.write(heartbeat_payload())
            await writer.drain()
            hb_task = asyncio.create_task(_heartbeat_task(writer))
            try:
                while True:
                    line = await asyncio.wait_for(reader.readuntil(b"\r\n"), read_timeout)
                    line = line.rstrip(b"\r\n")
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    backoff = 1.0
                    failures = 0
                    yield frame
            finally:
                if hb_task:
                    hb_task.cancel()
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
            # A timeout means we gave up on a silent device; EOF means the device
            # closed on us. Worth telling apart when the stream keeps dropping.
            _LOGGER.info("stream from %s ended: %s", host, type(exc).__name__)
            failures += 1
            if on_disconnect is not None:
                await on_disconnect()
            if max_failures is not None and failures >= max_failures:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def humanize(data_by_id: dict[int, float]) -> dict[str, float]:
    """Translate {attr_id: value} -> {attr_name: value}; unknown ids passthrough."""
    out: dict[str, float] = {}
    for k, v in data_by_id.items():
        name = ATTR_MAPPING.get(k, f"attr_{k}")
        out[name] = v
    return out
