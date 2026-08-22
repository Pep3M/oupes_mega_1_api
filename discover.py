#!/usr/bin/env python3
"""Find Oupes power stations on the local network.

Broadcasts a discovery packet on UDP :6095 and, if nothing answers, falls back
to a unicast sweep of the local /24. The fallback matters: plenty of consumer
APs drop broadcast towards WiFi clients, so a wired host never sees a reply
even though the device answers unicast fine.

    python3 discover.py                 # auto-detect the subnet(s) to search
    python3 discover.py 192.168.1       # sweep a specific /24 prefix
"""
import sys

from _common import protocol


def main(argv: list[str]) -> int:
    subnets = argv[1:] or None
    if subnets is None:
        detected = protocol.local_subnets()
        if detected:
            print(f"Local subnet(s): {', '.join(s + '.0/24' for s in detected)}")
        else:
            print("Could not determine the local subnet; broadcast only.")
            print("Pass a prefix to sweep explicitly, e.g. 'discover.py 192.168.1'.")
    else:
        print(f"Sweeping: {', '.join(s + '.0/24' for s in subnets)}")

    print(f"Broadcasting discovery on UDP :{protocol.UDP_PORT} ...")
    found = protocol.discover(timeout=3.0)
    if not found:
        for subnet in subnets or protocol.local_subnets():
            print(f"No reply yet — sweeping {subnet}.1-254 unicast ...")
            found = protocol.sweep(subnet)
            if found:
                break

    for result in found:
        print(f"\n{result.model} at {result.host}")
        for key, value in result.as_dict().items():
            if key not in ("host", "model"):
                print(f"  {key}: {value}")

    print(f"\n{len(found)} device(s) discovered.")
    if found:
        hosts = ", ".join(r.host for r in found)
        print(f"Set MEGA_HOST in your .env to pin one of: {hosts}")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
