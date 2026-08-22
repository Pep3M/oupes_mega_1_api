#!/usr/bin/env python3
"""Verbose BLE scan: prints every advertisement with full payload."""
import asyncio
from bleak import BleakScanner

seen = {}

def cb(device, adv):
    key = device.address
    fingerprint = (device.name, tuple(sorted(adv.service_uuids)),
                   tuple(sorted(adv.manufacturer_data.items())),
                   tuple(sorted(adv.service_data.items())))
    if seen.get(key) == fingerprint:
        return
    seen[key] = fingerprint
    print(f"\n{device.address}  RSSI={adv.rssi}  name={device.name!r}")
    if adv.local_name:
        print(f"  local_name: {adv.local_name!r}")
    for uuid in adv.service_uuids:
        print(f"  service_uuid: {uuid}")
    for cid, data in adv.manufacturer_data.items():
        print(f"  mfg[{cid:#06x}]: {data.hex()}  ({len(data)} bytes)")
    for uuid, data in adv.service_data.items():
        print(f"  svc_data[{uuid}]: {data.hex()}")
    if adv.tx_power is not None:
        print(f"  tx_power: {adv.tx_power}")

async def main():
    print("Scanning 60s — open the Oupes app and tap 'add/find device' NOW...")
    async with BleakScanner(cb):
        await asyncio.sleep(60)
    print(f"\nDone. {len(seen)} unique devices seen.")

asyncio.run(main())
