"""Joint range calibration: collect min/max, persist, map master → slave."""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

JOINT_KEYS = (
    "joint1",
    "joint2",
    "joint3",
    "joint4",
    "joint5",
    "joint6",
    "gripper",
)

# Minimum swept span (rad) to treat a joint range as usable for mapping.
MIN_SPAN = 0.05


def empty_ranges() -> dict[str, dict[str, float]]:
    return {k: {"min": 0.0, "max": 0.0} for k in JOINT_KEYS}


def seed_ranges(js: Optional[dict]) -> dict[str, dict[str, float]]:
    """Initialize min=max from a joint-state dict (missing keys → 0)."""
    out = empty_ranges()
    if not js:
        return out
    for k in JOINT_KEYS:
        v = js.get(k)
        if isinstance(v, (int, float)):
            fv = float(v)
            out[k] = {"min": fv, "max": fv}
    return out


def expand_ranges(ranges: dict[str, dict[str, float]], js: Optional[dict]) -> None:
    """In-place expand min/max from current joint sample."""
    if not js:
        return
    for k in JOINT_KEYS:
        v = js.get(k)
        if not isinstance(v, (int, float)):
            continue
        fv = float(v)
        slot = ranges.setdefault(k, {"min": fv, "max": fv})
        if fv < slot["min"]:
            slot["min"] = fv
        if fv > slot["max"]:
            slot["max"] = fv


def range_span(slot: Optional[dict]) -> float:
    if not isinstance(slot, dict):
        return 0.0
    try:
        return float(slot["max"]) - float(slot["min"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def is_range_valid(slot: Optional[dict], *, min_span: float = MIN_SPAN) -> bool:
    return range_span(slot) >= min_span


def calibration_ready(
    master: dict[str, dict[str, float]],
    slave: dict[str, dict[str, float]],
    *,
    min_span: float = MIN_SPAN,
    joint_keys: tuple[str, ...] = JOINT_KEYS,
) -> bool:
    """True if every arm joint (not gripper) has a usable span on both sides."""
    for k in joint_keys:
        if k == "gripper":
            # Gripper optional: map only if both sides are valid; otherwise passthrough.
            continue
        if not is_range_valid(master.get(k), min_span=min_span):
            return False
        if not is_range_valid(slave.get(k), min_span=min_span):
            return False
    return True


def map_master_to_slave(
    master_js: dict,
    master_ranges: dict[str, dict[str, float]],
    slave_ranges: dict[str, dict[str, float]],
    *,
    min_span: float = MIN_SPAN,
) -> dict:
    """Map master angles into slave command space via per-joint linear range map.

    alpha = (q_m - m_min) / (m_max - m_min)  clipped to [0, 1]
    q_s   = s_min + alpha * (s_max - s_min)

    Joints without a valid range on either side pass through unchanged.
    """
    out: dict = {}
    for k, v in master_js.items():
        if not isinstance(v, (int, float)):
            out[k] = v
            continue
        mr = master_ranges.get(k)
        sr = slave_ranges.get(k)
        if not is_range_valid(mr, min_span=min_span) or not is_range_valid(sr, min_span=min_span):
            out[k] = float(v)
            continue
        mspan = float(mr["max"]) - float(mr["min"])
        alpha = (float(v) - float(mr["min"])) / mspan
        alpha = 0.0 if alpha < 0.0 else (1.0 if alpha > 1.0 else alpha)
        out[k] = float(sr["min"]) + alpha * (float(sr["max"]) - float(sr["min"]))
    return out


def ranges_payload(
    master: dict[str, dict[str, float]],
    slave: dict[str, dict[str, float]],
    *,
    active: bool,
    saved_at: Optional[str],
    mapping_enabled: bool = False,
    joint_keys: tuple[str, ...] = JOINT_KEYS,
) -> dict:
    return {
        "active": active,
        "saved_at": saved_at,
        "mapping_enabled": mapping_enabled,
        "ready": calibration_ready(master, slave, joint_keys=joint_keys),
        "master": deepcopy(master),
        "slave": deepcopy(slave),
    }


def load_calibration(path: Path) -> tuple[dict, dict, Optional[str]]:
    if not path.is_file():
        return empty_ranges(), empty_ranges(), None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        master = _normalize_side(data.get("master"))
        slave = _normalize_side(data.get("slave"))
        saved_at = data.get("saved_at")
        if not isinstance(saved_at, str):
            saved_at = None
        return master, slave, saved_at
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to load calibration %s: %s", path, e)
        return empty_ranges(), empty_ranges(), None


def save_calibration(
    path: Path,
    master: dict[str, dict[str, float]],
    slave: dict[str, dict[str, float]],
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    saved_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "saved_at": saved_at,
        "master": deepcopy(master),
        "slave": deepcopy(slave),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("Calibration saved to %s", path)
    return saved_at


def _normalize_side(raw) -> dict[str, dict[str, float]]:
    out = empty_ranges()
    if not isinstance(raw, dict):
        return out
    for k in JOINT_KEYS:
        slot = raw.get(k)
        if not isinstance(slot, dict):
            continue
        try:
            lo = float(slot["min"])
            hi = float(slot["max"])
        except (KeyError, TypeError, ValueError):
            continue
        if hi < lo:
            lo, hi = hi, lo
        out[k] = {"min": lo, "max": hi}
    return out
