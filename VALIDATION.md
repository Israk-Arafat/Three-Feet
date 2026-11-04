# Physical validation and demo

Use an unpowered heater or non-heating stand-in for close-object staging. These tools only record observations; they do not operate the plug or run models. Keep physical and simulated trials in separately named setups.

## Record distances

Fix the phone after calibration. Use a tape measure from the heater footprint to each object's nearest floor-contact point. Record several placements on every visible side, especially immediately inside and outside the reviewed clearance boundary. Include different objects, lighting, and occlusions, and keep failed trials.

For each observation, supply the actual tape reading, displayed estimate and ± error, and active clearance. Replace these illustrative numbers with your own readings:

```powershell
python -m three_feet.evaluation distance --csv runs/validation/distances.csv --setup physical-fixed-phone --object blanket --measured-in 36 --estimated-in 35 --error-in 2 --required-in 36 --notes "Front; room light; clear floor contact"
```

## Record shutdown attempts

Log command failure separately from physical evidence. `--plug-off yes` means a plug report, not a power-meter measurement. Count independent confirmation only when the plug reports off **and** the camera sees the calibrated power light go dark. Use `unknown` for missing or unreadable evidence. Supply latency only for independently confirmed shutdowns.

```powershell
python -m three_feet.evaluation shutoff --csv runs/validation/shutoffs.csv --setup physical-fixed-phone --command-succeeded yes --plug-off yes --indicator-off unknown --notes "Light probe not configured; not independently confirmed"
```

Exercise camera loss, API failure, budget pause, and failed verification. Demonstrate fault injection in the simulator separately from the physical setup.

## Generate results

```powershell
python -m three_feet.evaluation report --distances runs/validation/distances.csv --shutoffs runs/validation/shutoffs.csv --output RESULTS.md
```

The report groups results by setup and includes trial counts, mean/max absolute error, coverage of the engineering error range, boundary misses and conservative false alarms, command failures, independent confirmations, and recorded confirmation latency. Keep CSV notes with the report so the conditions are reviewable. A report without trials explicitly says validation is missing.

## Three-minute recording

1. Explain the problem and show the fixed camera, heater footprint, and plug (20 seconds).
2. Show calibration, the reviewed clearance source, and how the three Meta models contribute (30 seconds).
3. Show object detection, distance ± uncertainty, countdown, OFF request, and both verification checks (70 seconds).
4. Show one failure case and the resulting unknown or unconfirmed state (30 seconds).
5. Show measured results, limitations, and the code built during the event (30 seconds).

Avoid claiming physical accuracy from simulator tests or independent shutdown confirmation from a relay report alone. Add the finished video link and actual results to the README before submission.
