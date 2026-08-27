# -*- coding: utf-8 -*-
"""Voice capability + intent dispatch into Controller (never touches hardware)."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from ..controller import Controller, ControllerError
from ..models import PlayMode
from ..storage import ActionLibrary
from .intents import (
    ERROR_CODES,
    SUPPORTED_INTENTS,
    SYSTEM_INTENTS,
    VoiceIntentId,
    VoicePolicy,
)
from .named_poses import NamedPoseStore
from .nlu import parse_utterance

log = logging.getLogger(__name__)

MASTER_MOTION_RAD = 0.03


class VoiceError(Exception):
    def __init__(self, code: str, detail: str | None = None, http_status: int = 400):
        self.code = code
        self.detail = detail or ERROR_CODES.get(code, code)
        self.http_status = http_status
        super().__init__(self.detail)


class VoiceService:
    def __init__(
        self,
        *,
        enabled: bool,
        health_url: str | None,
        policy: VoicePolicy,
        health_interval_s: float,
        controller: Controller,
        library: ActionLibrary,
        poses: NamedPoseStore,
        settings_path: Path | None = None,
    ):
        self.health_url = (health_url or "").strip() or None
        self.health_interval_s = max(1.0, health_interval_s)
        self.controller = controller
        self.library = library
        self.poses = poses
        self.settings_path = Path(settings_path) if settings_path else None

        self._lock = threading.RLock()
        self.enabled = bool(enabled)
        self._policy: VoicePolicy = policy if policy in (
            "follow_only",
            "voice_in_follow",
            "follow_first",
            "voice_first",
        ) else "follow_first"
        self._load_settings()

        self._reachable = False
        if self.enabled and not self.health_url:
            self._reachable = True
        self._last_health_check: float | None = None
        self._last_health_error: str | None = None
        self._last_intent: dict[str, Any] | None = None
        self._master_motion_until = 0.0
        self._last_master_js: dict[str, float] | None = None
        self._live_text: str = ""
        self._live_partial: bool = False
        self._device_listening: bool = False
        self._voice_cooldown_until = 0.0
        self._suppress_estop_until = 0.0

    def _load_settings(self) -> None:
        if not self.settings_path or not self.settings_path.exists():
            return
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                data = json.load(f)
            if "enabled" in data:
                self.enabled = bool(data["enabled"])
            pol = data.get("policy")
            if pol in (
                "follow_only",
                "voice_in_follow",
                "follow_first",
                "voice_first",
            ):
                self._policy = pol
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to load voice settings: %s", e)

    def _save_settings(self) -> None:
        if not self.settings_path:
            return
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.settings_path.with_suffix(".json.tmp")
            payload = {"enabled": self.enabled, "policy": self._policy}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp.replace(self.settings_path)
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to save voice settings: %s", e)

    @property
    def policy(self) -> VoicePolicy:
        with self._lock:
            return self._policy

    def set_policy(self, policy: VoicePolicy) -> VoicePolicy:
        if policy not in (
            "follow_only",
            "voice_in_follow",
            "follow_first",
            "voice_first",
        ):
            raise VoiceError("bad_slots", f"invalid policy: {policy}")
        with self._lock:
            self._policy = policy
            self._save_settings()
        log.info("Voice policy → %s", policy)
        return policy

    def set_enabled(self, enabled: bool) -> bool:
        with self._lock:
            self.enabled = bool(enabled)
            if self.enabled and not self.health_url:
                self._reachable = True
            if not self.enabled:
                self._reachable = False
            self._save_settings()
        log.info("Voice enabled → %s (policy=%s)", self.enabled, self._policy)
        return self.enabled

    def update_settings(
        self,
        *,
        enabled: bool | None = None,
        policy: VoicePolicy | None = None,
    ) -> dict[str, Any]:
        if policy is not None:
            self.set_policy(policy)
        if enabled is not None:
            self.set_enabled(enabled)
        return self.capability()

    def note_master_motion(self) -> None:
        with self._lock:
            self._master_motion_until = time.monotonic() + 0.8

    def on_master_js(self, js: dict) -> None:
        """Control-thread hook: follow_first may preempt voice on master motion."""
        if not js:
            return
        with self._lock:
            enabled = self.enabled
            policy = self._policy
            prev = self._last_master_js
            self._last_master_js = {k: float(v) for k, v in js.items()}
            cur = self._last_master_js
        if not enabled or prev is None:
            return
        if not self._moved(prev, cur):
            return
        self.note_master_motion()
        if policy != "follow_first":
            return
        mode = self.controller.mode
        if mode in ("playback", "transition", "return_to_follow"):
            log.info("follow_first: master motion interrupts voice (%s)", mode)
            try:
                self.controller.stop_playback()
            except Exception as e:  # noqa: BLE001
                log.warning("interrupt voice failed: %s", e)

    @staticmethod
    def _moved(a: dict, b: dict, thr: float = MASTER_MOTION_RAD) -> bool:
        for k in set(a) & set(b):
            try:
                if abs(float(a[k]) - float(b[k])) > thr:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def set_live_caption(self, text: str, *, partial: bool = True) -> dict[str, Any]:
        """Device ASR pushes live subtitle for UI (does not dispatch intents)."""
        cleaned = (text or "").replace(" ", "").strip()
        with self._lock:
            self._live_text = cleaned
            self._live_partial = bool(partial)
            if cleaned and not partial:
                # Keep final line visible until next speech.
                pass
        return {
            "ok": True,
            "live_text": cleaned,
            "live_partial": bool(partial),
        }

    def ping_health(self) -> bool:
        if not self.enabled:
            with self._lock:
                self._reachable = False
                self._device_listening = False
                self._last_health_check = time.time()
            return False
        if not self.health_url:
            with self._lock:
                self._reachable = True
                self._last_health_error = None
                self._last_health_check = time.time()
            return True
        ok = False
        err: str | None = None
        listening = False
        remote_text: str | None = None
        try:
            req = urllib.request.Request(self.health_url, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                ok = 200 <= getattr(resp, "status", 200) < 300
                if not ok:
                    err = f"HTTP {resp.status}"
                else:
                    try:
                        body = json.loads(resp.read().decode("utf-8"))
                        listening = bool(body.get("listening") or body.get("device_listen"))
                        remote_text = body.get("last_text") or None
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            err = str(e)
            ok = False
        with self._lock:
            self._reachable = ok
            self._last_health_error = err
            self._last_health_check = time.time()
            self._device_listening = bool(ok and listening)
            # Prefer live captions pushed by device; fall back to health last_text.
            if remote_text and not self._live_text:
                self._live_text = str(remote_text).replace(" ", "")
                self._live_partial = False
        return ok

    def capability(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "reachable": bool(self.enabled and self._reachable),
                "policy": self._policy,
                "supported_intents": list(SUPPORTED_INTENTS),
                "health_url": self.health_url,
                "last_health_check": self._last_health_check,
                "last_health_error": self._last_health_error,
                "last_intent": self._last_intent,
                "live_text": self._live_text or None,
                "live_partial": self._live_partial,
                "device_listening": self._device_listening,
            }

    def status_for_snapshot(self) -> dict[str, Any]:
        cap = self.capability()
        return {
            "enabled": cap["enabled"],
            "reachable": cap["reachable"],
            "policy": cap["policy"],
            "last_intent": cap["last_intent"],
            "live_text": cap["live_text"],
            "live_partial": cap["live_partial"],
            "device_listening": cap["device_listening"],
        }

    def _note_last(
        self,
        *,
        ok: bool,
        intent: str | None = None,
        utterance: str | None = None,
        source: str | None = None,
        slots: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        message: str | None = None,
        request_id: Any = None,
    ) -> None:
        with self._lock:
            self._last_intent = {
                "ts": time.time(),
                "ok": ok,
                "intent": intent,
                "utterance": utterance,
                "source": source,
                "slots": slots or {},
                "result": result,
                "error": error,
                "message": message,
                "request_id": request_id,
            }

    def handle_utterance(self, text: str, *, source: str = "utterance") -> dict[str, Any]:
        """Rule NLU on Chinese text → handle_intent (Core-side closed loop).

        If rules miss, fall back to action-library / named-pose name match so
        saying an action name like「立起来」plays that action.
        """
        raw = (text or "").strip()
        parsed = parse_utterance(raw)
        if not parsed:
            parsed = self._resolve_library_utterance(raw)
        if not parsed:
            msg = f"未识别指令：「{raw}」（也不是动作库/姿态名）"
            self._note_last(
                ok=False,
                intent=None,
                utterance=raw,
                source=source,
                error="unknown_intent",
                message=msg,
            )
            raise VoiceError("unknown_intent", msg, http_status=400)
        parsed["source"] = source
        if not parsed.get("utterance"):
            parsed["utterance"] = raw
        return self.handle_intent(parsed)

    def _resolve_library_utterance(self, raw: str) -> dict[str, Any] | None:
        """Match bare phrases to action / pose names (exact, then unique contains)."""
        t = raw.strip()
        if not t:
            return None
        for prefix in (
            "播放动作",
            "播放",
            "执行动作",
            "执行",
            "跑一下",
            "来一个",
            "做一个",
            "做",
        ):
            if t.startswith(prefix) and len(t) > len(prefix):
                t = t[len(prefix) :].strip(" 「」\"“”")
                break
        key = t.casefold()
        if len(key) < 1:
            return None

        def _play(name: str) -> dict[str, Any]:
            return {
                "intent": "play_action",
                "slots": {"action_name": name, "mode": "once"},
                "utterance": raw,
            }

        actions = list(self.library.list())
        exact = [a for a in actions if a.name.strip().casefold() == key]
        if len(exact) == 1:
            return _play(exact[0].name)

        # Utterance is a unique substring of one action name（「立」→「立起来」仅当唯一）
        in_name = [a for a in actions if key in a.name.strip().casefold()]
        if len({a.name.strip().casefold() for a in in_name}) == 1:
            return _play(in_name[0].name)

        # Action name uniquely appears inside the utterance（「请立起来」）
        if len(key) >= 2:
            embedded = [a for a in actions if a.name.strip().casefold() in key]
            if embedded:
                embedded.sort(key=lambda a: len(a.name.strip()), reverse=True)
                best = embedded[0]
                # If top two same length different names → ambiguous
                if len(embedded) == 1 or len(best.name.strip()) > len(
                    embedded[1].name.strip()
                ):
                    return _play(best.name)

        poses = self.poses.list()
        pose_hits = []
        for p in poses:
            names = [str(p.get("name") or "").strip()]
            names.extend(str(a).strip() for a in (p.get("aliases") or []))
            names = [n for n in names if n]
            if any(n.casefold() == key for n in names):
                pose_hits.append(p)
            elif len(key) >= 2 and any(
                key in n.casefold() or n.casefold() in key for n in names
            ):
                pose_hits.append(p)
        # unique pose
        ids = {p.get("id") for p in pose_hits}
        if len(ids) == 1:
            p = pose_hits[0]
            return {
                "intent": "goto_pose",
                "slots": {"pose_name": p.get("name"), "pose_id": p.get("id")},
                "utterance": raw,
            }
        return None

    def handle_intent(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise VoiceError("voice_disabled", "请先在页面勾选启用语音控制", http_status=503)

        intent = str(body.get("intent") or "").strip()
        utterance = body.get("utterance")
        source = body.get("source") or "voice"
        if intent not in SUPPORTED_INTENTS:
            msg = f"未知意图：{intent or '(empty)'}"
            self._note_last(
                ok=False,
                intent=intent or None,
                utterance=utterance,
                source=source,
                error="unknown_intent",
                message=msg,
                request_id=body.get("request_id"),
            )
            raise VoiceError("unknown_intent", msg, http_status=400)

        slots = body.get("slots") or {}
        if not isinstance(slots, dict):
            raise VoiceError("bad_slots", "slots must be an object")

        now = time.monotonic()
        with self._lock:
            cool = self._voice_cooldown_until
            suppress_estop = self._suppress_estop_until
        if intent == "estop" and now < suppress_estop:
            raise VoiceError(
                "policy_denied",
                "刚解除锁定，暂忽略可能的误识别急停（请说「急停」再试）",
                http_status=409,
            )
        if intent != "estop" and now < cool:
            raise VoiceError(
                "policy_denied",
                "上一条语音指令冷却中，请稍后再说",
                http_status=409,
            )

        try:
            self._check_policy(intent)
            self._check_mode_conflict(intent)
            result = self._dispatch(intent, slots)  # type: ignore[arg-type]
        except VoiceError as e:
            self._note_last(
                ok=False,
                intent=intent,
                utterance=utterance,
                source=source,
                slots=slots,
                error=e.code,
                message=e.detail,
                request_id=body.get("request_id"),
            )
            raise
        except ControllerError as e:
            self._note_last(
                ok=False,
                intent=intent,
                utterance=utterance,
                source=source,
                slots=slots,
                error="controller_error",
                message=str(e),
                request_id=body.get("request_id"),
            )
            raise VoiceError("controller_error", str(e), http_status=409) from e

        out = {
            "ok": True,
            "intent": intent,
            "request_id": body.get("request_id"),
            "source": source,
            "utterance": utterance,
            "result": result,
            "snapshot": self.controller.snapshot(),
        }
        self._note_last(
            ok=True,
            intent=intent,
            utterance=utterance,
            source=source,
            slots=slots,
            result=result,
            message=self._result_summary(intent, result),
            request_id=body.get("request_id"),
        )
        with self._lock:
            self._voice_cooldown_until = time.monotonic() + 1.2
            if intent == "resume":
                # Soft-approach + noisy ASR often false-fires「停止」right after unlock.
                self._suppress_estop_until = time.monotonic() + 3.0
        return out

    @staticmethod
    def _result_summary(intent: str, result: dict[str, Any] | None) -> str:
        if not result:
            return intent
        action = result.get("action")
        if action == "play":
            return f"播放「{result.get('action_name') or '?'}」({result.get('mode') or 'once'})"
        if action == "goto_pose":
            return f"去姿态「{result.get('pose_name') or result.get('pose_id') or '?'}」"
        if action == "set_policy":
            return f"策略 → {result.get('policy')}"
        if action == "pause":
            return "急停 / 暂停"
        if action == "resume":
            return "恢复跟随"
        if action == "stop_playback":
            return "停止播放"
        if action == "free_move":
            return "自由拖动"
        return str(action or intent)

    def _check_policy(self, intent: str) -> None:
        policy = self.policy
        if intent in SYSTEM_INTENTS:
            return
        if policy == "follow_only":
            raise VoiceError(
                "policy_denied",
                "当前未启用语音动作（仅跟随），仅允许急停",
                http_status=409,
            )
        if policy == "follow_first" and intent in ("play_action", "goto_pose", "free_move"):
            with self._lock:
                busy = time.monotonic() < self._master_motion_until
            if busy:
                raise VoiceError(
                    "policy_denied",
                    "跟随优先：主臂正在运动，语音动作已拒绝（主臂可随时打断语音）",
                    http_status=409,
                )

    def _check_mode_conflict(self, intent: str) -> None:
        mode = self.controller.mode
        if intent in ("estop", "stop_play", "set_policy", "resume"):
            return
        if mode == "calibrate":
            raise VoiceError("mode_conflict", "校准中，请先完成或取消校准", http_status=409)
        if mode == "record" and intent in ("play_action", "goto_pose", "free_move"):
            raise VoiceError("mode_conflict", "录制中不能执行该语音指令", http_status=409)

    def _dispatch(self, intent: VoiceIntentId, slots: dict[str, Any]) -> dict[str, Any]:
        if intent == "estop":
            self.controller.pause()
            return {"action": "pause"}
        if intent == "resume":
            self.controller.resume()
            return {"action": "resume"}
        if intent == "stop_play":
            self.controller.stop_playback()
            return {"action": "stop_playback"}
        if intent == "free_move":
            self.controller.start_free_move()
            return {"action": "free_move"}
        if intent == "set_policy":
            policy = slots.get("policy")
            if not policy:
                raise VoiceError("bad_slots", "set_policy 需要 slots.policy")
            self.set_policy(policy)
            return {"action": "set_policy", "policy": policy}
        if intent == "play_action":
            return self._play_action(slots)
        if intent == "goto_pose":
            return self._goto_pose(slots)
        raise VoiceError("unknown_intent", intent)

    def _resolve_action(self, slots: dict[str, Any]):
        action_id = (slots.get("action_id") or "").strip()
        action_name = (slots.get("action_name") or "").strip()
        if action_id:
            if not self.library.exists(action_id):
                raise VoiceError("not_found", f"动作不存在：{action_id}", http_status=404)
            return self.library.get(action_id)
        if not action_name:
            raise VoiceError("bad_slots", "play_action 需要 action_id 或 action_name")
        matches = [
            a
            for a in self.library.list()
            if a.name.strip().casefold() == action_name.casefold()
        ]
        if not matches:
            raise VoiceError("not_found", f"未找到名为「{action_name}」的动作", http_status=404)
        if len(matches) > 1:
            raise VoiceError(
                "not_found",
                f"动作名「{action_name}」不唯一，请用 action_id",
                http_status=409,
            )
        return matches[0]

    def _play_action(self, slots: dict[str, Any]) -> dict[str, Any]:
        action = self._resolve_action(slots)
        mode_raw = slots.get("mode") or action.default_play_mode
        if mode_raw not in ("loop", "once"):
            raise VoiceError("bad_slots", f"invalid play mode: {mode_raw}")
        mode: PlayMode = mode_raw  # type: ignore[assignment]
        # start_playback stops follow — voice_first preempts teleop.
        self.controller.start_playback(action.id, mode)
        return {
            "action": "play",
            "action_id": action.id,
            "action_name": action.name,
            "mode": mode,
        }

    def _goto_pose(self, slots: dict[str, Any]) -> dict[str, Any]:
        pose_id = (slots.get("pose_id") or "").strip()
        pose_name = (slots.get("pose_name") or "").strip()
        pose: Optional[dict[str, Any]] = None
        if pose_id:
            pose = self.poses.get(pose_id)
        elif pose_name:
            pose = self.poses.find_by_name(pose_name)
        else:
            raise VoiceError("bad_slots", "goto_pose 需要 pose_id 或 pose_name")
        if not pose:
            raise VoiceError(
                "not_found",
                f"未找到姿态：{pose_id or pose_name}",
                http_status=404,
            )
        js = pose.get("joint_states") or {}
        self.controller.goto_joint_states(js)
        return {
            "action": "goto_pose",
            "pose_id": pose["id"],
            "pose_name": pose.get("name"),
        }
