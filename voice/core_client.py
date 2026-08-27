# -*- coding: utf-8 -*-
"""HTTP client: Voice → Core (never touches motors)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def core_base() -> str:
    return os.environ.get("REBOT_CORE_URL", "http://127.0.0.1:8000").rstrip("/")


def post_json(path: str, body: dict[str, Any], *, timeout: float = 15.0) -> dict[str, Any]:
    url = core_base() + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            parsed = {"detail": detail}
        raise RuntimeError(f"Core HTTP {e.code}: {parsed}") from e


def post_intent(body: dict[str, Any]) -> dict[str, Any]:
    return post_json("/api/voice/intent", body)


def post_utterance(text: str, *, source: str = "voice") -> dict[str, Any]:
    return post_json("/api/voice/utterance", {"text": text, "source": source})


def post_live_caption(text: str, *, partial: bool = True) -> dict[str, Any]:
    return post_json(
        "/api/voice/live",
        {"text": text, "partial": partial},
        timeout=2.0,
    )
