"""Oupes LAN protocol primitives — UDP discovery + TCP telemetry frames.

Attribute IDs and product mappings derived from widewing/ha-oupes
(custom_components/oupes/const.py) plus first-hand inspection of a
Mega 1 (pid 'O44A5o', sv 1.2.0, hv 1.2.16).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from typing import AsyncIterator, Iterable

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
    30:  "remaining_time",
    32:  "battery_temp_f_x10",
    105: "fast_charging",
}

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
    "remaining_time": "min",
    "battery_temp_f_x10": "0.1°F",
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


async def telemetry_frames(host: str, port: int = TCP_PORT) -> AsyncIterator[dict]:
    """Async generator over telemetry frames from a device.

    Sends an initial heartbeat right after connect (the device seems to gate
    its `cmd:10` stream on at least one inbound message), then keeps a 10s
    heartbeat task running in parallel. Reconnects with backoff on any failure.
    """
    backoff = 1.0
    while True:
        hb_task: asyncio.Task | None = None
        try:
            reader, writer = await asyncio.open_connection(host, port)
            backoff = 1.0
            # Kick the stream open with an immediate heartbeat.
            writer.write(heartbeat_payload())
            await writer.drain()
            hb_task = asyncio.create_task(_heartbeat_task(writer))
            try:
                while True:
                    line = await reader.readuntil(b"\r\n")
                    line = line.rstrip(b"\r\n")
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
            finally:
                if hb_task:
                    hb_task.cancel()
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
        except (OSError, asyncio.IncompleteReadError, ConnectionError):
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def humanize(data_by_id: dict[int, float]) -> dict[str, float]:
    """Translate {attr_id: value} -> {attr_name: value}; unknown ids passthrough."""
    out: dict[str, float] = {}
    for k, v in data_by_id.items():
        name = ATTR_MAPPING.get(k, f"attr_{k}")
        out[name] = v
    return out
