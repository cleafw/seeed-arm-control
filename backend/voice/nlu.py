# -*- coding: utf-8 -*-
"""Rule-based Chinese utterance → Voice intent (no LLM required)."""
from __future__ import annotations

import re
from typing import Any


def _norm(text: str) -> str:
    t = text.strip().casefold()
    t = re.sub(r"\s+", "", t)
    return t


# Longer phrases first within each group.
# Keep estop / resume phrases long enough — Vosk small model often hallucinates
# short tokens like「停止」「继续」from ambient noise.
_ESTOP = ("紧急停止", "急停", "马上停", "停下来", "别动了", "不要动")
_RESUME = ("解除锁定", "恢复跟随", "继续跟随", "开始跟随", "解锁")
_STOP_PLAY = ("停止播放", "结束播放", "别播了", "停止动作", "取消播放")
_FREE = ("自由拖动", "自由活动", "可以拖动", "松力模式", "软拖动")
_RECORD_START = ("开始录制", "开始录音", "录制动作")  # deferred — not mapped
_RECORD_STOP = ("结束录制", "停止录制")

_PLAY_PATTERNS = (
    re.compile(r"^(?:播放|执行|跑一下|跑)(?:动作)?[「\"“]?(.+?)[」\"”]?$"),
    re.compile(r"^来一个[「\"“]?(.+?)[」\"”]?$"),
    re.compile(r"^做(?:一个)?[「\"“]?(.+?)[」\"”]?$"),
)
_GOTO_PATTERNS = (
    re.compile(r"^(?:回到|去|到|摆到)[「\"“]?(.+?)[」\"”]?(?:姿态|姿势|位置)?$"),
    re.compile(r"^(?:姿态|姿势)[「\"“]?(.+?)[」\"”]?$"),
)
_POLICY = (
    (("语音优先", "优先语音"), "voice_first"),
    (("跟随优先", "优先跟随", "仅跟随"), "follow_first"),
)


def parse_utterance(text: str) -> dict[str, Any] | None:
    """Return intent body dict or None if unrecognized."""
    raw = text.strip()
    if not raw:
        return None
    t = _norm(raw)

    # Longer / more specific phrases before short estop tokens like「停止」.
    for phrase in _STOP_PLAY:
        if phrase in t:
            return {"intent": "stop_play", "slots": {}, "utterance": raw}

    for phrase in _FREE:
        if phrase in t:
            return {"intent": "free_move", "slots": {}, "utterance": raw}

    for phrase in _ESTOP:
        if phrase in t:
            return {"intent": "estop", "slots": {}, "utterance": raw}

    for phrase in _RESUME:
        if phrase in t:
            return {"intent": "resume", "slots": {}, "utterance": raw}

    for phrases, policy in _POLICY:
        if any(p in t for p in phrases):
            return {
                "intent": "set_policy",
                "slots": {"policy": policy},
                "utterance": raw,
            }

    for pat in _PLAY_PATTERNS:
        m = pat.match(t) or pat.match(raw.replace(" ", ""))
        if m:
            name = m.group(1).strip("「」\"“” ")
            if name:
                mode = "loop" if ("循环" in t or "一直" in t) else "once"
                return {
                    "intent": "play_action",
                    "slots": {"action_name": name, "mode": mode},
                    "utterance": raw,
                }

    for pat in _GOTO_PATTERNS:
        m = pat.match(t)
        if m:
            name = m.group(1).strip("「」\"“” ")
            if name and name not in ("初始",):
                return {
                    "intent": "goto_pose",
                    "slots": {"pose_name": name},
                    "utterance": raw,
                }

    # Common aliases
    if any(x in t for x in ("初始位", "回零", "归零", "home")):
        return {
            "intent": "goto_pose",
            "slots": {"pose_name": "初始位"},
            "utterance": raw,
        }

    # "播放挥手" without regex edge cases
    if t.startswith("播放") and len(t) > 2:
        name = raw.strip()[2:].strip(" 「」\"“”")
        if name:
            return {
                "intent": "play_action",
                "slots": {"action_name": name, "mode": "once"},
                "utterance": raw,
            }

    return None
