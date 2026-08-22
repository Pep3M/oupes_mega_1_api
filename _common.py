"""Shared bootstrap for the standalone scripts at the repo root.

They reuse the backend's protocol module rather than reimplementing discovery,
so there is exactly one definition of the wire format to keep correct.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app import protocol  # noqa: E402


def resolve_host(argv: list[str]) -> str:
    """Device address from argv[1], else $MEGA_HOST, else LAN auto-detection."""
    if len(argv) > 1:
        return argv[1]

    env_host = (os.environ.get("MEGA_HOST") or "").strip()
    if env_host:
        return env_host

    print("No host given and MEGA_HOST unset — searching the LAN ...", file=sys.stderr)
    found = protocol.discover_all(timeout=3.0)
    if not found:
        raise SystemExit(
            "No Oupes device found. Pass the address explicitly "
            f"(`{Path(argv[0]).name} 192.168.1.42`) or set MEGA_HOST."
        )
    if len(found) > 1:
        hosts = ", ".join(r.host for r in found)
        print(f"Multiple devices found ({hosts}); using {found[0].host}.", file=sys.stderr)
    print(f"Using {found[0].model} at {found[0].host}.", file=sys.stderr)
    return found[0].host
