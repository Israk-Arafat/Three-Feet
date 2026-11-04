"""Check the configured power switch and optionally send a manual on/off command.

    python scripts/plug_test.py              # status for 3 s (PLUG_KIND / PLUG_NAME from .env)
    python scripts/plug_test.py --off        # cut power, then report how long until the plug shows it off
    python scripts/plug_test.py --on         # turn on, then report how long until the plug shows it on

Use --on only with a lamp. This manual test utility can energize the connected device; the live safety
engine restores power only after an explicit reset with a verified clear zone.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from three_feet.config import get_settings  # noqa: E402
from three_feet.safety.actuator import DryRunPlug, PlugStatus, make_switch  # noqa: E402


def describe(s: PlugStatus) -> str:
    power = f"{s.power_w:.1f} W" if s.power_w is not None else ("no meter" if not s.metering else "no reading")
    return f"reachable={s.reachable} on={s.on} power={power}"


def is_off(s: PlugStatus, off_w: float) -> bool:
    if s.metering and s.power_w is not None:
        return s.power_w < off_w
    return s.on is False


def is_on(s: PlugStatus, on_w: float) -> bool:
    if s.on is True:
        return True
    if s.metering and s.power_w is not None:
        return s.power_w >= on_w
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", help="override PLUG_KIND (dryrun | cync)")
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--off", action="store_true", help="cut power and time the confirmation")
    action.add_argument("--on", action="store_true", help="turn on for manual plug testing; use a lamp only")
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()

    s = get_settings()
    kind = args.kind or s.plug_kind
    print(f"Connecting to {kind} plug ...")
    t0 = time.perf_counter()
    switch = make_switch(kind, s.plug_name)
    print(f"Connected in {time.perf_counter() - t0:.1f} s")
    try:
        end = time.time() + 3
        while time.time() < end:
            print("  ", describe(switch.status()))
            time.sleep(1)
        if not args.off and not args.on:
            return 0
        if args.off:
            print("Sending OFF ...")
        else:
            print("Sending ON (manual test; use a lamp only) ...")
        sent = time.perf_counter()
        if args.off:
            switch.turn_off()
        elif isinstance(switch, DryRunPlug):
            switch.simulate_power_on()
        else:
            turn_on = getattr(switch, "turn_on_for_test", None)
            if not callable(turn_on):
                raise RuntimeError(f"{kind} plug does not support manual ON testing")
            turn_on()
        print(f"  command returned after {time.perf_counter() - sent:.2f} s")
        end = time.time() + args.seconds
        while time.time() < end:
            st = switch.status()
            if args.off and is_off(st, s.off_power_w):
                print(f"  confirmed off after {time.perf_counter() - sent:.2f} s ({describe(st)})")
                return 0
            if args.on and is_on(st, s.off_power_w):
                print(f"  confirmed on after {time.perf_counter() - sent:.2f} s ({describe(st)})")
                return 0
            time.sleep(0.25)
        state = "off" if args.off else "on"
        print(f"  NOT confirmed {state} within {args.seconds:.0f} s ({describe(switch.status())})")
        return 1
    finally:
        close = getattr(switch, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
