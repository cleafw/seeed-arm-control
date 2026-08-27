# -*- coding: utf-8 -*-
"""Inject a voice intent or Chinese utterance into Core (Mock Voice).

Usage:
  set VOICE_ENABLED=1
  python -m voice.mock_inject estop
  python -m voice.mock_inject play_action --name 挥手 --mode once
  python -m voice.mock_inject --text 急停
  python -m voice.mock_inject --text 播放挥手

Env:
  REBOT_CORE_URL   default http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Mock voice → Core intent / utterance")
    p.add_argument(
        "intent",
        nargs="?",
        help="estop|resume|stop_play|play_action|goto_pose|free_move|set_policy",
    )
    p.add_argument("--text", help="Chinese utterance (rule NLU via /api/voice/utterance)")
    p.add_argument("--core", default=os.environ.get("REBOT_CORE_URL", "http://127.0.0.1:8000"))
    p.add_argument("--name", help="action_name for play_action")
    p.add_argument("--action-id", help="action_id for play_action")
    p.add_argument("--mode", choices=["loop", "once"], default="once")
    p.add_argument("--pose", help="pose_name for goto_pose")
    p.add_argument("--pose-id", help="pose_id for goto_pose")
    p.add_argument(
        "--policy",
        choices=["follow_only", "voice_in_follow", "follow_first", "voice_first"],
    )
    p.add_argument("--utterance", default=None)
    args = p.parse_args(argv)

    if args.text:
        url = args.core.rstrip("/") + "/api/voice/utterance"
        try:
            out = post(url, {"text": args.text, "source": "mock"})
        except urllib.error.HTTPError as e:
            print(e.read().decode("utf-8", "replace"), file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if not args.intent:
        p.error("provide intent or --text")

    slots: dict = {}
    if args.intent == "play_action":
        if args.action_id:
            slots["action_id"] = args.action_id
        if args.name:
            slots["action_name"] = args.name
        slots["mode"] = args.mode
    elif args.intent == "goto_pose":
        if args.pose_id:
            slots["pose_id"] = args.pose_id
        if args.pose:
            slots["pose_name"] = args.pose
    elif args.intent == "set_policy":
        if not args.policy:
            print("set_policy requires --policy", file=sys.stderr)
            return 2
        slots["policy"] = args.policy

    body = {
        "intent": args.intent,
        "slots": slots,
        "source": "mock",
        "utterance": args.utterance,
    }
    url = args.core.rstrip("/") + "/api/voice/intent"
    try:
        out = post(url, body)
    except urllib.error.HTTPError as e:
        print(e.read().decode("utf-8", "replace"), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
