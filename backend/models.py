"""Pydantic request/response models and the internal Action dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


PlayMode = Literal["loop", "once"]
VoicePolicy = Literal[
    "follow_only",
    "voice_in_follow",
    "follow_first",
    "voice_first",
]
VoiceIntentId = Literal[
    "estop",
    "resume",
    "stop_play",
    "play_action",
    "goto_pose",
    "free_move",
    "set_policy",
]
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
        "missing",
        "error",
        "reconnecting",
        "initializing",
    ]
    detail: str = ""
    port: Optional[str] = None


class VoiceStatus(BaseModel):
    """Compact voice status embedded in health / WS snapshot."""
    enabled: bool = False
    reachable: bool = False
    policy: VoicePolicy = "voice_in_follow"
    last_intent: Optional[dict] = None
    # Live subtitle from device ASR (partial or final).
    live_text: Optional[str] = None
    live_partial: bool = False
    device_listening: bool = False


class VoiceCapability(BaseModel):
    enabled: bool
    reachable: bool
    policy: VoicePolicy
    supported_intents: list[str] = Field(default_factory=list)
    health_url: Optional[str] = None
    last_health_check: Optional[float] = None
    last_health_error: Optional[str] = None
    last_intent: Optional[dict] = None
    live_text: Optional[str] = None
    live_partial: bool = False
    device_listening: bool = False


class VoiceIntentRequest(BaseModel):
    intent: VoiceIntentId
    slots: dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None
    source: str = "voice"
    utterance: Optional[str] = None


class VoiceUtteranceRequest(BaseModel):
    """Chinese text → rule NLU → intent (UI / Mock without ASR)."""
    text: str
    source: str = "ui"


class VoiceLiveCaptionRequest(BaseModel):
    """Device ASR subtitle push (partial while speaking / final)."""
    text: str = ""
    partial: bool = True


class VoicePolicyRequest(BaseModel):
    policy: VoicePolicy


class VoiceSettingsRequest(BaseModel):
    """Runtime voice enable + priority (UI checkbox / dropdown)."""
    enabled: Optional[bool] = None
    policy: Optional[VoicePolicy] = None


class NamedPoseBody(BaseModel):
    name: str
    joint_states: dict[str, float]
    id: Optional[str] = None
    aliases: Optional[list[str]] = None


class NamedPoseInfo(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    joint_states: dict[str, float] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class HealthResponse(BaseModel):
    ok: bool
    mode: ControllerMode
    master_connected: bool
    slave_connected: bool
    leader_profile: Optional[str] = None
    follower_profile: Optional[str] = None
    pair_id: Optional[str] = None
    voice: Optional[VoiceStatus] = None


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
    """Legacy manual select (kept for API); UI uses auto-detect instead."""
    leader_profile: str
    follower_profile: str


class PortSelectRequest(BaseModel):
    """Explicit role-to-port binding for visually identical arm adapters."""

    leader_port: str
    follower_port: str


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
    # Last USB profile auto-detect result
    profile_detect: Optional[dict] = None
    # Optional voice module status (absent / disabled when VOICE_ENABLED=0)
    voice: Optional[VoiceStatus] = None


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
