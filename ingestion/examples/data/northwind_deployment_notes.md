# Northwind Reno pilot — deployment notes

Notes from the PickPoint One pilot at the Northwind Logistics fulfillment
center in Reno, Nevada. Author: Dana Whitfield, Operations Manager,
Northwind Logistics. Last updated June 20, 2025.

## Site and scope

Four PickPoint One arms are installed in the small-parts zone, mounted on
the existing racking as promised — no floor work was needed. The zone
handles roughly nine thousand picks a day of the building's sixty
thousand, which makes it a fair stress test without risking the whole
operation on week one.

## Commissioning

Commissioning ran the first week of June with a Meridian Robotics engineer
on site. Our maintenance crew completed the service training in three days;
by Thursday they were swapping a gripper assembly without supervision. The
one-week commissioning claim held up.

Arm 3 shipped with a miscalibrated wrist encoder and threw repeated
calibration faults on day two. The fix was a firmware recalibration pushed
remotely the same afternoon — logged here because the symptom (intermittent
drops on small items) looks like a mechanical problem and is not.

## FleetView

My shift leads have had FleetView dashboards from day one, and it has
changed how the zone is run. Fault trends flagged the Arm 3 encoder issue
before the floor noticed it, and per-arm utilization is how we now decide
which lanes feed the robotic zone. If we go to a full-building rollout,
FleetView is the reason I can sell it to our CFO.

## Open items

Pick accuracy in the pilot zone is at 99.1 percent against a 99.5 target;
Meridian believes the gap closes with the next gripper firmware. Peak-hour
throughput is already eight percent above the manual baseline. Decision
point for the full-building rollout is end of Q3.
