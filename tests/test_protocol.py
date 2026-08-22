"""Tests for the wire format, using frames captured from a real Mega 1."""
from __future__ import annotations

import asyncio
import contextlib
import json

from app import protocol

# Captured verbatim from a Mega 1 (sv 1.2.0). The device splits its telemetry
# across two frames per second rather than sending one combined update.
FRAME_A = (
    b'{"cmd":10,"pv":0,"sn":"1787402864000","msg":{"attr":[1,2,3,4,30,32,51,105],'
    b'"data":{"1":0,"2":0,"3":7,"4":0,"30":5940,"32":1051,"51":0,"105":0}}}'
)
FRAME_B = (
    b'{"cmd":10,"pv":0,"sn":"1787402864002","msg":{"attr":[5,6,7,8,9,21,22,23],'
    b'"data":{"5":0,"6":0,"7":0,"8":0,"9":0,"21":27,"22":0,"23":27}}}'
)
# The reply to our heartbeat, which arrives before any telemetry.
FRAME_ACK = (
    b'{"cmd":3,"pv":0,"sn":"1787402855628","res":0,'
    b'"msg":{"attr":[84],"data":{"84":1}}}'
)

DISCOVERY_RESPONSE = {
    "cmd": 0,
    "pv": 0,
    "msg": {
        "ip": "192.0.2.50",
        "did": "0011223344556677889a",
        "dtp": "02",
        "pid": "O44A5o",
        "mac": "001122334455",
        "rssi": "-43",
        "sv": "1.2.0",
        "hv": "1.2.16",
    },
}


class TestHumanize:
    def test_maps_known_ids_to_names(self):
        assert protocol.humanize({3: 77, 23: 415}) == {
            "battery_level": 77,
            "pv_input_power": 415,
        }

    def test_unknown_ids_pass_through_with_a_stable_name(self):
        # Unknown attrs must stay visible: identifying them is the whole point
        # of PLAN.md, and silently dropping them would hide the evidence.
        assert protocol.humanize({9: 1}) == {"attr_9": 1}

    def test_empty_input(self):
        assert protocol.humanize({}) == {}

    def test_attr_30_stays_unmapped(self):
        # Attribute 30 was once "remaining_time" in minutes. Measured at 1 Hz it
        # oscillates second to second and correlates 0.90 with PV input power,
        # so it is not a duration; PLAN.md tracks the PV-voltage hypothesis.
        # Locked down here because a name like "remaining_time" is exactly the
        # kind of plausible guess that gets re-added, and Home Assistant would
        # then record it as a duration forever.
        assert protocol.humanize({30: 5940}) == {"attr_30": 5940}
        assert "remaining_time" not in protocol.ATTR_UNITS


class TestDiscoveryResult:
    def test_parses_a_real_response(self):
        r = protocol.DiscoveryResult.from_response(DISCOVERY_RESPONSE, "192.0.2.50")
        assert r.host == "192.0.2.50"
        assert r.pid == "O44A5o"
        assert r.model == "Oupes Mega 1"
        assert r.sv == "1.2.0"
        assert r.rssi == -43  # arrives as a string, must end up an int

    def test_unknown_model_is_labelled_not_dropped(self):
        raw = {"msg": dict(DISCOVERY_RESPONSE["msg"], pid="zzzzzz")}
        r = protocol.DiscoveryResult.from_response(raw, "192.0.2.50")
        assert r.model == "Unknown (zzzzzz)"

    def test_falls_back_to_source_ip_when_device_omits_its_own(self):
        raw = {"msg": {k: v for k, v in DISCOVERY_RESPONSE["msg"].items() if k != "ip"}}
        r = protocol.DiscoveryResult.from_response(raw, "192.0.2.99")
        assert r.host == "192.0.2.99"

    def test_survives_an_empty_message(self):
        r = protocol.DiscoveryResult.from_response({}, "192.0.2.1")
        assert r.host == "192.0.2.1"
        assert r.rssi == 0

    def test_as_dict_is_json_serialisable(self):
        r = protocol.DiscoveryResult.from_response(DISCOVERY_RESPONSE, "192.0.2.50")
        assert json.loads(json.dumps(r.as_dict()))["pid"] == "O44A5o"


class TestHeartbeat:
    def test_shape_matches_what_the_device_expects(self):
        payload = protocol.heartbeat_payload()
        assert payload.endswith(b"\n")
        obj = json.loads(payload)
        assert obj["cmd"] == 3
        assert obj["msg"] == {"attr": [84], "data": {"84": 1}}
        assert obj["sn"].isdigit()


