# -*- coding: utf-8 -*-
"""On-device microphone listen loop (Jetson + ReSpeaker).

Captures via arecord → energy VAD → Vosk (or Whisper) → Core /api/voice/utterance.
Never touches motors.
"""
from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

log = logging.getLogger("voice.device_listen")

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class VoskAsr:
    def __init__(self, model_path: str):
        from vosk import KaldiRecognizer, Model  # type: ignore

        path = Path(model_path)
        if not path.is_dir():
            raise FileNotFoundError(f"Vosk model not found: {model_path}")
        self._model = Model(str(path))
        self._Rec = KaldiRecognizer
        log.info("Vosk model loaded: %s", path)

    def make_stream(self, sample_rate: int = SAMPLE_RATE):
        rec = self._Rec(self._model, sample_rate)
        rec.SetWords(False)
        return rec

    @staticmethod
    def partial_text(rec) -> str:
        try:
            data = json.loads(rec.PartialResult() or "{}")
            return str(data.get("partial") or "").replace(" ", "").strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def final_text(rec) -> str:
        try:
            data = json.loads(rec.FinalResult() or "{}")
            return str(data.get("text") or "").replace(" ", "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def transcribe_pcm16(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> str:
        rec = self.make_stream(sample_rate)
        step = sample_rate * 2  # 1s
        for i in range(0, len(pcm), step):
            rec.AcceptWaveform(pcm[i : i + step])
        return self.final_text(rec)


def build_device_asr():
    """Prefer Vosk (offline CN); fall back to WhisperAsr if configured."""
    vosk_path = (os.environ.get("VOICE_VOSK_MODEL") or "").strip()
    if vosk_path:
        return VoskAsr(vosk_path), "vosk"
    whisper = (os.environ.get("VOICE_WHISPER_MODEL") or "").strip()
    if whisper:
        from .asr import WhisperAsr

        return WhisperAsr(whisper, language=os.environ.get("VOICE_ASR_LANGUAGE", "zh")), "whisper"
    return None, "none"


class DeviceListenLoop:
    def __init__(
        self,
        *,
        on_text: Callable[[str], None],
        on_partial: Callable[[str, bool], None] | None = None,
        device: str | None = None,
        enabled_check: Callable[[], bool] | None = None,
    ):
        self.on_text = on_text
        self.on_partial = on_partial
        self.device = (
            device
            or os.environ.get("VOICE_ALSA_DEVICE")
            or "plughw:CARD=ArrayUAC10,DEV=0"
        )
        self.enabled_check = enabled_check or (lambda: True)
        self.start_rms = _env_float("VOICE_VAD_START_RMS", 350.0)
        self.end_rms = _env_float("VOICE_VAD_END_RMS", 220.0)
        self.silence_ms = _env_int("VOICE_VAD_SILENCE_MS", 700)
        self.min_ms = _env_int("VOICE_VAD_MIN_MS", 450)
        self.max_ms = _env_int("VOICE_VAD_MAX_MS", 5000)
        self.chunk_ms = 30
        self._partial_every_ms = _env_int("VOICE_PARTIAL_EVERY_MS", 120)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._asr = None
        self._asr_name = "none"
        self.last_text = ""
        self.last_partial = ""
        self.last_error: str | None = None
        self.listening = False
        self.frames_heard = 0

    def status(self) -> dict:
        return {
            "listening": self.listening,
            "device": self.device,
            "asr": self._asr_name,
            "last_text": self.last_text,
            "last_partial": self.last_partial,
            "last_error": self.last_error,
            "frames_heard": self.frames_heard,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._asr, self._asr_name = build_device_asr()
        if self._asr is None:
            self.last_error = (
                "未配置 ASR：请设置 VOICE_VOSK_MODEL 或 VOICE_WHISPER_MODEL"
            )
            log.error(self.last_error)
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="device-listen",
            daemon=True,
        )
        self._thread.start()
        log.info("Device listen started on %s (%s)", self.device, self._asr_name)

    def stop(self) -> None:
        self._stop.set()
        self.listening = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None

    def _run(self) -> None:
        chunk_bytes = int(SAMPLE_RATE * self.chunk_ms / 1000) * SAMPLE_WIDTH * CHANNELS
        cmd = [
            "arecord",
            "-D",
            self.device,
            "-f",
            "S16_LE",
            "-r",
            str(SAMPLE_RATE),
            "-c",
            str(CHANNELS),
            "-t",
            "raw",
            "-q",
        ]
        while not self._stop.is_set():
            try:
                self._capture_session(cmd, chunk_bytes)
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                log.warning("arecord session failed: %s", e)
                time.sleep(1.5)

    def _capture_session(self, cmd: list[str], chunk_bytes: int) -> None:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.listening = True
        self.last_error = None
        assert proc.stdout is not None
        speech: bytearray | None = None
        silence_acc = 0
        speech_ms = 0
        stream = None
        since_partial = 0
        use_stream = self._asr_name == "vosk" and hasattr(self._asr, "make_stream")
        try:
            while not self._stop.is_set():
                data = proc.stdout.read(chunk_bytes)
                if not data:
                    err = (proc.stderr.read() if proc.stderr else b"").decode(
                        "utf-8", "replace"
                    )
                    raise RuntimeError(f"arecord ended: {err or 'EOF'}")
                self.frames_heard += 1
                rms = _pcm_rms(data)
                if speech is None:
                    if rms >= self.start_rms and self.enabled_check():
                        speech = bytearray(data)
                        speech_ms = self.chunk_ms
                        silence_acc = 0
                        since_partial = 0
                        if use_stream:
                            stream = self._asr.make_stream(SAMPLE_RATE)
                            stream.AcceptWaveform(data)
                            self._emit_partial(stream)
                    continue
                speech.extend(data)
                speech_ms += self.chunk_ms
                since_partial += self.chunk_ms
                if stream is not None:
                    stream.AcceptWaveform(data)
                    if since_partial >= self._partial_every_ms:
                        since_partial = 0
                        self._emit_partial(stream)
                if rms < self.end_rms:
                    silence_acc += self.chunk_ms
                else:
                    silence_acc = 0
                if silence_acc >= self.silence_ms or speech_ms >= self.max_ms:
                    pcm = bytes(speech)
                    speech = None
                    silence_acc = 0
                    long_enough = (
                        speech_ms - self.silence_ms >= self.min_ms or speech_ms >= self.max_ms
                    )
                    if long_enough:
                        if stream is not None:
                            text = self._asr.final_text(stream)
                            stream = None
                            if text:
                                self.last_text = text
                                self.last_partial = text
                                self._push_partial(text, final=True)
                                log.info("Heard: %s", text)
                                try:
                                    self.on_text(text)
                                except Exception as e:  # noqa: BLE001
                                    self.last_error = f"dispatch: {e}"
                                    log.warning("dispatch failed: %s", e)
                            else:
                                self._push_partial("", final=True)
                        else:
                            self._handle_utterance(pcm)
                    else:
                        stream = None
                        self._push_partial("", final=True)
                    speech_ms = 0
        finally:
            self.listening = False
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    def _emit_partial(self, stream) -> None:
        text = self._asr.partial_text(stream)
        if text and text != self.last_partial:
            self.last_partial = text
            self._push_partial(text, final=False)

    def _push_partial(self, text: str, *, final: bool) -> None:
        if self.on_partial is None:
            return
        try:
            self.on_partial(text, final)
        except Exception as e:  # noqa: BLE001
            log.debug("partial push failed: %s", e)

    def _handle_utterance(self, pcm: bytes) -> None:
        if not self.enabled_check():
            return
        try:
            text = self._asr.transcribe_pcm16(pcm, SAMPLE_RATE)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"ASR: {e}"
            log.warning("ASR failed: %s", e)
            return
        text = (text or "").strip()
        if not text:
            self._push_partial("", final=True)
            return
        text = text.replace(" ", "")
        self.last_text = text
        self.last_partial = text
        self._push_partial(text, final=True)
        log.info("Heard: %s", text)
        try:
            self.on_text(text)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"dispatch: {e}"
            log.warning("dispatch failed: %s", e)


def _pcm_rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    n = len(pcm) // 2
    samples = struct.unpack("<" + "h" * n, pcm[: n * 2])
    if not samples:
        return 0.0
    acc = 0.0
    for s in samples:
        acc += float(s) * float(s)
    return (acc / n) ** 0.5


def write_wav(path: str | Path, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
