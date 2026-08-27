# -*- coding: utf-8 -*-
"""Voice intent whitelist and Core API mapping (V0 contract)."""
from __future__ import annotations

from typing import Literal

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

# First-phase intents (record start/stop deferred — high mis-trigger risk).
SUPPORTED_INTENTS: tuple[VoiceIntentId, ...] = (
    "estop",
    "resume",
    "stop_play",
    "play_action",
    "goto_pose",
    "free_move",
    "set_policy",
)

# Always allowed when voice is enabled (safety), even under follow_only.
SYSTEM_INTENTS: frozenset[str] = frozenset({"estop"})

INTENT_TO_CORE: dict[str, str] = {
    "estop": "POST /api/pause",
    "resume": "POST /api/resume",
    "stop_play": "POST /api/actions/stop",
    "play_action": "POST /api/actions/{id}/play (resolve name→id)",
    "goto_pose": "POST /api/named_poses/{id}/goto",
    "free_move": "POST /api/free_move",
    "set_policy": "POST /api/voice/policy",
}

# Stable machine codes for TTS / UI (Chinese detail is separate).
ERROR_CODES: dict[str, str] = {
    "voice_disabled": "语音未启用",
    "unknown_intent": "未知意图",
    "policy_denied": "当前语音策略不允许该指令",
    "mode_conflict": "当前运行模式冲突，无法执行",
    "not_found": "未找到对应动作或姿态",
    "bad_slots": "参数不完整或无效",
    "controller_error": "控制层拒绝执行",
}
