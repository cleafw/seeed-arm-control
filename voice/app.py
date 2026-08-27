# -*- coding: utf-8 -*-
"""Voice FastAPI on Jetson: health + text listen + optional device mic loop.

Env:
  REBOT_CORE_URL          Core base (default http://127.0.0.1:8000)
  VOICE_DEVICE_LISTEN     1 = start ReSpeaker listen loop on boot
  VOICE_VOSK_MODEL        path to vosk Chinese model dir
  VOICE_WHISPER_MODEL     optional Whisper dir
  VOICE_ALSA_DEVICE       default plughw:CARD=ArrayUAC10,DEV=0
  VOICE_TTS               log|espeak|off
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.voice.nlu import parse_utterance  # noqa: E402

from .asr import TextAsr, build_asr  # noqa: E402
from .core_client import core_base, post_intent, post_live_caption, post_utterance  # noqa: E402
from .device_listen import DeviceListenLoop  # noqa: E402
from .tts import speak  # noqa: E402

log = logging.getLogger("voice")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_asr = build_asr()
_device_loop: DeviceListenLoop | None = None


def _core_voice_enabled() -> bool:
    """Only dispatch while Core has voice enabled (UI checkbox / settings)."""
    import json
    import urllib.request

    try:
        url = core_base() + "/api/voice/capability"
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("enabled"))
    except Exception:  # noqa: BLE001
        return False


def _on_device_partial(text: str, final: bool) -> None:
    try:
        post_live_caption(text, partial=not final)
    except Exception as e:  # noqa: BLE001
        log.debug("live caption push failed: %s", e)


def _on_device_text(text: str) -> None:
    try:
        post_live_caption(text, partial=False)
    except Exception:  # noqa: BLE001
        pass
    speak(f"听到：{text}")
    try:
        out = post_utterance(text, source="device_mic")
        intent = out.get("intent")
        speak(f"已执行：{intent}")
        log.info("dispatched ok intent=%s text=%s", intent, text)
    except Exception as e:  # noqa: BLE001
        speak(f"失败：{e}")
        log.warning("device dispatch failed: %s", e)
        raise


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _device_loop
    if os.environ.get("VOICE_DEVICE_LISTEN", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        _device_loop = DeviceListenLoop(
            on_text=_on_device_text,
            on_partial=_on_device_partial,
            enabled_check=_core_voice_enabled,
        )
        _device_loop.start()
    yield
    if _device_loop is not None:
        _device_loop.stop()
        _device_loop = None


app = FastAPI(title="seeed-arm-control Voice", version="0.2.0", lifespan=lifespan)


class ListenText(BaseModel):
    text: str = Field(..., min_length=1)
    source: str = "voice"
    request_id: Optional[str] = None
    dispatch: bool = True


class HealthOut(BaseModel):
    ok: bool = True
    service: str = "voice"
    asr: str
    whisper_model: Optional[str] = None
    vosk_model: Optional[str] = None
    device_listen: bool = False
    listening: bool = False
    alsa_device: Optional[str] = None
    last_text: Optional[str] = None
    last_error: Optional[str] = None


@app.get("/health", response_model=HealthOut)
def health():
    st = _device_loop.status() if _device_loop else {}
    asr_name = st.get("asr") or (
        "whisper" if not isinstance(_asr, TextAsr) else "text"
    )
    return HealthOut(
        ok=True,
        asr=str(asr_name),
        whisper_model=(os.environ.get("VOICE_WHISPER_MODEL") or "").strip() or None,
        vosk_model=(os.environ.get("VOICE_VOSK_MODEL") or "").strip() or None,
        device_listen=_device_loop is not None,
        listening=bool(st.get("listening")),
        alsa_device=st.get("device"),
        last_text=st.get("last_partial") or st.get("last_text") or None,
        last_error=st.get("last_error"),
    )


@app.post("/listen")
def listen_text(body: ListenText) -> dict[str, Any]:
    text = body.text.strip()
    parsed = parse_utterance(text)
    if not parsed:
        speak(f"未识别：{text}")
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_intent", "message": f"未识别指令：「{text}」"},
        )
    rid = body.request_id or str(uuid.uuid4())
    if not body.dispatch:
        return {"ok": True, "parsed": parsed, "request_id": rid, "dispatched": False}

    try:
        out = post_utterance(text, source=body.source)
    except Exception as e:  # noqa: BLE001
        try:
            out = post_intent({**parsed, "source": body.source, "request_id": rid})
        except Exception as e2:  # noqa: BLE001
            speak(f"失败：{e2}")
            raise HTTPException(
                status_code=502,
                detail={"code": "core_unreachable", "message": str(e2)},
            ) from e2
        log.warning("Core /utterance failed (%s); used /intent", e)

    intent = out.get("intent") or parsed.get("intent")
    speak(f"已执行：{intent}")
    return {"ok": True, "parsed": parsed, "core": out, "request_id": rid, "dispatched": True}


@app.post("/listen/audio")
async def listen_audio(request: Request) -> dict[str, Any]:
    if isinstance(_asr, TextAsr) and (
        not _device_loop or _device_loop.status().get("asr") == "none"
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "asr_unavailable",
                "message": "未配置 ASR（VOICE_VOSK_MODEL / VOICE_WHISPER_MODEL）",
            },
        )
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio")
    dispatch = request.query_params.get("dispatch", "true").lower() not in (
        "0",
        "false",
        "no",
    )
    source = request.query_params.get("source", "voice")
    import tempfile

    suffix = ".wav"
    ctype = (request.headers.get("content-type") or "").lower()
    if "webm" in ctype:
        suffix = ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        engine = _asr if not isinstance(_asr, TextAsr) else None
        if engine is None and _device_loop and _device_loop._asr is not None:
            # reuse device vosk via file
            from .device_listen import SAMPLE_RATE
            import wave

            with wave.open(tmp_path, "rb") as wf:
                pcm = wf.readframes(wf.getnframes())
                text = _device_loop._asr.transcribe_pcm16(pcm, wf.getframerate() or SAMPLE_RATE)
        else:
            text = engine.transcribe_file(tmp_path)  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"code": "asr_error", "message": str(e)},
        ) from e
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass

    if not text:
        speak("没有听清")
        raise HTTPException(
            status_code=400,
            detail={"code": "empty_transcript", "message": "ASR 空结果"},
        )
    return listen_text(ListenText(text=text, source=source, dispatch=dispatch))


def main() -> None:
    import uvicorn

    uvicorn.run(
        "voice.app:app",
        host=os.environ.get("VOICE_HOST", "0.0.0.0"),
        port=int(os.environ.get("VOICE_PORT", "1883")),
        reload=False,
    )


if __name__ == "__main__":
    main()
