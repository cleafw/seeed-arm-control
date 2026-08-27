"""StarAi Violin / reBot Arm 102 leader (FashionStar UART).

Uses ``motorbridge-smart-servo`` (same stack as Hugging Face ``rebot_102_leader``).

Public API for the existing controller:
  get_fashionstar_joint_states() -> dict[str, float]  # radians, joint1..6 + gripper
  close()
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional

from motorbridge_smart_servo import FashionStarServo, ServoMonitor

log = logging.getLogger(__name__)

# App joint keys ↔ logical motor names (order matters for B601 follower).
_JOINT_ORDER = (
    ("joint1", "shoulder_pan"),
    ("joint2", "shoulder_lift"),
    ("joint3", "elbow_flex"),
    ("joint4", "wrist_flex"),
    ("joint5", "wrist_yaw"),
    ("joint6", "wrist_roll"),
    ("gripper", "gripper"),
)

# Official reBot Arm 102 / Violin map (HF rebot_102_leader).
_IDS_0_BASED: dict[str, int] = {
    "shoulder_pan": 0,
    "shoulder_lift": 1,
    "elbow_flex": 2,
    "wrist_flex": 3,
    "wrist_yaw": 4,
    "wrist_roll": 5,
    "gripper": 6,
}

# Some units answer on IDs 1..6 only (SO100-style numbering, no id=0).
# Map 6 physical servos → B601; missing wrist_yaw is held at 0.
_IDS_1_BASED: dict[str, int] = {
    "shoulder_pan": 1,
    "shoulder_lift": 2,
    "elbow_flex": 3,
    "wrist_flex": 4,
    "wrist_roll": 5,
    "gripper": 6,
    # wrist_yaw intentionally absent → always 0.0 rad for follower
}

_JOINT_DIRECTIONS: dict[str, float] = {
    "shoulder_pan": -1.0,
    "shoulder_lift": -1.0,
    "elbow_flex": 1.0,
    "wrist_flex": 1.0,
    "wrist_yaw": 1.0,
    "wrist_roll": -1.0,
    "gripper": -6.0,
}

_JOINT_RANGES_DEG: dict[str, tuple[float, float]] = {
    "shoulder_pan": (-150.0, 150.0),
    "shoulder_lift": (-200.0, 1.0),
    "elbow_flex": (-200.0, 1.0),
    "wrist_flex": (-80.0, 90.0),
    "wrist_yaw": (-90.0, 90.0),
    "wrist_roll": (-90.0, 90.0),
    "gripper": (-270.0, 0.0),
}

_SETTLE_SEC = 0.01


def _round_to_valid_range(value: float, min_value: float, max_value: float) -> float:
    center = (min_value + max_value) / 2.0
    turns = round((value - center) / 360.0)
    return value - turns * 360.0


def _detect_id_map(bus: FashionStarServo) -> tuple[dict[str, int], list[str]]:
    """Pick 0-based (7 servo) or 1-based (6 servo) map from live pings."""
    online_0 = [n for n, sid in _IDS_0_BASED.items() if bus.ping(sid)]
    if len(online_0) >= 6:
        return dict(_IDS_0_BASED), online_0

    online_1 = [n for n, sid in _IDS_1_BASED.items() if bus.ping(sid)]
    if len(online_1) >= 5:
        log.warning(
            "Leader answers on IDs 1..6 (not 0..6); using SO100-style map, wrist_yaw=0"
        )
        return dict(_IDS_1_BASED), online_1

    # Fall back to official map even if sparse — soft connect will retry.
    return dict(_IDS_0_BASED), online_0


def probe_fashionstar_positions(port: str, *, baudrate: int = 1_000_000) -> bool:
    """Identify a Violin/Arm-102 from live angle monitors without writes."""
    bus = FashionStarServo(port, baudrate=baudrate)
    try:
        monitors = bus.sync_monitor(list(range(7)))
        return sum(1 for monitor in monitors.values() if monitor is not None) >= 5
    finally:
        bus.close()


class PiPER_MateAgilex:
    """Violin / Arm-102 leader reader (name kept for controller compatibility)."""

    def __init__(
        self,
        fashionstar_port: str = "/dev/ttyUSB0",
        piper_can_name: str = "can0",
        gripper_exist: bool = True,
        fashionstar_baud: int = 1_000_000,
    ):
        del piper_can_name
        self.gripper_exist = gripper_exist
        self.port = fashionstar_port
        self.baudrate = fashionstar_baud
        self._bus: Optional[FashionStarServo] = None
        self._last_raw_deg: dict[str, float] = {}
        self._joint_ids: dict[str, int] = dict(_IDS_0_BASED)
        self._configured = False
        self._last_online_probe = 0.0
        self._stale_reads = 0
        self._motor_names = [n for _, n in _JOINT_ORDER if gripper_exist or n != "gripper"]
        self._app_keys = [k for k, n in _JOINT_ORDER if gripper_exist or n != "gripper"]
        # Logical motors that have a physical servo id in the active map
        self._readable_names: list[str] = []

        log.info(
            "Connecting Violin/102 leader on %s @ %d (motorbridge-smart-servo)",
            fashionstar_port,
            fashionstar_baud,
        )
        bus = FashionStarServo(fashionstar_port, baudrate=fashionstar_baud)
        try:
            # Don't abort the whole bus after intermittent misses.
            try:
                bus.set_loss_threshold(0)
            except Exception:  # noqa: BLE001
                pass

            self._joint_ids, online = _detect_id_map(bus)
            self._readable_names = [
                n for n in self._motor_names if n in self._joint_ids and n in online
            ]
            missing = [
                f"{n}(id={self._joint_ids[n]})"
                for n in self._motor_names
                if n in self._joint_ids and n not in online
            ]
            held = [n for n in self._motor_names if n not in self._joint_ids]
            if missing:
                log.error(
                    "Leader servos not responding: %s. Keeping UART open; soft-retry reads.",
                    ", ".join(missing),
                )
            if held:
                log.info("Leader logical joints held at 0 (no servo): %s", ", ".join(held))

            for name in online:
                self._last_raw_deg[name] = 0.0
            self._configure_online(bus, online)
            self._bus = bus
            self._configured = bool(online)
        except Exception:
            try:
                bus.close()
            except Exception:  # noqa: BLE001
                pass
            raise

        if self._configured:
            log.info(
                "Violin/102 leader ready on %s (map_ids=%s online=%d/%d)",
                fashionstar_port,
                sorted(self._joint_ids.values()),
                len(self._readable_names),
                len(self._motor_names),
            )
        else:
            log.warning(
                "Violin/102 UART open on %s but 0 servos online — power the arm",
                fashionstar_port,
            )

    def _configure_online(self, bus: FashionStarServo, names: list[str]) -> None:
        for name in names:
            sid = self._joint_ids[name]
            bus.unlock(sid)
            time.sleep(_SETTLE_SEC)
        for name in names:
            bus.reset_multi_turn(self._joint_ids[name])

    def _ensure_online(self) -> bool:
        if self._bus is None:
            return False
        if self._configured and self._readable_names:
            return True
        now = time.monotonic()
        if now - self._last_online_probe < 2.0:
            return False
        self._last_online_probe = now
        self._joint_ids, online = _detect_id_map(self._bus)
        if not online:
            return False
        try:
            self._configure_online(self._bus, online)
            self._readable_names = [
                n for n in self._motor_names if n in self._joint_ids and n in online
            ]
            for name in online:
                self._last_raw_deg.setdefault(name, 0.0)
            self._configured = True
            log.info(
                "Leader servos came online: %s (ids=%s)",
                ", ".join(online),
                sorted(self._joint_ids[n] for n in online),
            )
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("Leader late configure failed: %s", e)
            return False

    def _read_raw_deg(self) -> dict[str, float]:
        """Per-joint soft read: never drop the whole frame for one missing servo."""
        assert self._bus is not None
        names = list(self._readable_names) or [
            n for n in self._motor_names if n in self._joint_ids
        ]
        ids = [self._joint_ids[n] for n in names]
        if not ids:
            return dict(self._last_raw_deg)

        try:
            result: dict[int, ServoMonitor | None] = self._bus.sync_monitor(ids)
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            # USB/serial I/O loss must bubble so the controller can drop + reconnect.
            if (
                isinstance(e, (OSError, RuntimeError))
                or "serial" in msg
                or "i/o" in msg
                or "input/output" in msg
            ):
                log.error("Leader sync_monitor failed (link): %s", e)
                raise
            # Soft miss: keep last good for all.
            log.error("Leader sync_monitor failed: %s — holding last angles", e)
            return dict(self._last_raw_deg)

        id_to_name = {self._joint_ids[n]: n for n in names}
        raw = dict(self._last_raw_deg)
        fresh = 0
        for sid, monitor in result.items():
            name = id_to_name.get(sid)
            if name is None:
                continue
            if monitor is None:
                continue
            raw[name] = float(monitor.angle_deg)
            fresh += 1
        if fresh == 0 and not self._last_raw_deg:
            raise RuntimeError("No leader servo has ever responded")
        if fresh == 0:
            self._stale_reads += 1
            log.warning(
                "Leader sync_monitor returned no fresh angles (%d)",
                self._stale_reads,
            )
            if self._stale_reads >= 5:
                raise RuntimeError(
                    "Leader no fresh feedback (USB unplugged or arm power off)"
                )
            return dict(self._last_raw_deg)
        self._stale_reads = 0
        return raw

    def get_fashionstar_joint_states(self) -> dict:
        """Return joint1..6 (+ gripper) in radians for the follower."""
        if self._bus is None:
            return {}
        if not self._ensure_online():
            return {}
        try:
            raw = self._read_raw_deg()
            self._last_raw_deg = raw
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if (
                isinstance(e, (OSError, RuntimeError))
                or "serial" in msg
                or "i/o" in msg
                or "input/output" in msg
                or "no fresh" in msg
            ):
                # Must bubble so controller marks 主臂异常 — do NOT hold last pose forever.
                log.error("Leader read failed (link): %s", e)
                raise
            log.error("Leader read failed: %s — using last good angles", e)
            raw = self._last_raw_deg
            if not raw:
                return {}

        out: dict[str, float] = {}
        for app_key, motor_name in zip(self._app_keys, self._motor_names):
            if motor_name not in self._joint_ids:
                # e.g. wrist_yaw on 6-servo leader
                out[app_key] = 0.0
                continue
            if motor_name not in raw:
                out[app_key] = 0.0
                continue
            rmin, rmax = _JOINT_RANGES_DEG[motor_name]
            direction = _JOINT_DIRECTIONS[motor_name]
            sign = 1.0 if direction >= 0 else -1.0
            unwrapped = _round_to_valid_range(raw[motor_name], rmin * sign, rmax * sign)
            position_deg = max(rmin, min(rmax, unwrapped * direction))
            out[app_key] = math.radians(position_deg)
        return out

    def check_link(self) -> None:
        """Raise if USB node is gone or leader servos stop answering."""
        import os

        # Windows COM ports are not filesystem paths; the subsequent ping is
        # the real liveness test. Linux/macOS still reject a vanished device.
        if not self.port or (os.name != "nt" and not os.path.exists(self.port)):
            raise OSError(f"leader port missing: {self.port}")
        if self._bus is None:
            raise OSError("leader bus closed")
        # Prefer a live ping on any mapped servo.
        probe_ids = [
            self._joint_ids[n]
            for n in (self._readable_names or list(self._joint_ids))
            if n in self._joint_ids
        ][:3]
        if not probe_ids:
            probe_ids = [0, 1]
        online = 0
        for sid in probe_ids:
            try:
                if self._bus.ping(sid):
                    online += 1
            except Exception as e:  # noqa: BLE001
                raise OSError(f"leader ping failed: {e}") from e
        if online == 0:
            raise RuntimeError(
                "leader servos not responding (USB unplugged or arm power off)"
            )

    def close(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.close()
        except Exception as e:  # noqa: BLE001
            log.warning("Leader close failed: %s", e)
        self._bus = None
        log.info("Violin/102 leader closed")
