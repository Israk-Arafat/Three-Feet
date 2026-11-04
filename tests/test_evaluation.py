import csv

from three_feet.evaluation import DISTANCE_FIELDS, SHUTOFF_FIELDS, append_trial, read_trials, report


def test_trial_roundtrip_reports_errors_boundary_misses_and_command_failures(tmp_path):
    distances, shutoffs = tmp_path / "distances.csv", tmp_path / "shutoffs.csv"
    # Synthetic fixtures exercise the report; they are not physical validation results.
    for measured, estimated, error in [(35, 38, 1), (40, 35, 3)]:
        append_trial(distances, DISTANCE_FIELDS, dict(setup="synthetic", object="test cloth",
            measured_in=measured, estimated_in=estimated, error_in=error, required_in=36, notes="fixture"))
    for succeeded in ("yes", "no"):
        append_trial(shutoffs, SHUTOFF_FIELDS, dict(setup="synthetic", command_succeeded=succeeded,
            latency_s=2 if succeeded == "yes" else "", notes="fixture"))
    result = report(read_trials(distances, DISTANCE_FIELDS), read_trials(shutoffs, SHUTOFF_FIELDS))
    assert "Mean absolute error: **4.00 in**" in result
    assert "Missed boundary violations: **1**" in result
    assert "False alarms: **1**" in result
    assert "Command acknowledgements: **1**. Failed commands: **1**" in result
    with distances.open(newline="", encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) == 2


def test_empty_report_never_implies_validation():
    assert "No trials recorded" in report([], [])
