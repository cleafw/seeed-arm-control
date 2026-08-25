"""Pydantic request/response models and the internal Action dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


PlayMode = Literal["loop", "once"]
ControllerMode = Literal[
    "idle",
    "follow",
    "record",
    "transition",
    "playback",
    "return_to_follow",
    "paused",
    "calibrate",
    "free_move",
]


# ---------------------------------------------------------------------------
# Internal storage dataclass
# ---------------------------------------------------------------------------

@dataclass
class Action:
    id: str
    name: str
    created_at: str
    updated_at: str
    default_play_mode: PlayMode
    duration_s: float
    frames: list[dict] = field(default_factory=list)

    def meta_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "default_play_mode": self.default_play_mode,
            "duration_s": self.duration_s,
            "frame_count": len(self.frames),
        }

    def full_dict(self) -> dict:
        return {**self.meta_dict(), "frames": self.frames}


# ---------------------------------------------------------------------------
# REST request models
# ---------------------------------------------------------------------------

class RecordStartRequest(BaseModel):
    name: Optional[str] = None


class PlayRequest(BaseModel):
    mode: PlayMode


class SafetyRequest(BaseModel):
    enabled: bool


class ActionPatch(BaseModel):
    name: Optional[str] = None
    default_play_mode: Optional[PlayMode] = None


# ---------------------------------------------------------------------------
# REST response models
# ---------------------------------------------------------------------------

class ArmStatus(BaseModel):
    """Per-arm USB/serial link status for the UI status window."""
    id: str
    label: str
    status: Literal[
        "ok",
        "mock",
        "missing",
        "error",
        "reconnecting",
        "initializing",
    ]
    detail: str = ""
    port: Optional[str] = None


class HealthResponse(BaseModel):
    ok: bool
    mode: ControllerMode
    master_connected: bool
    slave_connected: bool
    leader_profile: Optional[str] = None
    follower_profile: Optional[str] = None
    pair_id: Optional[str] = None


class ArmProfileInfo(BaseModel):
    """Registered arm model (from backend.profiles)."""
    id: str
    role: Literal["leader", "follower"]
    label: str
    label_zh: str
    description: str = ""
    default_baudrate: Optional[int] = None
    capabilities: list[str] = Field(default_factory=list)
    usb_hints: list[dict] = Field(default_factory=list)
    has_detector: bool = False
    has_driver_factory: bool = False


class ProfileSelectRequest(BaseModel):
    """Manual leader/follower selection (phase 1.3)."""
    leader_profile: str
    follower_profile: str


class StateSnapshot(BaseModel):
    ts: float
    mode: ControllerMode
    safety_enabled: bool = True
    recovering: bool = False
    active_action_id: Optional[str] = None
    active_play_mode: Optional[PlayMode] = None
    frame_count: int
    recording_frames: Optional[int] = None
    joint_states: dict = Field(default_factory=dict)
    master_joint_states: dict = Field(default_factory=dict)
    slave_joint_states: dict = Field(default_factory=dict)
    calibration: dict = Field(default_factory=dict)
    motor_map: dict = Field(default_factory=dict)
    motor_map_blending: bool = False
    last_error: Optional[str] = None
    arms: dict = Field(default_factory=dict)
    # Active arm pairing (phase 1.2); ids match backend.profiles
    leader_profile: Optional[str] = None
    follower_profile: Optional[str] = None
    pair_id: Optional[str] = None


class MotorMapRequest(BaseModel):
    """Master joint → {slave, dir} or legacy string/null."""
    map: dict[str, Any] = Field(default_factory=dict)


class ActionMeta(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    default_play_mode: PlayMode
    duration_s: float
    frame_count: int
