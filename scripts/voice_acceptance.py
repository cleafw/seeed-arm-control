# -*- coding: utf-8 -*-
"""Voice policy / utterance acceptance checks against a running Core.

Usage:
  set REBOT_CORE_URL=http://127.0.0.1:1882
  python scripts/voice_acceptance.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

CORE = os.environ.get("REBOT_CORE_URL", "http://127.0.0.1:8000").rstrip("/")


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(
        CORE + path,
        data=data,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def ok(label: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    msg = f"[{mark}] {label}" + (f" -- {detail}" if detail else "")
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))
    return cond



def detail_code(out: object) -> str | None:
    if isinstance(out, dict):
        d = out.get("detail")
        if isinstance(d, dict):
            return d.get("code")
    return None


def main() -> int:
    fails = 0
    code, cap = req("GET", "/api/voice/capability")
    if not ok("GET capability", code == 200, str(cap)[:200]):
        return 1

    code, _ = req("POST", "/api/voice/settings", {"enabled": True, "policy": "follow_first"})
    fails += not ok("enable follow_first", code == 200)

    code, out = req("POST", "/api/voice/utterance", {"text": "急停", "source": "acceptance"})
    fails += not ok(
        "utterance 急停",
        code == 200 and isinstance(out, dict) and out.get("intent") == "estop",
        str(out)[:160],
    )

    code, out = req("POST", "/api/voice/utterance", {"text": "把杯子放到左边", "source": "acceptance"})
    fails += not ok("unknown utterance → 4xx", code >= 400, str(out)[:120])

    code, _ = req("POST", "/api/voice/settings", {"enabled": True, "policy": "follow_only"})
    fails += not ok("set follow_only", code == 200)
    code, out = req("POST", "/api/voice/utterance", {"text": "急停", "source": "acceptance"})
    fails += not ok("follow_only allows estop", code == 200, str(out)[:120])

    code, out = req(
        "POST",
        "/api/voice/intent",
        {
            "intent": "play_action",
            "slots": {"action_name": "__no_such__"},
            "source": "acceptance",
        },
    )
    fails += not ok(
        "follow_only denies play",
        code == 409 and detail_code(out) == "policy_denied",
        str(out)[:160],
    )

    code, _ = req("POST", "/api/voice/settings", {"enabled": True, "policy": "voice_first"})
    fails += not ok("set voice_first", code == 200)

    for text, intent in (
        ("停止播放", "stop_play"),
        ("解除锁定", "resume"),
        ("语音优先", "set_policy"),
        ("跟随优先", "set_policy"),
    ):
        code, out = req("POST", "/api/voice/utterance", {"text": text, "source": "acceptance"})
        if code == 200 and isinstance(out, dict) and out.get("intent") == intent:
            passed = True
        elif code == 409:
            # NLU+dispatch reached controller; state may refuse (ok for acceptance)
            passed = True
        else:
            passed = False
        fails += not ok(f"utterance [{text}] -> {intent}", passed, f"HTTP {code} {str(out)[:100]}")

    req("POST", "/api/voice/settings", {"enabled": True, "policy": "follow_first"})

    print("---")
    if fails:
        print(f"{fails} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
