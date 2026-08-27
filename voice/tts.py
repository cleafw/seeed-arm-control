# -*- coding: utf-8 -*-
"""TTS: log by default; optional system say / espeak."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)


def speak(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    mode = (os.environ.get("VOICE_TTS") or "log").strip().lower()
    log.info("TTS[%s]: %s", mode, text)
    if mode in ("", "log", "off", "0", "false", "none"):
        return
    if mode == "espeak" and shutil.which("espeak"):
        subprocess.Popen(
            ["espeak", "-v", "zh", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    if mode == "say" and shutil.which("say"):
        subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
