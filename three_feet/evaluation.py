"""Record operator-supplied physical trials and summarize them; never operates hardware.

Run ``python -m three_feet.evaluation --help`` for commands.
"""

from __future__ import annotations

import argparse
import csv
import math
from datetime import datetime, timezone
from pathlib import Path

DISTANCE_FIELDS = ["recorded_at", "setup", "object", "measured_in", "estimated_in", "error_in", "required_in", "notes"]
SHUTOFF_FIELDS = ["recorded_at", "setup", "command_succeeded", "latency_s", "notes"]


def nonnegative(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("Use a finite, nonnegative number")
    return number


def append_trial(path: Path, fields: list[str], row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    if exists:
        with path.open(newline="", encoding="utf-8") as stream:
            if next(csv.reader(stream), []) != fields:
                raise ValueError(f"Unexpected columns in {path}; use the matching trial CSV")
    with path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({"recorded_at": datetime.now(timezone.utc).isoformat(), **row})


def read_trials(path: Path | None, fields: list[str]) -> list[dict]:
    if path is None:
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != fields:
            raise ValueError(f"Unexpected columns in {path}")
        return list(reader)


def report(distances: list[dict], shutoffs: list[dict]) -> str:
    lines = ["# Three Feet — measured trials", "",
             "Operator-recorded observations. These trials do not establish real-world safety or certification.", ""]
    for setup in sorted({r["setup"] for r in [*distances, *shutoffs]}):
        lines += [f"## Setup: {setup}", ""]
        ds = [r for r in distances if r["setup"] == setup]
        ss = [r for r in shutoffs if r["setup"] == setup]
        if ds:
            errors, coverage, missed, false_alarms = [], 0, 0, 0
            for row in ds:
                actual, estimate, error, required = (nonnegative(row[k]) for k in
                    ("measured_in", "estimated_in", "error_in", "required_in"))
                errors.append(abs(estimate - actual))
                coverage += abs(estimate - actual) <= error
                actual_inside, predicted_inside = actual < required, estimate < required
                missed += actual_inside and not predicted_inside
                false_alarms += predicted_inside and not actual_inside
            lines += [f"Distance trials: **{len(ds)}**. Mean absolute error: **{sum(errors) / len(errors):.2f} in**. "
                      f"Maximum absolute error: **{max(errors):.2f} in**.", "",
                      f"Actual distance within estimated ± range: **{coverage}/{len(ds)}**. "
                      f"Missed boundary violations: **{missed}**. False alarms: **{false_alarms}**.", ""]
        if ss:
            succeeded = sum(r["command_succeeded"] == "yes" for r in ss)
            latency = [nonnegative(r["latency_s"]) for r in ss if r["latency_s"]]
            lines += [f"Shutdown attempts: **{len(ss)}**. Command acknowledgements: **{succeeded}**. "
                      f"Failed commands: **{len(ss) - succeeded}**.", ""]
            if latency:
                lines += [f"Recorded command latency ({len(latency)} trials): mean "
                          f"**{sum(latency) / len(latency):.2f} s**, max **{max(latency):.2f} s**.", ""]
    if not distances and not shutoffs:
        lines += ["No trials recorded. Accuracy and shutdown reliability have not been measured.", ""]
    lines += ["## Conditions and limitations", "",
              "Consult the source CSV notes for placements, lighting, occlusions, failures, and exclusions. "
              "Report physical and simulated setups separately. Error estimates are engineering bounds, "
              "not statistically calibrated confidence intervals.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("distance", "shutoff"):
        sub = commands.add_parser(name, help=f"Append one observed {name} trial")
        sub.add_argument("--csv", type=Path, required=True)
        sub.add_argument("--setup", required=True, help="Setup name; distinguish physical trials from simulation")
        sub.add_argument("--notes", default="")
        if name == "distance":
            sub.add_argument("--object", required=True)
            for flag in ("measured-in", "estimated-in", "error-in", "required-in"):
                sub.add_argument(f"--{flag}", type=nonnegative, required=True)
        else:
            sub.add_argument("--command-succeeded", choices=("yes", "no"), required=True)
            sub.add_argument("--latency-s", type=nonnegative)
    summary = commands.add_parser("report", help="Summarize recorded trials by setup")
    summary.add_argument("--distances", type=Path)
    summary.add_argument("--shutoffs", type=Path)
    summary.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "report":
            result = report(read_trials(args.distances, DISTANCE_FIELDS), read_trials(args.shutoffs, SHUTOFF_FIELDS))
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(result, encoding="utf-8")
            else:
                print(result)
        else:
            row = vars(args).copy()
            path = row.pop("csv")
            row.pop("command")
            if args.command == "shutoff":
                if args.latency_s is not None and args.command_succeeded != "yes":
                    parser.error("Command latency requires a successful command acknowledgement")
            append_trial(path, DISTANCE_FIELDS if args.command == "distance" else SHUTOFF_FIELDS, row)
            print(f"Recorded trial in {path}")
    except (ValueError, OSError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
