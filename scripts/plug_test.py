"""Check the configured power switch: read its status, and optionally cut power and time the confirmation.

    python scripts/plug_test.py              # status for 10 s (PLUG_KIND / PLUG_HOST / PLUG_NAME from .env)
    python scripts/plug_test.py --off        # cut power, then report how long until the plug shows it off

Test with a lamp plugged in, not the heater. Three Feet never turns power on: switch it back on with the
plug's button or its app.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from three_feet.config import get_settings  # noqa: E402
from three_feet.safety.actuator import PlugStatus, make_switch  # noqa: E402


def describe(s: PlugStatus) -> str:
    power = f"{s.power_w:.1f} W" if s.power_w is not None else ("no meter" if not s.metering else "no reading")
    return f"reachable={s.reachable} on={s.on} power={power}"


def is_off(s: PlugStatus, off_w: float) -> bool:
    if s.metering and s.power_w is not None:
        return s.power_w < off_w
    return s.on is False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", help="override PLUG_KIND (dryrun | shelly | cync)")
    ap.add_argument("--off", action="store_true", help="cut power and time the confirmation")
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()

    s = get_settings()
    kind = args.kind or s.plug_kind
    print(f"Connecting to {kind} plug ...")
    t0 = time.perf_counter()
    switch = make_switch(kind, s.plug_host, s.plug_name)
    print(f"Connected in {time.perf_counter() - t0:.1f} s")
    try:
        end = time.time() + 3
        while time.time() < end:
            print("  ", describe(switch.status()))
            time.sleep(1)
        if not args.off:
            return 0
        print("Sending OFF ...")
        sent = time.perf_counter()
        switch.turn_off()
        print(f"  command returned after {time.perf_counter() - sent:.2f} s")
        end = time.time() + args.seconds
        while time.time() < end:
            st = switch.status()
            if is_off(st, s.off_power_w):
                print(f"  confirmed off after {time.perf_counter() - sent:.2f} s ({describe(st)})")
                return 0
            time.sleep(0.25)
        print(f"  NOT confirmed off within {args.seconds:.0f} s ({describe(switch.status())})")
        return 1
    finally:
        close = getattr(switch, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
