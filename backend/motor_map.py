"""Master→slave motor routing (target motor + forward/reverse)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

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

JOINT_LABELS = {
    "joint1": "J1",
    "joint2": "J2",
    "joint3": "J3",
    "joint4": "J4",
    "joint5": "J5",
    "joint6": "J6",
    "gripper": "GR",
}


def default_motor_map() -> dict[str, dict[str, Any]]:
    """Identity routing, all forward."""
    return {k: {"slave": k, "dir": 1} for k in JOINT_KEYS}


def _parse_entry(raw: Any) -> tuple[Optional[str], int]:
    """Return (slave_key|None, dir ±1) from legacy or structured entry."""
    if raw is None or raw == "" or raw == "none" or raw == "无":
        return None, 1
    if isinstance(raw, str) and raw in JOINT_KEYS:
        return raw, 1
    if isinstance(raw, dict):
        slave = raw.get("slave", raw.get("to", raw.get("motor")))
        if slave is None or slave == "" or slave == "none" or slave == "无":
            slave_key: Optional[str] = None
        elif isinstance(slave, str) and slave in JOINT_KEYS:
            slave_key = slave
        else:
            slave_key = None
        d = raw.get("dir", raw.get("direction", 1))
        if d in (-1, "-1", "rev", "reverse", "反向", False):
            direction = -1
        elif d in (1, "+1", "fwd", "forward", "正向", True):
            direction = 1
        else:
            try:
                direction = -1 if int(d) < 0 else 1
            except (TypeError, ValueError):
                direction = 1
        return slave_key, direction
    return None, 1


def normalize_motor_map(raw: Optional[dict]) -> dict[str, dict[str, Any]]:
    """Validate routing. Enforces one-to-one slave assignment; keeps per-joint dir."""
    out = default_motor_map()
    if not isinstance(raw, dict):
        return out

    proposed: dict[str, tuple[Optional[str], int]] = {}
    for mk in JOINT_KEYS:
        if mk not in raw:
            continue
        proposed[mk] = _parse_entry(raw[mk])

    used: set[str] = set()
    for mk in JOINT_KEYS:
        if mk in proposed:
            sk, direction = proposed[mk]
        else:
            sk = out[mk]["slave"]
            direction = int(out[mk]["dir"])
        if sk is None:
            out[mk] = {"slave": None, "dir": direction}
            continue
        if sk in used:
            log.warning("motor_map: slave %s already mapped — clearing master %s", sk, mk)
            out[mk] = {"slave": None, "dir": direction}
            continue
        used.add(sk)
        out[mk] = {"slave": sk, "dir": direction}
    return out


def load_motor_map(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return default_motor_map()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("map") if isinstance(data, dict) and "map" in data else data
        # Legacy separate "dir" object alongside string map
        if isinstance(data, dict) and isinstance(data.get("dir"), dict) and isinstance(raw, dict):
            merged: dict[str, Any] = {}
            for mk in JOINT_KEYS:
                entry = raw.get(mk, mk)
                sk, direction = _parse_entry(entry)
                d_extra = data["dir"].get(mk)
                if d_extra is not None:
                    _, direction = _parse_entry({"slave": sk, "dir": d_extra})
                merged[mk] = {"slave": sk, "dir": direction}
            return normalize_motor_map(merged)
        return normalize_motor_map(raw if isinstance(raw, dict) else None)
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to load motor map %s: %s", path, e)
        return default_motor_map()


def save_motor_map(path: Path, mapping: dict) -> dict[str, dict[str, Any]]:
    normalized = normalize_motor_map(mapping)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep previous file so accidental overwrites can be recovered.
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to backup motor map: %s", e)
    path.write_text(
        json.dumps({"map": normalized}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info("Motor map saved to %s", path)
    return normalized


def slave_of(entry: Any) -> Optional[str]:
    sk, _ = _parse_entry(entry)
    return sk


def dir_of(entry: Any) -> int:
    _, d = _parse_entry(entry)
    return d


def apply_motor_map(
    master_cmd: dict,
    mapping: dict,
    *,
    hold: Optional[dict] = None,
) -> dict:
    """Route master-key commands into slave-key dict for hardware send.

    Unmapped slave motors keep ``hold`` (last command / measured).
    Reverse without calibration: negate the master command angle.
    """
    mapping = normalize_motor_map(mapping)
    hold = hold or {}
    out: dict[str, float] = {}
    for sk in JOINT_KEYS:
        v = hold.get(sk)
        if isinstance(v, (int, float)):
            out[sk] = float(v)
        else:
            mv = master_cmd.get(sk)
            out[sk] = float(mv) if isinstance(mv, (int, float)) else 0.0

    used: set[str] = set()
    for mk in JOINT_KEYS:
        sk = mapping[mk]["slave"]
        direction = int(mapping[mk]["dir"])
        if sk is None or sk not in JOINT_KEYS:
            continue
        if sk in used:
            continue
        mv = master_cmd.get(mk)
        if isinstance(mv, (int, float)):
            out[sk] = float(direction) * float(mv)
            used.add(sk)
    return out


def route_range_map(
    master_js: dict,
    master_ranges: dict,
    slave_ranges: dict,
    mapping: dict,
    *,
    min_span: float = 0.05,
) -> dict:
    """Range-map each master joint into its routed slave motor's range.

    Forward: master min→max maps to slave min→max.
    Reverse: master min→max maps to slave max→min (行程取反).
    """
    from .calibration import is_range_valid  # local import to avoid cycle at load

    mapping = normalize_motor_map(mapping)
    out: dict[str, float] = {}
    for mk in JOINT_KEYS:
        sk = mapping[mk]["slave"]
        direction = int(mapping[mk]["dir"])
        if sk is None:
            continue
        v = master_js.get(mk)
        if not isinstance(v, (int, float)):
            continue
        mr = master_ranges.get(mk)
        sr = slave_ranges.get(sk)
        if not is_range_valid(mr, min_span=min_span) or not is_range_valid(sr, min_span=min_span):
            out[sk] = float(direction) * float(v)
            continue
        mspan = float(mr["max"]) - float(mr["min"])
        alpha = (float(v) - float(mr["min"])) / mspan
        alpha = max(0.0, min(1.0, alpha))
        if direction < 0:
            alpha = 1.0 - alpha
        out[sk] = float(sr["min"]) + alpha * (float(sr["max"]) - float(sr["min"]))
    return out
