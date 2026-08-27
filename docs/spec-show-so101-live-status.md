---
title: 'Show connected SO-ARM101 arms as live hardware'
type: 'bugfix'
created: '2026-08-26'
status: 'in-review'
baseline_commit: 'd647911f2f2b72196647810ab1f5811450ec1d97'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Both SO-ARM101 buses are healthy and return live positions on COM11 and COM13 for IDs 1–6, but the page still reports both arms as simulated. The current backend is either absent or starts the legacy Violin/B601 driver, which cannot represent Feetech SO-ARM101 hardware.

**Approach:** Make the local startup select the SO-ARM101 hardware path when two CH343 buses are present, and add a read-safe Feetech adapter so the existing status endpoint/UI report each arm as connected only after position data is actually read.

## Boundaries & Constraints

**Always:** Preserve manual profile selection; map the two detected CH343 ports deterministically while allowing explicit port overrides; treat a serial port as connected only after a successful Feetech position read; keep torque disabled during startup/status detection; use the observed STS3215 IDs 1–6 at 1 Mbps; when no compatible hardware is attached, keep the service online and show the physical connection as unavailable.

**Ask First:** Any change that sends a non-zero motion command, enables torque beyond a read-safe check, alters motor IDs/baud rate, or changes calibration values.

**Never:** Start the legacy B601/DM-CAN driver against the SO-ARM101 CH343 ports; infer a hardware connection solely from VID/PID; overwrite unrelated existing working-tree changes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Both arms online | COM11 and COM13 each answer ID 1–6 position reads | API/UI show live leader and follower with their ports | No motion or torque command is issued |
| One bus unavailable | Only one port answers ID 1–6 | That role shows live; the other shows reconnecting/missing | Keep service and UI available; retry safely |
| No SO-ARM hardware | No compatible live Feetech bus | Service remains available and retries | UI reports the physical connection as unavailable |
| Wrong legacy selection | SO-ARM101 profiles selected | Backend must not instantiate B601 or Violin drivers | Report configuration error rather than talking a foreign protocol |

</frozen-after-approval>

## Code Map

- `start-local.bat` -- starts the physical SO-ARM101 backend environment.
- `backend/config.py` -- environment-backed profile and serial-port configuration.
- `backend/controller.py` -- hardware lifecycle, status snapshot, profile application, and reconnect loop.
- `backend/profiles/builtin.py` -- SO-ARM101 profile metadata currently marked as unwired.
- `backend/app.py` -- serves the status and profile endpoints consumed by the frontend.
- `frontend/src/App.tsx` -- displays arm connectivity state.

## Tasks & Acceptance

**Execution:**
- [x] `backend/` -- added an SO-ARM101 Feetech read-safe driver adapter; it exposes live joint feedback and rejects all motion/torque calls.
- [x] `backend/controller.py` and `backend/config.py` -- select the adapter for `so101_leader`/`so101_follower`, bind two CH343 buses, and preserve safe retry/status behavior.
- [x] `start-local.bat` -- starts the physical SO-ARM101 profile pair and keeps retrying when an arm is unavailable.
- [x] `frontend/` -- existing status rendering was verified against real backend snapshots; no UI code change was needed.
- [x] `backend/` focused checks -- verified both live buses return all six positions with no motor writes; frontend production build succeeds. `pytest` is not installed in this environment.

**Acceptance Criteria:**
- Given COM11 and COM13 each return Feetech positions for IDs 1–6, when the application starts, then the API state reports both arms `ok` with their real ports and the UI no longer labels either arm as simulated.
- Given either bus stops answering, when the reconnect cycle runs, then only that arm becomes unavailable and no position or torque command is sent.
- Given no hardware is attached, when local startup runs, then the UI reports both physical arms as unavailable and continues retrying.
- Given SO-ARM101 is selected, when the backend initializes, then no B601/DM-CAN or Violin protocol is transmitted to either SO-ARM101 bus.

## Spec Change Log

## Design Notes

The verified hardware is two CH343 buses with identical USB IDs, so USB enumeration cannot determine leader versus follower. The process must assign a stable default order (COM11 leader, COM13 follower in the current session) and expose overrides, while liveness is based on an ID 1–6 feedback read.

## Verification

**Commands:**
- `uv run python -m pytest` -- expected: existing automated tests pass where available.
- `cd frontend; npm run build` -- expected: production build completes.
- Read-only Feetech probe on both selected ports -- expected: IDs 1–6 return position data, with torque staying disabled.
