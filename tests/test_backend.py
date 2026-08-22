"""Tests for the backend's state handling and HTTP surface."""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def state():
    """A fresh State, so tests do not leak into each other via the module global."""
    fresh = main.State()
    original = main.state
    main.state = fresh
    yield fresh
    main.state = original


def frame(**data):
    return {"cmd": 10, "pv": 0, "msg": {"attr": list(data), "data": data}}


class TestStateMerge:
    async def test_merges_successive_partial_frames(self, state):
        # The device never sends a complete picture in one frame, so state has
        # to accumulate rather than replace.
        await state.merge_and_broadcast(frame(**{"3": 50}))
        await state.merge_and_broadcast(frame(**{"23": 415}))
        assert state.latest_by_id == {3: 50, 23: 415}
        assert state.snapshot()["attrs"] == {
            "battery_level": 50,
            "pv_input_power": 415,
        }

    async def test_later_frames_overwrite_earlier_values(self, state):
        await state.merge_and_broadcast(frame(**{"3": 50}))
        await state.merge_and_broadcast(frame(**{"3": 51}))
        assert state.latest_by_id == {3: 51}

    async def test_ignores_frames_with_no_data(self, state):
        await state.merge_and_broadcast({"cmd": 3, "msg": {}})
        await state.merge_and_broadcast({})
        assert state.latest_by_id == {}
        assert state.last_frame_at is None

    async def test_ignores_non_integer_attribute_keys(self, state):
        await state.merge_and_broadcast(frame(**{"3": 50, "bogus": 1}))
        assert state.latest_by_id == {3: 50}

    async def test_history_records_every_frame_even_without_changes(self, state):
        # Repeated identical frames still advance time, and a flat line is real
        # data -- dropping it would leave gaps in the history chart.
        for _ in range(3):
            await state.merge_and_broadcast(frame(**{"3": 50}))
        assert len(state.history) == 3

    async def test_history_is_bounded(self, state):
        small = main.State()
        small.history = type(small.history)(maxlen=2)
        for value in range(5):
            await small.merge_and_broadcast(frame(**{"3": value}))
        assert len(small.history) == 2
        assert small.history[-1]["attrs_by_id"] == {3: 4}


class TestFanout:
    async def test_broadcasts_only_what_changed(self, state):
        sent = []

        class FakeWS:
            async def send_json(self, event):
                sent.append(event)

        state.subscribers.add(FakeWS())
        await state.merge_and_broadcast(frame(**{"3": 50, "4": 0}))
        await state.merge_and_broadcast(frame(**{"3": 50, "4": 120}))

        assert len(sent) == 2, "an unchanged frame should not produce an event"
        assert sent[1]["changed_by_id"] == {4: 120}
        assert sent[1]["changed"] == {"total_output_power": 120}

    async def test_a_dead_subscriber_is_dropped_not_retried(self, state):
        class DeadWS:
            async def send_json(self, event):
                raise ConnectionResetError("client vanished")

        state.subscribers.add(DeadWS())
        await state.merge_and_broadcast(frame(**{"3": 50}))
        assert state.subscribers == set()

    async def test_one_dead_subscriber_does_not_starve_the_others(self, state):
        sent = []

        class DeadWS:
            async def send_json(self, event):
                raise ConnectionResetError

        class LiveWS:
            async def send_json(self, event):
                sent.append(event)

        state.subscribers.add(DeadWS())
        state.subscribers.add(LiveWS())
        await state.merge_and_broadcast(frame(**{"3": 50}))
        assert len(sent) == 1
        assert len(state.subscribers) == 1


class TestEndpoints:
    """Exercised without the lifespan, so no device or discovery is involved."""

    @pytest.fixture
    def client(self, state):
        app = main.app
        original = app.router.lifespan_context

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def noop(app):
            yield

        app.router.lifespan_context = noop
        with TestClient(app) as c:
            yield c
        app.router.lifespan_context = original

    def test_status_before_any_frame_arrives(self, client):
        body = client.get("/status").json()
        assert body["connected"] is False
        assert body["attrs"] == {}
        assert body["units"]["battery_level"] == "%"

    def test_raw_exposes_ids_not_names(self, client, state):
        state.latest_by_id[3] = 50
        body = client.get("/raw").json()
        assert body["attrs_by_id"] == {"3": 50}

    def test_history_limit(self, client, state):
        for value in range(10):
            state.history.append({"t": value, "attrs_by_id": {3: value}})
        body = client.get("/history?limit=3").json()
        assert body["count"] == 3
        assert body["items"][-1]["attrs_by_id"]["3"] == 9

    def test_attrs_documents_the_mapping(self, client):
        body = client.get("/attrs").json()
        assert body["known"]["3"] == "battery_level"
        assert body["models"]["O44A5o"] == "Oupes Mega 1"

    def test_websocket_opens_with_a_snapshot(self, client, state):
        state.latest_by_id[3] = 50
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
        assert first["type"] == "snapshot"
        assert first["snapshot"]["attrs"]["battery_level"] == 50


class TestHostConfiguration:
    def test_an_empty_env_var_means_auto_detect(self, monkeypatch):
        # Regression guard: os.environ.get("MEGA_HOST", default) returns the
        # empty string when compose passes MEGA_HOST= with no value, so the
        # default never applies and the backend tries to connect to "".
        monkeypatch.setenv("MEGA_HOST", "")
        import importlib

        reloaded = importlib.reload(main)
        try:
            assert reloaded.MEGA_HOST is None
        finally:
            monkeypatch.delenv("MEGA_HOST", raising=False)
            importlib.reload(main)

    def test_whitespace_is_stripped(self, monkeypatch):
        monkeypatch.setenv("MEGA_HOST", "  192.0.2.10  ")
        import importlib

        reloaded = importlib.reload(main)
        try:
            assert reloaded.MEGA_HOST == "192.0.2.10"
        finally:
            monkeypatch.delenv("MEGA_HOST", raising=False)
            importlib.reload(main)
