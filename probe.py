#!/usr/bin/env python3
"""Targeted probe of a single device, for protocol work.

1) Unicast UDP discovery to retrieve pid/did/mac/sv/hv.
2) Open TCP 5555 and dump the raw stream for ~10s to inspect framing.

    python3 probe.py                    # $MEGA_HOST, else auto-detect
    python3 probe.py 192.168.1.42       # explicit address
"""
import socket
import sys
import time

from _common import protocol, resolve_host

DUMP_SECONDS = 10


def main(argv: list[str]) -> int:
    host = resolve_host(argv)

    print(f"=== UDP discovery -> {host}:{protocol.UDP_PORT} ===")
    results = protocol.discover(host, timeout=2.0)
    if not results:
        print("  (no response)")
    for result in results:
        for key, value in result.as_dict().items():
            print(f"  {key}: {value}")

    print(f"\n=== TCP dump {host}:{protocol.TCP_PORT} ({DUMP_SECONDS}s) ===")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    sock.connect((host, protocol.TCP_PORT))
    print("connected.")
    # Without this the device stays silent; see protocol.telemetry_frames.
    sock.sendall(protocol.heartbeat_payload())

    deadline = time.time() + DUMP_SECONDS
    buf = b""
    while time.time() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            print("(remote closed)")
            break
        buf += chunk
        try:
            print(f"<- {len(chunk)}B text: {chunk.decode()!r}")
        except UnicodeDecodeError:
            print(f"<- {len(chunk)}B hex: {chunk.hex()}")
    sock.close()

    print(f"\nTotal received: {len(buf)} bytes")
    print(f"Buffer hex (first 600): {buf[:600].hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
