#!/usr/bin/env python3
"""Oupes live monitor — connects to TCP 5555, prints decoded telemetry.

Frames are JSON objects separated by \\r\\n. Each frame has shape:
    {"cmd": <int>, "pv": 0, "sn": "<ts_ms>", "msg": {"attr": [...], "data": {...}}}

    python3 monitor.py                  # $MEGA_HOST, else auto-detect
    python3 monitor.py 192.168.1.42     # explicit address
"""
import json
import socket
import sys
import time

from _common import protocol, resolve_host

TCP_PORT = protocol.TCP_PORT

ATTRS = {
    1:   ("switch_status",       ""),
    3:   ("battery_level",       "%"),
    4:   ("total_output_power",  "W"),
    5:   ("ac_output_power",     "W"),
    6:   ("dc12_output_power",   "W"),
    7:   ("pd_output_power",     "W"),
    8:   ("usb_output_power",    "W"),
    21:  ("total_input_power",   "W"),
    22:  ("ac_input_power",      "W"),
    23:  ("pv_input_power",      "W"),
    30:  ("remaining_time",      "min"),
    32:  ("battery_temp_f_x10",  "0.1°F"),
    105: ("fast_charging",       ""),
}

state = {}

def fmt_temp_c(v_f_x10: int) -> str:
    f = v_f_x10 / 10
    c = (f - 32) * 5 / 9
    return f"{f:.1f}°F ({c:.1f}°C)"

def render(host):
    sys.stdout.write("\x1b[H\x1b[2J")  # clear screen
    print(f"=== Oupes @ {host}  {time.strftime('%H:%M:%S')} ===\n")
    keys_in_order = [3, 4, 5, 6, 7, 8, 21, 22, 23, 30, 32, 1, 105]
    for k in keys_in_order:
        if k not in state:
            continue
        name, unit = ATTRS.get(k, (f"attr_{k}", ""))
        v = state[k]
        if k == 32:
            print(f"  {name:<22} {fmt_temp_c(v)}")
        else:
            print(f"  {name:<22} {v} {unit}")
    unknown = {k: v for k, v in state.items() if k not in ATTRS}
    if unknown:
        print("\n  -- undocumented attrs --")
        for k, v in sorted(unknown.items()):
            print(f"  attr_{k:<18} {v}")

def main():
    host = resolve_host(sys.argv)
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(10)
                s.connect((host, TCP_PORT))
                # The device gates its telemetry push on at least one inbound
                # message, so knock first or we just sit here reading nothing.
                s.sendall(protocol.heartbeat_payload())
                last_hb = time.time()
                buf = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    if time.time() - last_hb >= protocol.HEARTBEAT_INTERVAL_S:
                        s.sendall(protocol.heartbeat_payload())
                        last_hb = time.time()
                    buf += chunk
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        data = (obj.get("msg") or {}).get("data") or {}
                        for k_str, v in data.items():
                            state[int(k_str)] = v
                        render(host)
        except (ConnectionError, socket.timeout, OSError) as e:
            print(f"\n[reconnecting after error: {e}]")
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nbye.")
            return

if __name__ == "__main__":
    main()
