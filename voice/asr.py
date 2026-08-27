# -*- coding: utf-8 -*-
"""ASR adapters: text passthrough + optional local Whisper."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class AsrBackend(Protocol):
    def transcribe_pcm16(self, pcm: bytes, sample_rate: int = 16000) -> str: ...
    def transcribe_file(self, path: str | Path) -> str: ...


class TextAsr:
    """No audio — caller already has text."""

    def transcribe_pcm16(self, pcm: bytes, sample_rate: int = 16000) -> str:
        raise RuntimeError("TextAsr does not accept audio; use WhisperAsr or POST text")

    def transcribe_file(self, path: str | Path) -> str:
        raise RuntimeError("TextAsr does not accept audio; use WhisperAsr or POST text")


class WhisperAsr:
    """Load a local Whisper / transformers model once (lazy)."""

    def __init__(self, model_path: str, language: str = "zh"):
        self.model_path = model_path
        self.language = language
        self._pipe = None
        self._backend: str | None = None

    def _ensure(self) -> None:
        if self._pipe is not None:
            return
        path = Path(self.model_path)
        if not path.exists():
            raise FileNotFoundError(f"Whisper model not found: {self.model_path}")

        # Prefer transformers (matches model.safetensors layout)
        try:
            from transformers import pipeline  # type: ignore

            self._pipe = pipeline(
                "automatic-speech-recognition",
                model=str(path),
                device=-1,
            )
            self._backend = "transformers"
            log.info("ASR: transformers Whisper from %s", path)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("transformers ASR unavailable: %s", e)

        try:
            from faster_whisper import WhisperModel  # type: ignore

            self._pipe = WhisperModel(str(path), device="cpu", compute_type="int8")
            self._backend = "faster_whisper"
            log.info("ASR: faster-whisper from %s", path)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("faster-whisper unavailable: %s", e)

        raise RuntimeError(
            "No Whisper backend installed. pip install transformers torch "
            "or faster-whisper, or use text-only /listen."
        )

    def transcribe_file(self, path: str | Path) -> str:
        self._ensure()
        path = Path(path)
        if self._backend == "transformers":
            out = self._pipe(str(path), generate_kwargs={"language": self.language})
            if isinstance(out, dict):
                return str(out.get("text") or "").strip()
            return str(out).strip()
        # faster_whisper
        segments, _info = self._pipe.transcribe(str(path), language=self.language)
        return "".join(s.text for s in segments).strip()

    def transcribe_pcm16(self, pcm: bytes, sample_rate: int = 16000) -> str:
        import tempfile
        import wave

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm)
        try:
            return self.transcribe_file(tmp_path)
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass


def build_asr() -> AsrBackend:
    model = (os.environ.get("VOICE_WHISPER_MODEL") or "").strip()
    if not model:
        log.info("ASR: text-only (set VOICE_WHISPER_MODEL for Whisper)")
        return TextAsr()
    lang = os.environ.get("VOICE_ASR_LANGUAGE", "zh")
    return WhisperAsr(model, language=lang)