class TestLocalSubnets:
    def test_skips_networks_where_we_hold_the_gateway_address(self, monkeypatch):
        # .1 means we host the network (a Docker bridge), so no device is on it.
        monkeypatch.setattr(
            protocol,
            "local_ipv4s",
            lambda: ["192.168.1.50", "172.17.0.1", "10.0.0.1", "10.7.0.6"],
        )
        assert protocol.local_subnets() == ["192.168.1", "10.7.0"]

    def test_deduplicates_two_addresses_on_one_subnet(self, monkeypatch):
        monkeypatch.setattr(
            protocol, "local_ipv4s", lambda: ["192.168.1.50", "192.168.1.51"]
        )
        assert protocol.local_subnets() == ["192.168.1"]

    def test_no_usable_interfaces(self, monkeypatch):
        monkeypatch.setattr(protocol, "local_ipv4s", lambda: [])
        assert protocol.local_subnets() == []


class TestLocalIpv4s:
    def test_excludes_loopback_and_public_addresses(self, monkeypatch):
        monkeypatch.setattr(protocol, "primary_ipv4", lambda: "192.168.1.50")
        monkeypatch.setattr(
            protocol.socket,
            "getaddrinfo",
            lambda *a, **k: [
                (None, None, None, None, ("127.0.0.1", 0)),
                (None, None, None, None, ("8.8.8.8", 0)),
                (None, None, None, None, ("169.254.1.1", 0)),
                (None, None, None, None, ("10.1.2.3", 0)),
            ],
        )
        assert protocol.local_ipv4s() == ["192.168.1.50", "10.1.2.3"]


class TestBroadcastTargets:
    def test_includes_directed_broadcast_per_subnet(self):
        targets = protocol._broadcast_targets(["192.168.1", "10.7.0"])
        assert "192.168.1.255" in targets
        assert "10.7.0.255" in targets
        assert "255.255.255.255" in targets

    def test_no_duplicates(self):
        targets = protocol._broadcast_targets(["192.168.1", "192.168.1"])
        assert len(targets) == len(set(targets))


async def _close(writer: asyncio.StreamWriter) -> None:
    """Close a server-side writer. Leaving it to the GC makes asyncio complain
    from __del__, which surfaces as an unraisable-exception test failure."""
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


async def _serve_frames(frames: list[bytes]):
    """A fake device: waits for an inbound message, then emits `frames`."""
    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        received.append(await reader.read(200))
        for frame in frames:
            writer.write(frame + b"\r\n")
        await writer.drain()
        await _close(writer)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], received


class TestTelemetryFrames:
    async def test_yields_parsed_frames(self):
        server, port, _ = await _serve_frames([FRAME_ACK, FRAME_A, FRAME_B])
        async with server:
            frames = []
            gen = protocol.telemetry_frames("127.0.0.1", port)
            async for frame in gen:
                frames.append(frame)
                if len(frames) == 3:
                    break
            await gen.aclose()

        assert [f["cmd"] for f in frames] == [3, 10, 10]
        assert frames[1]["msg"]["data"]["3"] == 7
        assert frames[2]["msg"]["data"]["21"] == 27

    async def test_knocks_first_so_the_device_starts_talking(self):
        # The device stays silent until it receives something; regression guard
        # for the bug where monitor.py connected and then waited forever.
        server, port, received = await _serve_frames([FRAME_A])
        async with server:
            gen = protocol.telemetry_frames("127.0.0.1", port)
            async for _ in gen:
                break
            await gen.aclose()

        assert received, "nothing was sent to the device"
        assert json.loads(received[0].split(b"\n")[0])["cmd"] == 3

    async def test_skips_malformed_frames_without_dying(self):
        server, port, _ = await _serve_frames([b"not json at all", FRAME_A])
        async with server:
            gen = protocol.telemetry_frames("127.0.0.1", port)
            async for frame in gen:
                break
            await gen.aclose()

        assert frame["cmd"] == 10

    async def test_reconnects_after_the_device_drops_the_connection(self):
        attempts = 0

        async def handle(reader, writer):
            nonlocal attempts
            attempts += 1
            await reader.read(200)
            if attempts == 1:
                await _close(writer)  # simulate a drop before any telemetry
                return
            writer.write(FRAME_A + b"\r\n")
            await writer.drain()
            await _close(writer)

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            gen = protocol.telemetry_frames("127.0.0.1", port)
            frame = await asyncio.wait_for(gen.__anext__(), timeout=10)
            await gen.aclose()

        assert attempts == 2
        assert frame["cmd"] == 10
