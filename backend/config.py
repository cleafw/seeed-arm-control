"""Environment-variable backed configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Config:
    # --- hardware ---
    master_port: str | None
    slave_port: str | None
    baudrate: int               # slave DM_CAN baud
    master_baudrate: int        # leader Fashionstar UART (StarAi Violin = 1000000)
    update_rate_hz: int
    gripper_exist: bool
    mock: bool                  # synthesize joint data, no real serial I/O

    # --- recordings ---
    recordings_dir: Path

    # --- timing ---
    return_time_s: float        # how long the slow return-to-master takes
    transition_time_s: float    # blend from current → action[0]
    loop_blend_time_s: float    # blend from action[-1] → action[0] when looping
    end_hold_time_s: float      # hold-still pad appended at end of recording

    # --- recording filter ---
    record_filter_alpha: float
    min_record_interval_s: float
    min_joint_change_rad: float

    # --- safety (follow/record only) ---
    safety_default_enabled: bool
    max_joint_vel_rad_s: float       # per-joint slew limit; clipped each tick
    spike_threshold_rad: float       # per-tick |Δ| above this → auto-pause
    recover_blend_time_s: float      # slow blend to home before re-handshake

    # --- web ---
    ws_push_hz: int

    @classmethod
    def from_env(cls) -> "Config":
        repo_root = Path(__file__).resolve().parent.parent
        recordings = Path(os.environ.get("REBOT_RECORDINGS_DIR", repo_root / "recordings"))
        return cls(
            master_port=os.environ.get("MASTER_PORT") or None,
            slave_port=os.environ.get("SLAVE_PORT") or None,
            baudrate=_env_int("REBOT_BAUDRATE", 921600),
            # StarAi Violin / StarArm 102 official baud is 1000000 (not 115200).
            master_baudrate=_env_int("MASTER_BAUD", 1000000),
            update_rate_hz=_env_int("REBOT_UPDATE_HZ", 30),
            gripper_exist=os.environ.get("REBOT_GRIPPER", "1") not in ("0", "false", "no"),
            mock=os.environ.get("REBOT_MOCK", "0") not in ("0", "false", "no", ""),
            recordings_dir=recordings,
            return_time_s=_env_float("REBOT_RETURN_TIME", 2.0),
            transition_time_s=_env_float("REBOT_TRANSITION_TIME", 0.6),
            loop_blend_time_s=_env_float("REBOT_LOOP_BLEND_TIME", 0.30),
            end_hold_time_s=_env_float("REBOT_END_HOLD_TIME", 0.15),
            record_filter_alpha=_env_float("REBOT_RECORD_FILTER", 0.35),
            min_record_interval_s=_env_float("REBOT_MIN_REC_INTERVAL", 0.01),
            min_joint_change_rad=_env_float("REBOT_MIN_JOINT_CHANGE", 0.003),
            safety_default_enabled=os.environ.get("REBOT_SAFETY_DEFAULT", "1") not in ("0", "false", "no", ""),
            max_joint_vel_rad_s=_env_float("REBOT_MAX_JOINT_VEL", 4.0),
            spike_threshold_rad=_env_float("REBOT_SPIKE_THRESHOLD", 1.5),
            recover_blend_time_s=_env_float("REBOT_RECOVER_BLEND_TIME", 2.0),
            ws_push_hz=_env_int("REBOT_WS_PUSH_HZ", 10),
        )
