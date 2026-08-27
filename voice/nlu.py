# -*- coding: utf-8 -*-
"""Re-export Core NLU for Voice package callers."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.voice.nlu import parse_utterance  # noqa: E402, F401

__all__ = ["parse_utterance"]
