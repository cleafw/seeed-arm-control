# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Service scope

Standalone teleop / record / playback service for **B601-DM master arm → SO102 slave arm**: 30Hz follow + named action library + loop/once playback with smooth transitions back to live master. Single-process FastAPI app; React SPA served from `/`; one WS topic `/ws` carrying state snapshots.

## Dev commands

```bash
# Backend dev (requires real arms)
uv run uvicorn backend.app:app --reload --port 8000

# Frontend dev (proxies /api + /ws to :8000)
cd frontend && npm install && npm run dev      # opens :5173

# Frontend production build (consumed by FastAPI's StaticFiles mount)
cd frontend && npm run build                   # tsc -b && vite build → frontend/dist
```

There is **no test suite** and no linter wired up. `tsc -b` runs as part of `npm run build`. Python uses 3.12 (`.python-version`).

## Architecture

### Process / threading

`backend/app.py` builds a single FastAPI app whose lifespan starts a **daemon control thread** running `Controller.run()` at `update_rate_hz` (default 30Hz). REST handlers acquire `controller.lock` (RLock) briefly to issue commands; the lock guards every mutable controller field. The control thread reads master serial → broadcasts to slave → updates state — never blocks on FastAPI.

WS fan-out goes through `SnapshotHub`: control thread calls `push_from_thread(snap)` which uses `loop.call_soon_threadsafe` to fan the snapshot out to per-client `asyncio.Queue(maxsize=8)` — slow clients drop frames rather than block the producer. Snapshot push happens on the control thread at `ws_push_hz` (default 10Hz), independent of the 30Hz control rate.

If the control thread dies (serial loss, hardware unplug), it calls `os.kill(os.getpid(), SIGTERM)` from `finally:` so the **container's `restart: unless-stopped` policy recovers it**. There is no in-process serial reconnection; restart is the recovery mechanism. SIGTERM handler also calls `controller.request_shutdown()` so `safe_shutdown` (slow zero + motor disable) runs before exit.

### Mode state machine (`backend/controller.py`)

```
                 ┌─ start_record ──────►   record   ──── stop_record ────► follow (saves Action)
                 │
   follow  ◄─────┤                                        ┌─ once: ─► return_to_follow ─►┐
                 │                                        │   (slow 2s blend → master)   │
                 └─ start_playback ─►  transition  ──►  playback ───────────────────────►┤
                                       (0.6s blend                                       │
                                        current → frame[0])    loop: wraps with          │
                                                               loop_blend_time_s         │
                                                                                         ▼
                                                                                       follow
```

The five `ControllerMode` values (`follow / record / transition / playback / return_to_follow`) are the source of truth — `models.py`, the WS snapshot, and the frontend `StageMain` all key off this string. Adding a mode = update `models.py` literal + `Controller` branch in `run()` + frontend `modeStyle.ts` + `StageMain.tsx`.

`return_to_follow` re-reads the master each tick so the slave tracks toward the operator's *current* pose during the slow blend, not a stale snapshot.

### Hardware abstraction

- **Master**: `PiPER_MateAgilex` from `backend/pipermate.py` — CH340 / fashionstar UART; **read-only** (joint angles + gripper opening).
- **Slave**: `SlaveArm` wrapping `MotorControl` from `backend/u2can/DM_CAN.py` — DM 4340/4310 motors; joints 1–6 use `POS_VEL`, joint 7 (gripper) uses `Torque_Pos` with force = 350 (3.5%).
- **Port detection**: VID/PID match (master = `0x1a86:0x7523`, slave = manufacturer "HDSC" or product startswith "CDC"). Multiple candidates → first wins + warn. `MASTER_PORT`/`SLAVE_PORT` env override.

### Storage

`backend/storage.py`: one JSON file per action at `recordings/actions/<uuid>.json` (atomic `.tmp` rename). The action's `name` lives **inside** the JSON, not in the filename, so renames don't move files. Legacy `slot_<N>.json` files at `recordings/` root are auto-imported on first start if `actions/` is empty (idempotent; old files preserved). The list is sorted by `created_at` (ISO 8601 string, lexically sortable).

### Frontend (mode-driven thorough swap)

`frontend/src/`: React + Vite + TS, tailwind utilities for layout + custom CSS tokens in `index.css` (`@layer components`) for the unique components — daisyui was removed; don't reintroduce daisyui-styled components.

The whole UI is keyed off `snapshot.mode`. When mode changes:
- Top **StatusBanner** swaps to a full-width colored bar (per-mode accent), or hides in `follow`.
- Left **JointPanel** bars recolor to the mode accent.
- Center **StageMain** completely swaps content per mode (follow = library + record entry; record/playback/etc = single-purpose centered view with timer + stop button).

Per-mode visual properties (label / accent var / pulse) live in **one** place: `frontend/src/modeStyle.ts`. Add a new mode there + add a branch in `StageMain.tsx`.

`useWs.ts` is the single state source: it tracks `connected`, `snapshot`, and `modeStartTs` (the first `snapshot.ts` seen for the current mode). The frontend computes `elapsed = snapshot.ts - modeStartTs` because the backend snapshot does **not** carry per-mode elapsed/duration — keep this contract if extending. For playback, action `duration_s` comes from the actions list (`api.listActions()`).

REST contract is small and stable; see `backend/app.py`. PlayMode is the literal `"loop" | "once"` string (not a boolean).

## Key environment variables

All optional. Full list in `backend/config.py`. Most-used:

| Var | Default | Purpose |
|---|---|---|
| `MASTER_PORT` / `SLAVE_PORT` | auto-detect | Override VID/PID detection |
| `REBOT_UPDATE_HZ` | `30` | Control-loop rate |
| `REBOT_WS_PUSH_HZ` | `10` | WS broadcast rate (independent of control rate) |
| `REBOT_TRANSITION_TIME` | `0.6` | Blend from current → frame[0] before playback |
| `REBOT_RETURN_TIME` | `2.0` | "Once" playback → live master slow blend |
| `REBOT_LOOP_BLEND_TIME` | `0.30` | End-of-loop wraparound blend |
| `REBOT_RECORDINGS_DIR` | `./recordings` | Where action JSONs live |

## Gotchas

- **Container char-major rules**: `deploy/docker-compose.yml` pins `device_cgroup_rules` to char major **188 (CH340)** and **166 (CDC ACM)**. If the host kernel uses different majors, `Permission denied: /dev/ttyXXX` even though the bind-mount is fine. Check with `ls -l /dev/ttyUSB0 /dev/ttyACM0` before deploying to a new box.
- **`/dev` bind-mount**: docker-compose binds `/dev:/dev` so USB hot-replug is visible to the container without a restart. Don't replace this with named devices unless you also accept a container restart on every replug.
- **Frontend dist served by FastAPI**: the static mount is added **last** in `build_app()` so `/api/*` and `/ws` win. Don't mount additional paths under `/`.
- **Recording filter**: `_changed_enough` + `_filter` smooth & deduplicate frames. A motionless arm + small `min_joint_change_rad` produces a 2-frame action (the initial pose + end-hold pad) — not a bug.
