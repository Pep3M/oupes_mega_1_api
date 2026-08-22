# Contributing

Thanks for taking a look. This project exists because the Oupes protocol is
undocumented, so **protocol knowledge is worth more here than code**.

## The most useful contributions

### 1. Tell us about your device

If `discover.py` reports your unit as `Unknown (<pid>)`, open an issue with the
full output. That is all it takes to add a model — the `pid` string maps to a
display name in `MODEL_MAPPING` (`backend/app/protocol.py`).

If the telemetry looks wrong for your model (values in the wrong field, missing
sensors), say so too. The attribute IDs were mapped against a Mega 1 and there
is no guarantee they are identical across the range.

### 2. Identify an unknown attribute

`/status` returns anything it does not recognise as `attr_<id>`. `PLAN.md`
lists the ones still unidentified and the experiments that would pin them down.
The method is simple: watch `monitor.py`, change one thing on the unit, and see
which ID moves.

A confirmed identification — "attr 2 is the LED lamp, it goes 0→1 when I press
the light button" — is a genuinely valuable contribution, even with no code
attached.

### 3. Code

Normal pull request flow. Keep in mind:

- **The HA integration is a thin client of the backend**, not a second
  direct-to-device implementation. New data should surface in the backend first
  and be consumed from `/status`.
- **`backend/app/protocol.py` is the single definition of the wire format.**
  The root scripts import it via `_common.py`; please do not reimplement
  discovery or framing anywhere else.
- No hardcoded addresses, and no defaults that only make sense on one person's
  network.

## Running the tests

```
pip install -r backend/requirements.txt -r requirements-dev.txt
python -m pytest
```

The tests are offline — they exercise frame parsing and discovery against
in-process fakes, so no device is needed.

## Working without a device

Most of the codebase can be developed without hardware. `tests/test_protocol.py`
contains real captured frames you can extend; a fake device that replays them
over TCP is a contribution that would help everyone.

## A note on writes

The backend is read-only today. If you are experimenting with write commands
(`PLAN.md` phase 2), be careful: these are large batteries feeding real loads,
and an unrecognised command frame goes straight to firmware nobody here has
audited. Test on something you can afford to have switch off unexpectedly.
