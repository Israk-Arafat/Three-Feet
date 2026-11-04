# Physical validation and demo

Use an unpowered heater or non-heating stand-in for close-object staging. These tools only record observations; they do not operate the plug or run models. Keep physical and simulated trials in separately named setups.

## Record distances

Fix the phone after calibration. Use a tape measure from the heater footprint to each object's nearest floor-contact point. Record several placements on every visible side, especially immediately inside and outside the reviewed clearance boundary. Include different objects, lighting, and occlusions, and keep failed trials.

For each observation, supply the actual tape reading, displayed estimate and ± error, and active clearance. Replace these illustrative numbers with your own readings:

```powershell
python -m three_feet_app.evaluation distance --csv runs/validation/distances.csv --setup physical-fixed-phone --object blanket --measured-in 36 --estimated-in 35 --error-in 2 --required-in 36 --notes "Front; room light; clear floor contact"
```

## Record shutdown attempts

Record whether the OFF command returned successfully or raised an error. Optional latency measures command acknowledgement time. The app records command outcomes and displays the plug's reported state separately.

```powershell
python -m three_feet_app.evaluation shutoff --csv runs/validation/shutoffs.csv --setup physical-fixed-phone --command-succeeded yes --latency-s 1.2 --notes "OFF command returned successfully"
```

Exercise camera loss, API failure, budget pause, and command failure. Demonstrate fault injection in the simulator separately from the physical setup.

## Generate results

```powershell
python -m three_feet_app.evaluation report --distances runs/validation/distances.csv --shutoffs runs/validation/shutoffs.csv --output RESULTS.md
```

The report groups results by setup and includes trial counts, mean/max absolute error, coverage of the engineering error range, boundary misses and false alarms, command failures, and command latency. Keep CSV notes with the report so the conditions are reviewable. A report without trials explicitly says validation is missing.

## Three-minute recording

1. Explain the problem and show the fixed camera, heater footprint, and plug (20 seconds).
2. Show calibration, the reviewed clearance source, and how the three Meta models contribute (30 seconds).
3. Show object detection, distance ± uncertainty, countdown, OFF request, and plug status (70 seconds).
4. Show one failure case and the resulting unknown state or command error (30 seconds).
5. Show measured results, limitations, and the code built during the event (30 seconds).

Avoid claiming physical accuracy from simulator tests. Add the finished video link and actual results to the README before submission.
