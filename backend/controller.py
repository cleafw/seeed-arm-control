"""Headless teleop / record / playback controller.

Modes:
    idle                no teleop; wait for calibration (follower not commanded)
    follow              master → slave teleop (default after valid calibration)
    record              follow + sampling joint_states into the active recording
    transition          smooth blend from current slave pose → action[0]
    playback            playing back an action (loop or once)
    return_to_follow    after a "once" playback: slow blend → live master pose
    calibrate           free-move both arms; collect min/max for range mapping
    free_move           stop teleop; unlock motors (MIT zero / disable, no damping)
    paused              emergency stop; slave holds last pose

The control loop is a regular Python thread running at UPDATE_RATE Hz. The
FastAPI layer calls Controller.start_*/stop_* methods (RLock-protected) and
reads thread-safe state via Controller.snapshot().
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from copy import deepcopy
from typing import Optional

import serial
import serial.tools.list_ports

from .calibration import (
    calibration_ready,
    expand_ranges,
    is_range_valid,
    load_calibration,
    ranges_payload,
    save_calibration,
    seed_ranges,
)
from .config import Config
from .models import Action, ControllerMode, PlayMode
from .motor_map import (
    JOINT_KEYS as MOTOR_MAP_KEYS,
    apply_motor_map,
    default_motor_map,
    load_motor_map,
    route_range_map,
    save_motor_map,
)
from .pipermate import PiPER_MateAgilex, probe_fashionstar_positions
from .so101 import SO101Arm
from .profiles import (
    detect_arm_profiles,
    get_profile,
    load_active_profiles,
    load_active_ports,
    pair_id as make_pair_id,
    save_active_profiles,
    save_active_ports,
)
from .profiles.registry import ProfileError
from .storage import ActionLibrary
from .u2can.DM_CAN import (
    Control_Type,
    DM_Motor_Type,
    Motor,
    MotorControl,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Port detection (daemon-friendly: no input(), env-var override)
# ---------------------------------------------------------------------------

def detect_ports(
    preferred_master: Optional[str] = None,
    preferred_slave: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Auto-detect master + slave serial ports.

    Master (Violin / Arm 102): CH340 FashionStar UART (VID=0x1a86, PID=0x7523).
    Slave (B601-DM): HDSC CDC Damiao serial bridge, or CH343.

    Multiple candidates → log warning, pick first. None → return None.
    Caller passes env-var overrides as `preferred_master`/`preferred_slave`.
    """
    ports = list(serial.tools.list_ports.comports())

    def fmt(p):
        vid = f"{p.vid:04x}" if p.vid else "----"
        pid = f"{p.pid:04x}" if p.pid else "----"
        return (f"{p.device:<18} {vid}:{pid}  mfr={p.manufacturer!r}  "
                f"product={p.product!r}")

    log.info("Enumerated %d serial port(s):", len(ports))
    for p in ports:
        log.info("  %s", fmt(p))

    master_candidates = [p for p in ports if p.vid == 0x1a86 and p.pid == 0x7523]
    slave_candidates = [
        p for p in ports
        if (p.manufacturer or "").upper() == "HDSC"
        or (p.product or "").upper().startswith("CDC")
        or (p.vid == 0x1A86 and p.pid == 0x55D3)
    ]

    def pick(label: str, cands, preferred: Optional[str]) -> Optional[str]:
        if preferred:
            if any(c.device == preferred for c in cands):
                log.info("%s: using configured %s", label, preferred)
                return preferred
            log.warning("%s: configured port %s not in candidates; falling back to auto-detect",
                        label, preferred)
        if not cands:
            log.error("%s: no candidates found", label)
            return None
        if len(cands) > 1:
            log.warning("%s: %d candidates, picking first (%s)",
                        label, len(cands), cands[0].device)
        else:
            log.info("%s: auto-selected %s (%s)", label, cands[0].device, cands[0].product)
        return cands[0].device

    return (
        pick("master (Violin / CH340)", master_candidates, preferred_master),
        pick("slave (B601-DM / HDSC CDC / CH343)", slave_candidates, preferred_slave),
    )


def detect_slave_port_candidates(preferred: Optional[str] = None) -> list[str]:
    """Return every plausible B601 serial port, ordered for live probing.

    USB adapters are often identical on both arms. Their VID/PID only gives us
    candidates; the caller must read motor feedback to decide whether a port
    is actually a reachable follower.
    """
    if preferred:
        return [preferred]
    ports = list(serial.tools.list_ports.comports())
    candidates = [
        p.device
        for p in ports
        if (p.manufacturer or "").upper() == "HDSC"
        or (p.product or "").upper().startswith("CDC")
        or (p.vid == 0x1A86 and p.pid == 0x55D3)
    ]
    return candidates


def detect_so101_ports(
    preferred_leader: Optional[str] = None,
    preferred_follower: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Return the two CH343 Feetech buses in a stable order.

    Both Seeed controller boards have identical USB identities, so role cannot
    be inferred from USB metadata.  Explicit ports win; otherwise natural COM
    ordering provides a predictable default that users can override.
    """
    ports = sorted(
        (
            p.device
            for p in serial.tools.list_ports.comports()
            if p.vid == 0x1A86 and p.pid == 0x55D3
        ),
        key=lambda value: int("".join(ch for ch in value if ch.isdigit()) or 0),
    )
    # Preserve a saved assignment only while that COM port still exists.
    # Windows may allocate a different COM number after a USB replug; in that
    # case fall back to the currently enumerated buses instead of retrying a
    # dead historical port forever.
    leader = preferred_leader if preferred_leader in ports else None
    follower = (
        preferred_follower
        if preferred_follower in ports and preferred_follower != leader
        else None
    )
    remaining = [p for p in ports if p not in (leader, follower)]
    if leader is None and remaining:
        leader = remaining.pop(0)
    if follower is None and remaining:
        follower = remaining.pop(0)
    return leader, follower


# ---------------------------------------------------------------------------
# Slave arm: B601-DM via project DM_CAN (u2can). LeRobot uses motorbridge;
# ensure_mode is stricter and fails hard when 24V/CAN is down — keep u2can.
# ---------------------------------------------------------------------------

class SlaveArm:
    def __init__(self, port: str, baudrate: int = 921600, name: str = "slave"):
        self.port = port
        self.baudrate = baudrate
        self.name = name
        self.serial_device: Optional[serial.Serial] = None
        self.motor_control: Optional[MotorControl] = None
        self.motors: list[Motor] = []

    @staticmethod
    def probe_positions(port: str, baudrate: int = 921600) -> bool:
        """Read-only B601/DM-CAN identification from motor status feedback."""
        device = serial.Serial(port, baudrate, timeout=0.15)
        try:
            control = MotorControl(device)
            probes = (
                Motor(DM_Motor_Type.DM4340, 0x01, 0x11),
                Motor(DM_Motor_Type.DM4340, 0x02, 0x12),
                Motor(DM_Motor_Type.DM4340, 0x03, 0x13),
            )
            for motor in probes:
                control.addMotor(motor)
            replies = 0
            for motor in probes:
                before = getattr(motor, "_rx_gen", 0)
                control.refresh_motor_status(motor)
                time.sleep(0.02)
                try:
                    control.recv()
                except Exception:  # noqa: BLE001
                    pass
                if getattr(motor, "_rx_gen", 0) > before:
                    replies += 1
            return replies > 0
        finally:
            device.close()

    def setup(self) -> None:
        self.serial_device = serial.Serial(self.port, self.baudrate, timeout=0.5)

        Motor1 = Motor(DM_Motor_Type.DM4340, 0x01, 0x11)
        Motor2 = Motor(DM_Motor_Type.DM4340, 0x02, 0x12)
        Motor3 = Motor(DM_Motor_Type.DM4340, 0x03, 0x13)
        Motor4 = Motor(DM_Motor_Type.DM4310, 0x04, 0x14)
        Motor5 = Motor(DM_Motor_Type.DM4310, 0x05, 0x15)
        Motor6 = Motor(DM_Motor_Type.DM4310, 0x06, 0x16)
        Motor7 = Motor(DM_Motor_Type.DM4310, 0x07, 0x17)

        self.motor_control = MotorControl(self.serial_device)
        for m in (Motor1, Motor2, Motor3, Motor4, Motor5, Motor6, Motor7):
            self.motor_control.addMotor(m)
        self.motors = [Motor1, Motor2, Motor3, Motor4, Motor5, Motor6, Motor7]

        for motor in self.motors:
            self.motor_control.disable(motor)
            if motor is not Motor7:
                self.motor_control.switchControlMode(motor, Control_Type.POS_VEL)
            else:
                self.motor_control.switchControlMode(motor, Control_Type.Torque_Pos)
            self.motor_control.enable(motor)
            time.sleep(0.001)

        log.info("[%s] initialized on %s (u2can/DM_CAN)", self.name, self.port)

    def send_joint_states(self, js: dict) -> None:
        self.motor_control.control_Pos_Vel(self.motors[0], js["joint1"], 15)
        time.sleep(0.0005)
        self.motor_control.control_Pos_Vel(self.motors[1], js["joint2"], 15)
        time.sleep(0.0005)
        self.motor_control.control_Pos_Vel(self.motors[2], js["joint3"], 15)
        time.sleep(0.0005)
        self.motor_control.control_Pos_Vel(self.motors[3], js["joint4"], 15)
        time.sleep(0.0005)
        self.motor_control.control_Pos_Vel(self.motors[4], js["joint5"], 15)
        time.sleep(0.0005)
        self.motor_control.control_Pos_Vel(self.motors[5], js["joint6"], 15)
        time.sleep(0.0005)
        self.motor_control.control_pos_force(self.motors[6], js["gripper"], 2000, 350)

    def recover(self) -> None:
        """Re-handshake all motors after a single CAN/power line was reconnected."""
        if not self.motor_control or not self.motors:
            return
        log.warning("[%s] recover: re-enabling all motors", self.name)
        for motor in self.motors:
            try:
                self.motor_control.disable(motor)
                time.sleep(0.005)
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] disable id=%d failed: %s", self.name, motor.SlaveID, e)
        for motor in self.motors:
            try:
                mode = Control_Type.Torque_Pos if motor is self.motors[6] else Control_Type.POS_VEL
                self.motor_control.switchControlMode(motor, mode)
                self.motor_control.enable(motor)
                time.sleep(0.005)
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] re-enable id=%d failed: %s", self.name, motor.SlaveID, e)
        for motor in self.motors:
            try:
                self.motor_control.refresh_motor_status(motor)
                time.sleep(0.002)
            except Exception as e:  # noqa: BLE001
                log.debug("[%s] refresh id=%d failed: %s", self.name, motor.SlaveID, e)
        log.info("[%s] recover done", self.name)

    def get_measured_joint_states(self) -> dict:
        if not self.motors:
            return {}
        out: dict = {}
        for i in range(min(6, len(self.motors))):
            out[f"joint{i+1}"] = float(getattr(self.motors[i], "state_q", 0.0))
        if len(self.motors) > 6:
            out["gripper"] = float(getattr(self.motors[6], "state_q", 0.0))
        return out

    def poll_measured_joint_states(self) -> dict:
        """Refresh encoder feedback then return measured joints (for paused UI)."""
        if not self.motor_control or not self.motors:
            return {}
        for motor in self.motors:
            try:
                self.motor_control.refresh_motor_status(motor)
            except Exception:  # noqa: BLE001
                pass
        return self.get_measured_joint_states()

    def disable_all(self) -> None:
        """Legacy: raw disable. Prefer enter_free_move() for hand-guiding."""
        if not self.motor_control or not self.motors:
            return
        for motor in self.motors:
            try:
                self.motor_control.disable(motor)
                try:
                    self.motor_control.recv()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] disable id=%d failed: %s", self.name, motor.SlaveID, e)
            time.sleep(0.005)
        log.info("[%s] all motors disabled", self.name)

    def enter_free_move(self) -> None:
        """MIT zero-torque (or stay disabled) so the arm can be hand-moved.

        J1 (motors[0], DM4340 base) stays DISABLED — MIT zero-torque still feels
        braked on this joint for some units. Other joints use MIT kp=kd=τ=0.
        Never re-enable a motor still stuck in POS_VEL.
        """
        if not self.motor_control or not self.motors:
            return
        self._mit_free_motors: list = []
        self._disable_only_motors: list = []
        for idx, motor in enumerate(self.motors):
            try:
                self.motor_control.disable(motor)
                time.sleep(0.02)
                try:
                    self.motor_control.recv()
                except Exception:  # noqa: BLE001
                    pass

                # J1: force disable-only freewheel (no MIT enable).
                if idx == 0:
                    self.motor_control.disable(motor)
                    self._disable_only_motors.append(motor)
                    log.info(
                        "[%s] J1 id=%d free-move = DISABLED (no MIT)",
                        self.name,
                        motor.SlaveID,
                    )
                    continue

                ok = False
                for _ in range(4):
                    if self.motor_control.switchControlMode(motor, Control_Type.MIT):
                        ok = True
                        break
                    self.motor_control.disable(motor)
                    time.sleep(0.05)

                if ok:
                    self.motor_control.enable(motor)
                    time.sleep(0.01)
                    self._mit_free_motors.append(motor)
                else:
                    self.motor_control.disable(motor)
                    self._disable_only_motors.append(motor)
                    log.warning(
                        "[%s] MIT switch failed id=%d — left DISABLED for freewheel",
                        self.name,
                        motor.SlaveID,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] enter_free_move id=%d failed: %s", self.name, motor.SlaveID, e)
        self._free_move = True
        self.hold_free_move()
        log.info(
            "[%s] free-move ON (MIT ids=%s, disabled=%s)",
            self.name,
            [m.SlaveID for m in self._mit_free_motors],
            [m.SlaveID for m in self._disable_only_motors],
        )

    def hold_free_move(self) -> dict:
        """Zero-torque MIT for soft joints; re-assert disable for J1 / failed ones."""
        if not self.motor_control or not self.motors:
            return {}
        mit_set = set(getattr(self, "_mit_free_motors", []) or [])
        dis_set = set(getattr(self, "_disable_only_motors", []) or [])
        for motor in self.motors:
            try:
                if motor in mit_set:
                    self.motor_control.controlMIT(motor, 0.0, 0.0, 0.0, 0.0, 0.0)
                else:
                    if motor in dis_set:
                        now = time.monotonic()
                        # Re-assert disable ~2Hz — every-tick disable floods CAN/USB.
                        if now - float(getattr(self, "_j1_disable_ts", 0.0) or 0.0) >= 0.5:
                            self._j1_disable_ts = now
                            self.motor_control.disable(motor)
                    self.motor_control.refresh_motor_status(motor)
                err = int(getattr(motor, "state_err", 0) or 0)
                if err and self.motors and motor is self.motors[0]:
                    now = time.monotonic()
                    last = float(getattr(self, "_j1_fault_log_ts", 0.0) or 0.0)
                    if now - last > 2.0:
                        self._j1_fault_log_ts = now
                        log.warning(
                            "[%s] J1 fault nibble=0x%X — may need power cycle if still stiff",
                            self.name,
                            err,
                        )
            except Exception as e:  # noqa: BLE001
                log.debug("[%s] free-move id=%d: %s", self.name, motor.SlaveID, e)
            time.sleep(0.0004)
        return self.get_measured_joint_states()

    def enable_all(self) -> None:
        """Leave free-move / re-enable POS_VEL (gripper Torque_Pos) for teleop."""
        if not self.motor_control or not self.motors:
            return
        self._free_move = False
        self._mit_free_motors = []
        self._disable_only_motors = []
        for idx, motor in enumerate(self.motors):
            try:
                self.motor_control.disable(motor)
                time.sleep(0.02)
                try:
                    self.motor_control.recv()
                except Exception:  # noqa: BLE001
                    pass
                mode = Control_Type.Torque_Pos if motor is self.motors[6] else Control_Type.POS_VEL
                ok = False
                # J1 (DM4340) is stubborn after long DISABLED free-move — more retries.
                attempts = 6 if idx == 0 else 3
                for _ in range(attempts):
                    if self.motor_control.switchControlMode(motor, mode):
                        ok = True
                        break
                    time.sleep(0.05)
                if not ok:
                    log.warning(
                        "[%s] switchControlMode failed id=%d (want %s) — enabling anyway",
                        self.name,
                        motor.SlaveID,
                        mode,
                    )
                self.motor_control.enable(motor)
                time.sleep(0.02)
                try:
                    self.motor_control.refresh_motor_status(motor)
                except Exception:  # noqa: BLE001
                    pass
                if idx == 0:
                    err = int(getattr(motor, "state_err", 0) or 0)
                    log.info(
                        "[%s] J1 re-enabled (mode_ok=%s, err=0x%X, q=%.3f)",
                        self.name,
                        ok,
                        err,
                        float(getattr(motor, "state_q", 0.0) or 0.0),
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] enable id=%d failed: %s", self.name, motor.SlaveID, e)
            time.sleep(0.01)
        log.info("[%s] all motors enabled (POS_VEL)", self.name)

    def safe_shutdown(self, duration: float = 2.0, steps: int = 20) -> None:
        if not self.motor_control or not self.motors:
            return
        try:
            log.info("[%s] safe shutdown: slow zero → disable", self.name)
            current = []
            for motor in self.motors:
                pos = 0.0
                try:
                    if hasattr(motor, "state") and hasattr(motor.state, "pos"):
                        pos = float(motor.state.pos)
                    elif hasattr(motor, "pos"):
                        pos = float(motor.pos)
                except Exception:  # noqa: BLE001
                    pos = 0.0
                current.append(pos)

            target = [0.0] * len(self.motors)
            dt = duration / steps if steps > 0 else 0.02

            for step in range(1, steps + 1):
                a = step / steps
                a = a * a * (3 - 2 * a)
                for i in range(6):
                    pos = current[i] + a * (target[i] - current[i])
                    self.motor_control.control_Pos_Vel(self.motors[i], pos, 0.8)
                    time.sleep(0.002)
                grip = current[6] + a * (target[6] - current[6])
                self.motor_control.control_pos_force(self.motors[6], grip, 1000, 200)
                time.sleep(dt)

            for motor in self.motors:
                try:
                    self.motor_control.disable(motor)
                    time.sleep(0.002)
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] disable failed: %s", self.name, e)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] safe_shutdown error: %s", self.name, e)

    def close(self) -> None:
        try:
            if self.serial_device is not None and self.serial_device.is_open:
                self.serial_device.close()
                log.info("[%s] serial closed", self.name)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] close failed: %s", self.name, e)

    def check_link(self) -> None:
        """Raise if USB node is gone, serial closed, or motors stop answering."""
        # COM ports do not exist as regular paths on Windows. Opening the
        # serial device and receiving CAN position feedback below is the
        # authoritative liveness check there.
        if not self.port or (os.name != "nt" and not os.path.exists(self.port)):
            raise OSError(f"[{self.name}] port missing: {self.port}")
        if self.serial_device is None or not self.serial_device.is_open:
            raise OSError(f"[{self.name}] serial closed")
        if not self.motor_control or not self.motors:
            raise OSError(f"[{self.name}] motors not initialized")
        try:
            _ = self.serial_device.in_waiting
        except Exception as e:  # noqa: BLE001
            raise OSError(f"[{self.name}] serial dead: {e}") from e

        # Probe up to 3 joints — need at least one CAN status frame back.
        for motor in self.motors[:3]:
            before = getattr(motor, "_rx_gen", 0)
            try:
                self.motor_control.refresh_motor_status(motor)
                time.sleep(0.02)
                try:
                    self.motor_control.recv()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                raise OSError(f"[{self.name}] refresh failed: {e}") from e
            if getattr(motor, "_rx_gen", 0) > before:
                return
        raise RuntimeError(
            f"[{self.name}] no motor feedback (USB unplugged or arm power off)"
        )


    def close(self) -> None:
        return

    def check_link(self) -> None:
        return


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class ControllerError(Exception):
    """Raised when a command is invalid in the current mode."""


class Controller:
    def __init__(self, cfg: Config, library: ActionLibrary):
        self.cfg = cfg
        self.library = library
        # Persisted UI port pairing wins over env (MASTER_PORT/SLAVE_PORT are
        # first-boot defaults only when no active_ports.json exists yet).
        saved_ports = load_active_ports(cfg.recordings_dir)
        if saved_ports:
            cfg.master_port, cfg.slave_port = saved_ports
            log.info(
                "Loaded persisted ports: leader=%s follower=%s",
                saved_ports[0],
                saved_ports[1],
            )

        # ---- runtime state (all guarded by self.lock) ----
        self.lock = threading.RLock()
        self.running = True
        self.safety_enabled: bool = cfg.safety_default_enabled
        self._recovering: bool = False

        # Recording
        self.record_buffer: list[dict] = []
        self.record_action_name: Optional[str] = None
        self.record_start_time: Optional[float] = None
        self.last_recorded_time: Optional[float] = None
        self.last_recorded_joint_states: Optional[dict] = None

        # Playback
        self.current_action: Optional[Action] = None     # Action being played
        self.current_play_mode: Optional[PlayMode] = None
        self.play_start_time: Optional[float] = None
        self.play_index: int = 0

        # Transition (current_output → action[0])
        self.transition_start_time: Optional[float] = None
        self.transition_from_js: Optional[dict] = None
        self.transition_to_js: Optional[dict] = None
        self.transition_target_action: Optional[Action] = None
        self.transition_target_mode: Optional[PlayMode] = None

        # Return-to-follow (current_output → live master) — used after "once" playback
        self.return_start_time: Optional[float] = None
        self.return_from_js: Optional[dict] = None

        # Master/slave shared state
        self.last_joint_states: Optional[dict] = None        # last master read
        self.last_output_joint_states: Optional[dict] = None # last slave write (mapped)
        self.last_safety_joint_states: Optional[dict] = None  # last master-space safety baseline
        self._safety_grace_until: float = 0.0
        self.last_measured_joint_states: Optional[dict] = None  # slave encoder
        self.frame_count: int = 0
        self.last_error: Optional[str] = None

        # Per-arm USB/serial link status (surfaced in UI status window)
        self._arm_status: dict[str, dict] = {
            "master": {
                "id": "master",
                "label": "主臂",
                "status": "initializing",
                "detail": "",
                "port": None,
            },
            "slave": {
                "id": "slave",
                "label": "从臂",
                "status": "initializing",
                "detail": "",
                "port": None,
            },
        }
        self._arms_hint: Optional[str] = None
        self._last_reconnect_attempt: float = 0.0
        self._reconnect_interval_s: float = 2.0
        # The control thread and the HTTP "自动检测" action can both ask for a
        # reconnect.  A CH343 handle must never be closed/opened by two
        # threads at once; Windows will otherwise intermittently reject the
        # second open even though the COM port is present.
        self._hardware_reconnect_lock = threading.RLock()
        self._arms_were_ready: bool = False
        self._ever_had_link_fault: bool = False
        self._probe_fail_counts: dict[str, int] = {"master": 0, "slave": 0}
        self._probe_fail_limit: int = 2

        # Active arm profiles (phase 1.2/1.3; UI select + persist)
        self.leader_profile_id, self.follower_profile_id = self._boot_profile_ids(cfg)
        self.pair_id = make_pair_id(self.leader_profile_id, self.follower_profile_id)
        self._profile_detect: dict = {
            "status": "pending",
            "message": "尚未检测",
            "leader_id": None,
            "follower_id": None,
            "leader_port": None,
            "follower_port": None,
            "leader_candidates": [],
            "follower_candidates": [],
            "applied": False,
        }
        log.info(
            "Active pairing: leader=%s follower=%s pair_id=%s",
            self.leader_profile_id,
            self.follower_profile_id,
            self.pair_id,
        )

        # Calibration ranges (live session + last saved)
        cal_path = self.cfg.recordings_dir / "calibration.json"
        self._calibration_path = cal_path
        m_r, s_r, saved_at = load_calibration(cal_path)
        self.cal_master_ranges = m_r
        self.cal_slave_ranges = s_r
        self.cal_saved_at: Optional[str] = saved_at
        self._calibrating = False
        self._j1_disable_ts = 0.0
        self._j1_fault_log_ts = 0.0
        # Master→slave motor routing (identity by default)
        map_path = self.cfg.recordings_dir / "motor_map.json"
        self._motor_map_path = map_path
        self.motor_map = load_motor_map(map_path)
        # Soft blend after motor_map change (slave-space from → live mapped target)
        self._mmap_blend_start: Optional[float] = None
        self._mmap_blend_from: Optional[dict] = None
        # Keep stable wrap-branch anchors for mapping (prevents 0/2π jumps).
        self._mapping_anchor: dict[str, float] = {}
        # A saved calibration defines the mapping, but it is never consent to
        # move a real arm at application startup.  Opening a service while the
        # two arms are in different poses must leave the follower torque off
        # until the operator explicitly presses "解除锁定" / "开始跟随".
        self.mode: ControllerMode = "idle"

        # Hardware (set in setup_hardware)
        self.master: Optional[PiPER_MateAgilex | SO101Arm] = None
        self.slaves: list[SlaveArm | SO101Arm] = []

        # Snapshot listeners (e.g., WS broadcaster)
        self._listeners: list = []
        # Optional VoiceService.status_for_snapshot provider
        self._voice_status_provider = None
        # Optional VoiceService.on_master_js for follow_first preempt
        self._voice_master_js_hook = None

    def set_voice_status_provider(self, fn) -> None:
        self._voice_status_provider = fn

    def set_voice_master_js_hook(self, fn) -> None:
        self._voice_master_js_hook = fn

    def _notify_voice_master_js(self, js: dict | None) -> None:
        if not js or self._voice_master_js_hook is None:
            return
        try:
            self._voice_master_js_hook(js)
        except Exception as e:  # noqa: BLE001
            log.debug("voice master_js hook failed: %s", e)

    # ------------------------------------------------------------------
    # Arm profiles / pairing (phase 1.2 / 1.3)
    # ------------------------------------------------------------------
    def _boot_profile_ids(self, cfg: Config) -> tuple[str, str]:
        """Prefer saved active_profiles.json, then env, then legacy defaults."""
        saved = load_active_profiles(cfg.recordings_dir)
        if saved is not None:
            try:
                resolved = self._resolve_profile_ids(saved[0], saved[1])
                log.info(
                    "Loaded persisted profiles: leader=%s follower=%s",
                    resolved[0],
                    resolved[1],
                )
                return resolved
            except Exception as e:  # noqa: BLE001
                log.warning("Ignoring saved active_profiles.json: %s", e)
        return self._resolve_profile_ids(cfg.leader_profile, cfg.follower_profile)

    @staticmethod
    def _resolve_profile_ids(leader_id: str, follower_id: str) -> tuple[str, str]:
        """Validate profile ids; fall back to the original generic pair."""
        default_leader, default_follower = "violin_102", "b601_dm"
        try:
            leader = get_profile(leader_id)
            if leader.role != "leader":
                raise ProfileError(f"{leader_id} is not a leader")
        except ProfileError as e:
            log.warning("Invalid LEADER_PROFILE=%r (%s); using %s", leader_id, e, default_leader)
            leader_id = default_leader
            get_profile(leader_id)
        try:
            follower = get_profile(follower_id)
            if follower.role != "follower":
                raise ProfileError(f"{follower_id} is not a follower")
        except ProfileError as e:
            log.warning(
                "Invalid FOLLOWER_PROFILE=%r (%s); using %s",
                follower_id,
                e,
                default_follower,
            )
            follower_id = default_follower
            get_profile(follower_id)
        return leader_id, follower_id

    def profile_snapshot_fields(self) -> dict:
        return {
            "leader_profile": self.leader_profile_id,
            "follower_profile": self.follower_profile_id,
            "pair_id": self.pair_id,
            "profile_detect": dict(self._profile_detect),
        }

    def _active_joint_keys(self) -> tuple[str, ...]:
        if (self.leader_profile_id, self.follower_profile_id) == ("so101_leader", "so101_follower"):
            return ("joint1", "joint2", "joint3", "joint4", "joint5", "gripper")
        return MOTOR_MAP_KEYS

    def _calibration_ready(self) -> bool:
        return calibration_ready(
            self.cal_master_ranges,
            self.cal_slave_ranges,
            joint_keys=self._active_joint_keys(),
        )

    @staticmethod
    def _unwrap_to_reference(value: float, reference: float) -> float:
        """Return `value` shifted by k×2π to be closest to reference."""
        return value + math.tau * round((reference - value) / math.tau)

    @staticmethod
    def _angular_delta(a: float, b: float) -> float:
        """Signed shortest delta on a circle (radians)."""
        return (b - a + math.pi) % math.tau - math.pi

    @staticmethod
    def _is_circular_joint(joint: str) -> bool:
        """Gripper is treated as linear; all other joints use circular shortest-path math."""
        return joint != "gripper"

    @staticmethod
    def _circular_lerp(a: float, b: float, alpha: float) -> float:
        return a + alpha * Controller._angular_delta(a, b)

    def _alignment_reference_for_joint(self, joint: str) -> Optional[float]:
        prev = self._mapping_anchor.get(joint)
        if prev is not None:
            return prev
        if not self._calibration_ready():
            return None
        slot = self.cal_master_ranges.get(joint)
        if not is_range_valid(slot):
            return None
        return (float(slot["min"]) + float(slot["max"])) / 2.0

    def _align_master_for_mapping(self, master_js: dict) -> dict:
        aligned = dict(master_js)
        if not self._calibration_ready():
            return aligned
        for key in self._active_joint_keys():
            slot = self.cal_master_ranges.get(key)
            if not is_range_valid(slot):
                self._mapping_anchor.pop(key, None)
                continue
            value = master_js.get(key)
            if not isinstance(value, (int, float)):
                self._mapping_anchor.pop(key, None)
                continue
            reference = self._alignment_reference_for_joint(key)
            if reference is None:
                continue
            unwrapped = self._unwrap_to_reference(float(value), float(reference))
            aligned[key] = unwrapped
            self._mapping_anchor[key] = unwrapped
        return aligned

    def _reset_mapping_anchor(self, master_js: Optional[dict] = None) -> None:
        self._mapping_anchor = {}
        if not self._calibration_ready():
            return
        if not isinstance(master_js, dict):
            return
        for key in self._active_joint_keys():
            slot = self.cal_master_ranges.get(key)
            if not is_range_valid(slot):
                continue
            value = master_js.get(key)
            if not isinstance(value, (int, float)):
                continue
            mid = (float(slot["min"]) + float(slot["max"])) / 2.0
            self._mapping_anchor[key] = self._unwrap_to_reference(float(value), mid)

    def _discover_live_so101_ports(self) -> list[str]:
        """Read-probe every USB serial arm candidate for a complete SO-ARM101.

        This discovery is deliberately independent from the selected profiles.
        It never enables torque or sends a position command: a candidate is
        reported only after all six Feetech Present_Position registers reply.
        Existing live SO-ARM101 instances are reused so their COM handles are
        not opened twice.
        """
        live: list[str] = []
        active: dict[str, SO101Arm] = {}
        if isinstance(self.master, SO101Arm):
            active[self.master.port] = self.master
        for slave in self.slaves:
            if isinstance(slave, SO101Arm):
                active[slave.port] = slave

        for port_info in serial.tools.list_ports.comports():
            # Only USB serial devices are arm candidates.  This avoids opening
            # unrelated built-in / Bluetooth COM ports during discovery.
            if port_info.vid is None and port_info.pid is None:
                continue
            port = port_info.device
            arm = active.get(port)
            temporary = arm is None
            if temporary:
                arm = SO101Arm(port, name="so101_discovery")
            try:
                if temporary:
                    arm.setup()
                if arm.get_fashionstar_joint_states():
                    live.append(port)
            except Exception as exc:  # noqa: BLE001
                log.debug("SO-ARM101 probe skipped %s: %s", port, exc)
            finally:
                if temporary:
                    try:
                        arm.close()
                    except Exception:  # noqa: BLE001
                        pass
        return live

    def _discover_live_profiles(self) -> dict[str, list[str]]:
        """Scan every supported USB arm family using feedback-only probes."""
        found: dict[str, list[str]] = {
            "so101_leader": self._discover_live_so101_ports(),
            "so101_follower": [],
            "violin_102": [],
            "b601_dm": [],
        }
        found["so101_follower"] = list(found["so101_leader"])
        so101_ports = set(found["so101_leader"])

        for info in serial.tools.list_ports.comports():
            port = info.device
            if not port or port in so101_ports:
                continue
            try:
                if info.vid == 0x1A86 and info.pid == 0x7523:
                    if probe_fashionstar_positions(port):
                        found["violin_102"].append(port)
                elif (
                    (info.manufacturer or "").upper() == "HDSC"
                    or (info.product or "").upper().startswith("CDC")
                    or (info.vid == 0x1A86 and info.pid == 0x55D3)
                ):
                    if SlaveArm.probe_positions(port):
                        found["b601_dm"].append(port)
            except Exception as exc:  # noqa: BLE001
                log.debug("Profile feedback probe skipped %s: %s", port, exc)
        return {profile: ports for profile, ports in found.items() if ports}

    def detect_and_apply_profiles(self, source: str = "manual") -> dict:
        """Auto-select only arms that can return live joint-position data.

        USB VID/PID is used only to find a serial candidate. A profile is
        considered detected after its driver has read live position feedback;
        users can always override the selected profiles from the UI.
        """
        self._try_reconnect(force=True)
        live_profiles = self._discover_live_profiles()
        result = detect_arm_profiles(live_profiles=live_profiles)
        info = result.to_dict()
        scan_message = result.message

        # A USB adapter alone is not proof that an arm is connected. Confirm
        # the candidate with actual joint-position reads from its driver.
        leader_live = False
        follower_live = False
        leader_port: Optional[str] = None
        follower_port: Optional[str] = None
        try:
            if self.master is not None and self.arm_connected("master"):
                leader_live = bool(self.master.get_fashionstar_joint_states())
                leader_port = self._arm_status["master"].get("port")
        except Exception as e:  # noqa: BLE001
            log.warning("Leader position probe failed: %s", e)
        try:
            if self.slaves and self.arm_connected("slave"):
                measured = self.slaves[0].poll_measured_joint_states()
                follower_live = bool(measured)
                follower_port = self._arm_status["slave"].get("port")
        except Exception as e:  # noqa: BLE001
            log.warning("Follower position probe failed: %s", e)

        # Report the physical scan results.  Do not overwrite them with the
        # pair that happened to be selected before the scan began.
        if not info.get("leader_id"):
            info["leader_id"] = self.leader_profile_id if leader_live else None
        if not info.get("follower_id"):
            info["follower_id"] = self.follower_profile_id if follower_live else None
        if not info.get("leader_port"):
            info["leader_port"] = leader_port
        if not info.get("follower_port"):
            info["follower_port"] = follower_port

        if leader_live and follower_live:
            info["status"] = "ok"
            info["message"] = (
                f"{scan_message} 已确认当前主臂和从臂均能读取关节位置。"
            )
        elif leader_live or follower_live:
            which = "主臂" if leader_live else "从臂"
            missing = "从臂" if leader_live else "主臂"
            info["status"] = "partial"
            info["message"] = (
                f"{scan_message} 已读取{which}关节位置数据，未读取到{missing}位置数据"
            )
        else:
            info["status"] = "none"
            info["message"] = (
                f"{scan_message} 未读取到当前主从配置的机械臂关节位置数据"
                "（请检查供电、USB 和通讯线）"
            )

        if result.status == "ok" and result.leader_id and result.follower_id:
            try:
                # Live feedback is the authoritative confirmation.  With two
                # identical CH343 adapters USB hints cannot determine which
                # arm is which, so do not replace an explicit SO-ARM101 pair
                # with the legacy B601 profile merely because it shares VID/PID.
                detected_leader = result.leader_id or self.leader_profile_id
                detected_follower = result.follower_id or self.follower_profile_id
                changed = (detected_leader, detected_follower) != (
                    self.leader_profile_id,
                    self.follower_profile_id,
                )
                self.set_profiles(detected_leader, detected_follower)
                if changed:
                    # A scan is permitted to select a different physical arm
                    # type.  Reopen under its driver before declaring it live.
                    self._drop_master(quiet=True)
                    self._drop_slave(quiet=True, safe=False)
                    self._try_reconnect(force=True)
                info["applied"] = True
            except ControllerError as e:
                info["applied"] = False
                info["message"] = f"{info['message']}；未能应用：{e}"
            self._profile_detect = info
            log.info(
                "Profile detect (%s): %s applied=%s",
                source,
                info["status"],
                info["applied"],
            )
            return self.snapshot()

        # A partial scan must not persist a mixed leader/follower pair. Keep
        # the current manual choices until a complete pair is identified.
        info["applied"] = False
        self._profile_detect = info
        log.info("Profile detect (%s): %s — %s", source, info["status"], info["message"])
        return self.snapshot()

    def set_profiles(self, leader_id: str, follower_id: str) -> dict:
        """Select leader/follower profiles and persist (used by auto-detect).

        Does not hot-swap drivers yet (phase 4). Switching while calibrating /
        recording / playing is rejected.
        """
        with self.lock:
            if self.mode in ("calibrate", "record", "transition", "playback", "return_to_follow"):
                raise ControllerError(
                    f"当前模式 {self.mode} 下不能切换臂型，请先停止录制/回放或结束校准"
                )
            leader_id, follower_id = self._resolve_profile_ids(leader_id, follower_id)
            new_pair = make_pair_id(leader_id, follower_id)
            changed = (
                leader_id != self.leader_profile_id
                or follower_id != self.follower_profile_id
            )
            self.leader_profile_id = leader_id
            self.follower_profile_id = follower_id
            self.pair_id = new_pair

        try:
            save_active_profiles(
                self.cfg.recordings_dir,
                leader_id,
                follower_id,
                pair_id=new_pair,
            )
        except OSError as e:
            raise ControllerError(f"保存运行配置失败: {e}") from e

        if changed:
            log.info("Profiles updated → %s (persist ok)", new_pair)
        return self.snapshot()

    def set_so101_ports(self, leader_port: str, follower_port: str) -> dict:
        """Persist an explicit leader/follower assignment for twin CH343 buses.

        Allowed in idle / paused / follow / free_move (same gate as profile
        select). Active teleop is paused first so the control loop does not
        command the slave while serial ownership is swapped.
        """
        leader_port, follower_port = leader_port.strip(), follower_port.strip()
        if not leader_port or not follower_port or leader_port == follower_port:
            raise ControllerError("主臂和从臂必须选择两个不同的串口")
        with self.lock:
            if self.mode in (
                "calibrate",
                "record",
                "transition",
                "playback",
                "return_to_follow",
            ):
                raise ControllerError(
                    f"当前模式 {self.mode} 下不能切换端口，请先停止录制/回放或结束校准"
                )
            if (self.leader_profile_id, self.follower_profile_id) != (
                "so101_leader",
                "so101_follower",
            ):
                raise ControllerError("端口配对仅适用于当前 SO-ARM101 主从组合")
            # Stop commanding the follower while we drop/reopen serial ports.
            if self.mode in ("follow", "free_move"):
                self.mode = "paused"
                self._arms_hint = "已切换主从端口，从臂已锁定；请点击「解除锁定」继续跟随"
            self.cfg.master_port = leader_port
            self.cfg.slave_port = follower_port
        try:
            save_active_ports(self.cfg.recordings_dir, leader_port, follower_port)
        except OSError as exc:
            raise ControllerError(f"保存端口配对失败: {exc}") from exc

        # Both adapters are read-only at this stage.  Reopen them under the
        # new ownership so the next feedback read proves the selected pairing.
        self._drop_master(quiet=True)
        self._drop_slave(quiet=True, safe=False)
        self._set_arm_status("master", "reconnecting", detail="正在应用主臂端口", port=leader_port)
        self._set_arm_status("slave", "reconnecting", detail="正在应用从臂端口", port=follower_port)
        self._try_reconnect(force=True)
        if not self.arms_ready():
            raise ControllerError("端口已保存，但未能读取所选机械臂的位置数据")
        return self.snapshot()

    # ------------------------------------------------------------------
    # Hardware setup / teardown / reconnect
    # ------------------------------------------------------------------
    def arm_connected(self, which: str) -> bool:
        """True when the physical arm link is usable for teleoperation."""
        st = self._arm_status.get(which) or {}
        return st.get("status") == "ok"

    def arms_ready(self) -> bool:
        return self.arm_connected("master") and self.arm_connected("slave")

    def _set_arm_status(
        self,
        which: str,
        status: str,
        *,
        detail: str = "",
        port: Optional[str] = None,
    ) -> None:
        cur = self._arm_status.get(which)
        if cur is None:
            return
        cur["status"] = status
        cur["detail"] = detail or ""
        if port is not None:
            cur["port"] = port

    def setup_hardware(self) -> None:
        self._set_arm_status("master", "initializing", detail="正在连接…")
        self._set_arm_status("slave", "initializing", detail="正在连接…")
        # Soft connect: missing/failed ports leave the HTTP server up and
        # the control loop will keep retrying until both arms are back.
        self._try_reconnect(force=True)
        self.detect_and_apply_profiles("boot")
        if self._calibration_ready() and self.arms_ready():
            with self.lock:
                self._arms_hint = "已加载校准映射；从臂保持锁定，请点击「解除锁定」后才开始跟随"
            log.info("Saved calibration loaded; waiting for explicit operator resume")

    def cleanup(self) -> None:
        log.info("Cleaning up hardware...")
        self.running = False
        self._drop_master(quiet=True)
        self._drop_slave(quiet=True, safe=True)
        log.info("Cleanup complete.")

    def request_shutdown(self) -> None:
        log.info("Shutdown requested")
        self.running = False

    def add_listener(self, fn) -> None:
        """Register fn(snapshot_dict). Called from the control thread."""
        self._listeners.append(fn)

    def _drop_master(self, *, quiet: bool = False) -> None:
        m = self.master
        self.master = None
        if m is None:
            return
        try:
            m.close()
        except Exception as e:  # noqa: BLE001
            if not quiet:
                log.warning("Master close failed: %s", e)

    def _drop_slave(self, *, quiet: bool = False, safe: bool = False) -> None:
        slaves = list(self.slaves)
        self.slaves = []
        for s in slaves:
            if safe:
                try:
                    s.safe_shutdown()
                except Exception as e:  # noqa: BLE001
                    if not quiet:
                        log.warning("[%s] safe_shutdown failed: %s", s.name, e)
            try:
                s.close()
            except Exception as e:  # noqa: BLE001
                if not quiet:
                    log.warning("[%s] close failed: %s", s.name, e)

    @staticmethod
    def _is_link_error(exc: BaseException) -> bool:
        if isinstance(exc, serial.SerialException):
            return True
        if isinstance(exc, OSError):
            return True
        if isinstance(exc, RuntimeError):
            msg = str(exc).lower()
            return any(
                tok in msg
                for tok in (
                    "serial",
                    "i/o",
                    "input/output",
                    "port missing",
                    "no motor feedback",
                    "no fresh",
                    "not responding",
                    "power off",
                    "usb",
                    "bus closed",
                    "serial closed",
                )
            )
        msg = str(exc).lower()
        return any(
            tok in msg
            for tok in (
                "serial",
                "i/o error",
                "input/output",
                "device disconnected",
                "clearcommerror",
                "permissionerror",
                "access is denied",
                "拒绝访问",
            )
        )

    def _probe_links(self) -> Optional[str]:
        """Active liveness check. Returns which arm failed, or None if both OK.

        Detects USB unplug (device node gone) and power-off (no servo/CAN reply)
        even when the control mode is paused and would not otherwise touch hardware.
        Requires ``_probe_fail_limit`` consecutive failures to avoid one-shot flicker.
        """
        # On Windows an already-open serial handle can survive unplugging for
        # a while.  Treat the OS device enumeration as an additional liveness
        # signal, otherwise a cached handle can leave a removed COM port shown
        # as "正常" indefinitely.
        enumerated_ports = {
            str(info.device).casefold()
            for info in serial.tools.list_ports.comports()
            if info.device
        }

        def _assert_port_present(port: Optional[str], label: str) -> None:
            if not port:
                return
            if os.name == "nt":
                if str(port).casefold() not in enumerated_ports:
                    raise OSError(f"{label} USB 串口已移除: {port}")
            elif not os.path.exists(port):
                raise OSError(f"{label} port missing: {port}")

        def _fail(which: str, err: BaseException) -> Optional[str]:
            self._probe_fail_counts[which] = self._probe_fail_counts.get(which, 0) + 1
            n = self._probe_fail_counts[which]
            log.error("%s link probe fail %d/%d: %s", which, n, self._probe_fail_limit, err)
            if n >= self._probe_fail_limit:
                self._probe_fail_counts[which] = 0
                self._enter_link_fault(which, err)
                return which
            return None

        if self.master is not None and self.arm_connected("master"):
            try:
                port = getattr(self.master, "port", None)
                _assert_port_present(port, "主臂")
                if hasattr(self.master, "check_link"):
                    self.master.check_link()
                warning = getattr(self.master, "last_warning", None)
                if warning:
                    self._set_arm_status("master", "ok", detail=warning)
                elif self.leader_profile_id == "so101_leader":
                    self._set_arm_status("master", "ok", detail="已读取 SO-ARM101 主臂位置（待校准）")
                self._probe_fail_counts["master"] = 0
            except Exception as e:  # noqa: BLE001
                hit = _fail("master", e)
                if hit:
                    return hit

        if self.slaves and self.arm_connected("slave"):
            for slave in list(self.slaves):
                try:
                    port = getattr(slave, "port", None)
                    _assert_port_present(port, "从臂")
                    if hasattr(slave, "check_link"):
                        slave.check_link()
                    warning = getattr(slave, "last_warning", None)
                    if warning:
                        self._set_arm_status("slave", "ok", detail=warning)
                    elif self.follower_profile_id == "so101_follower":
                        self._set_arm_status("slave", "ok", detail="已读取 SO-ARM101 从臂位置（待校准）")
                    self._probe_fail_counts["slave"] = 0
                except Exception as e:  # noqa: BLE001
                    hit = _fail("slave", e)
                    if hit:
                        return hit
        return None

    def _enter_link_fault(self, which: str, err: BaseException) -> None:
        """Mark arm(s) down, pause teleop, keep HTTP/WS alive for reconnect."""
        detail = f"{type(err).__name__}: {err}"
        log.error("Arm link fault (%s): %s", which, detail)
        with self.lock:
            self._stop_active_locked()
            if which in ("master", "both"):
                self._set_arm_status("master", "error", detail=detail)
            if which in ("slave", "both"):
                self._set_arm_status("slave", "error", detail=detail)
            self.last_error = f"串口异常 ({'主臂' if which == 'master' else '从臂' if which == 'slave' else '双臂'}): {err}"
            self._arms_hint = "串口异常，正在等待机械臂重新接入…"
            self._arms_were_ready = False
            self._ever_had_link_fault = True
            # Wait for unlock after reconnect (same UX as e-stop).
            if self._calibration_ready():
                self.mode = "paused"
            else:
                self.mode = "idle"
        if which in ("master", "both"):
            self._drop_master()
        if which in ("slave", "both"):
            self._drop_slave(safe=False)

    def _resolve_ports(self) -> tuple[Optional[str], Optional[str]]:
        if self.leader_profile_id == "so101_leader" and self.follower_profile_id == "so101_follower":
            return detect_so101_ports(self.cfg.master_port, self.cfg.slave_port)
        master_port = self.cfg.master_port
        slave_port = self.cfg.slave_port
        if not master_port or not slave_port:
            detected_master, detected_slave = detect_ports(master_port, slave_port)
            master_port = master_port or detected_master
            slave_port = slave_port or detected_slave
        return master_port, slave_port

    def _open_master(self, port: str) -> None:
        self._drop_master(quiet=True)
        if self.leader_profile_id == "so101_leader":
            self.master = SO101Arm(
                port, name="so101_leader", configure_position_mode=True
            )
            self.master.setup()
            self.master.disable_all()
            self._set_arm_status("master", "ok", detail="已读取 SO-ARM101 主臂位置（待校准）", port=port)
            log.info("SO-ARM101 leader initialized on %s (awaiting calibration)", port)
            return
        self.master = PiPER_MateAgilex(
            fashionstar_port=port,
            gripper_exist=self.cfg.gripper_exist,
            fashionstar_baud=self.cfg.master_baudrate,
        )
        try:
            if hasattr(self.master, "check_link"):
                self.master.check_link()
        except Exception:
            self._drop_master(quiet=True)
            raise
        self._set_arm_status("master", "ok", detail="已连接", port=port)
        log.info("Master arm initialized on %s (baud=%s)", port, self.cfg.master_baudrate)

    def _open_slave(self, port: str) -> None:
        self._drop_slave(quiet=True, safe=False)
        if self.follower_profile_id == "so101_follower":
            slave = SO101Arm(
                port, name="so101_follower", configure_position_mode=True
            )
            slave.setup()
            slave.disable_all()
            self.slaves = [slave]
            self._set_arm_status("slave", "ok", detail="已读取 SO-ARM101 从臂位置（待校准）", port=port)
            log.info("SO-ARM101 follower initialized on %s (awaiting calibration)", port)
            return
        slave = SlaveArm(port, self.cfg.baudrate, "slave_1")
        slave.setup()
        try:
            slave.check_link()
        except Exception:
            try:
                slave.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        self.slaves = [slave]
        self._set_arm_status("slave", "ok", detail="已连接", port=port)
        log.info("Slave arm initialized on %s", port)

    def _try_reconnect(self, *, force: bool = False) -> None:
        """Serialize all physical reconnect operations.

        In particular, this protects a manual auto-detection request from
        racing the control thread's background reconnect loop on COM11/COM13.
        """
        with self._hardware_reconnect_lock:
            self._try_reconnect_locked(force=force)

    def _try_reconnect_locked(self, *, force: bool = False) -> None:
        """Attempt to (re)open missing/failed arms. Caller holds reconnect lock."""
        now = time.monotonic()
        if not force and (now - self._last_reconnect_attempt) < self._reconnect_interval_s:
            return
        self._last_reconnect_attempt = now

        need_master = self.master is None or not self.arm_connected("master")
        need_slave = (not self.slaves) or not self.arm_connected("slave")
        if not need_master and not need_slave:
            return

        master_port, slave_port = self._resolve_ports()

        # A Windows USB serial handle can remain unavailable briefly after an
        # unplug/replug.  Opening COM13 for a temporary all-profile probe and
        # immediately opening it again for the real SO-ARM101 adapter causes
        # ClearCommError/PermissionError on many CH343 drivers.  When the
        # currently selected SO pair is still enumerated, reconnect it
        # directly; _open_* verifies all six live position registers anyway.
        # Fall back to a cross-profile feedback scan only when the selected
        # SO-ARM101 family is no longer present, preserving automatic support
        # for a different arm type after a hardware swap.
        selected_so101 = (
            self.leader_profile_id == "so101_leader"
            and self.follower_profile_id == "so101_follower"
        )
        if not (selected_so101 and (master_port or slave_port)):
            live_profiles = self._discover_live_profiles()
            discovered = detect_arm_profiles(live_profiles=live_profiles)
            if (
                discovered.status == "ok"
                and discovered.leader_id
                and discovered.follower_id
                and (discovered.leader_id, discovered.follower_id)
                != (self.leader_profile_id, self.follower_profile_id)
            ):
                log.info(
                    "Auto-detected live pair %s__%s; replacing %s",
                    discovered.leader_id,
                    discovered.follower_id,
                    self.pair_id,
                )
                self.set_profiles(discovered.leader_id, discovered.follower_id)
                master_port, slave_port = self._resolve_ports()

        if need_master:
            self._set_arm_status(
                "master",
                "reconnecting",
                detail="正在重连…",
                port=master_port,
            )
            if not master_port:
                self._set_arm_status(
                    "master",
                    "missing",
                    detail="未检测到串口（CH340 /dev/rebot-master）",
                )
            else:
                try:
                    self._open_master(master_port)
                except Exception as e:  # noqa: BLE001
                    log.warning("Master reconnect failed: %s", e)
                    self._drop_master(quiet=True)
                    self._set_arm_status(
                        "master",
                        "error",
                        detail=str(e),
                        port=master_port,
                    )

        if need_slave:
            self._set_arm_status(
                "slave",
                "reconnecting",
                detail="正在重连…",
                port=slave_port,
            )
            if self.follower_profile_id == "so101_follower":
                _, detected_follower = detect_so101_ports(self.cfg.master_port, self.cfg.slave_port)
                slave_candidates = [detected_follower] if detected_follower else []
            else:
                slave_candidates = detect_slave_port_candidates(self.cfg.slave_port)
            if not slave_candidates:
                self._set_arm_status(
                    "slave",
                    "missing",
                    detail="未检测到串口（HDSC CDC / CH343）",
                )
            else:
                errors: list[str] = []
                for candidate in slave_candidates:
                    try:
                        self._open_slave(candidate)
                        break
                    except Exception as e:  # noqa: BLE001
                        log.warning("Slave probe on %s failed: %s", candidate, e)
                        self._drop_slave(quiet=True, safe=False)
                        errors.append(f"{candidate}: {e}")
                else:
                    self._set_arm_status(
                        "slave",
                        "error",
                        detail="；".join(errors),
                        port=slave_candidates[0],
                    )

        if self.arms_ready() and not self._arms_were_ready:
            self._arms_were_ready = True
            if self._ever_had_link_fault:
                self._on_arms_reconnected()
            else:
                self._arms_hint = None
                log.info("Both arms connected (initial)")

    def _on_arms_reconnected(self) -> None:
        """Both links OK after a fault → re-init pose baseline, wait for unlock → follow."""
        log.warning("Both arms reconnected after fault — waiting for unlock to follow")
        for slave in self.slaves:
            try:
                if hasattr(slave, "recover"):
                    slave.recover()
            except Exception as e:  # noqa: BLE001
                log.warning("post-reconnect recover failed: %s", e)

        measured: dict = {}
        for slave in self.slaves:
            try:
                if hasattr(slave, "poll_measured_joint_states"):
                    measured = slave.poll_measured_joint_states() or {}
                elif hasattr(slave, "get_measured_joint_states"):
                    measured = slave.get_measured_joint_states() or {}
                if measured:
                    break
            except Exception as e:  # noqa: BLE001
                log.debug("post-reconnect measured: %s", e)

        master_js: Optional[dict] = None
        if self.master:
            try:
                master_js = self.master.get_fashionstar_joint_states() or None
            except Exception as e:  # noqa: BLE001
                log.warning("post-reconnect master poll: %s", e)

        with self.lock:
            self.last_error = None
            if measured:
                self.last_measured_joint_states = deepcopy(measured)
                self.last_output_joint_states = deepcopy(measured)
            if master_js:
                self.last_joint_states = deepcopy(master_js)
                self.last_safety_joint_states = deepcopy(master_js)
                self._reset_mapping_anchor(master_js)
            else:
                self._reset_mapping_anchor(None)
            self._stop_active_locked()
            if self._calibration_ready():
                self.mode = "paused"
                self._arms_hint = "机械臂已恢复，请点击「解除锁定」进入跟随"
            else:
                self.mode = "idle"
                self._arms_hint = "机械臂已恢复，请先完成校准"

    # ------------------------------------------------------------------
    # Math helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _filter(new_js: dict, last: Optional[dict], alpha: float) -> dict:
        if last is None:
            return deepcopy(new_js)
        out: dict = {}
        for k, value in new_js.items():
            prev = last.get(k)
            if not isinstance(value, (int, float)) or not isinstance(prev, (int, float)):
                continue
            if Controller._is_circular_joint(k):
                out[k] = Controller._circular_lerp(float(prev), float(value), alpha)
            else:
                out[k] = float(prev) + alpha * (float(value) - float(prev))
        return out

    @staticmethod
    def _changed_enough(a: Optional[dict], b: Optional[dict], threshold: float) -> bool:
        if a is None or b is None:
            return True
        for k in a:
            if not isinstance(a.get(k), (int, float)) or not isinstance(b.get(k), (int, float)):
                continue
            av = float(a[k])
            bv = float(b[k])
            delta = Controller._angular_delta(av, bv) if Controller._is_circular_joint(k) else bv - av
            if abs(delta) > threshold:
                return True
        return False

    @staticmethod
    def _interpolate(frame_a: dict, frame_b: dict, t_now: float) -> dict:
        t0, t1 = frame_a["t"], frame_b["t"]
        js0, js1 = frame_a["joint_states"], frame_b["joint_states"]
        if abs(t1 - t0) < 1e-9:
            return deepcopy(js0)
        a = max(0.0, min(1.0, (t_now - t0) / (t1 - t0)))
        a = a * a * (3 - 2 * a)
        out: dict = {}
        for k in js0:
            v0 = js0.get(k)
            v1 = js1.get(k)
            if not isinstance(v0, (int, float)) or not isinstance(v1, (int, float)):
                continue
            if Controller._is_circular_joint(k):
                out[k] = Controller._circular_lerp(float(v0), float(v1), a)
            else:
                out[k] = float(v0) + a * (float(v1) - float(v0))
        return out

    @staticmethod
    def _blend(js_from: dict, js_to: dict, alpha: float) -> dict:
        a = max(0.0, min(1.0, alpha))
        a = a * a * (3 - 2 * a)
        keys = set(js_from) | set(js_to)
        out: dict = {}
        for k in keys:
            v0 = js_from.get(k, js_to.get(k))
            v1 = js_to.get(k, js_from.get(k))
            if isinstance(v0, (int, float)) and isinstance(v1, (int, float)):
                if Controller._is_circular_joint(k):
                    out[k] = Controller._circular_lerp(float(v0), float(v1), a)
                else:
                    out[k] = float(v0) + a * (float(v1) - float(v0))
        return out

    def _current_output_js(self) -> Optional[dict]:
        """Current follower command pose in slave/hardware space."""
        if self.last_output_joint_states is not None:
            return deepcopy(self.last_output_joint_states)
        if self.last_measured_joint_states is not None:
            return deepcopy(self.last_measured_joint_states)
        if self.last_joint_states is not None:
            # last_joint is master-space — convert before using as a blend endpoint.
            return deepcopy(self._map_for_slave(self.last_joint_states))
        return None

    def _broadcast_to_slaves(self, js: dict) -> None:
        for slave in self.slaves:
            try:
                slave.send_joint_states(js)
            except Exception as e:  # noqa: BLE001
                if self._is_link_error(e):
                    raise
                log.warning("[%s] send_joint_states failed: %s", slave.name, e)
                raise
        self.last_output_joint_states = deepcopy(js)
        # control_Pos_Vel already updates state_q via feedback; cache for UI.
        self._cache_measured_from_slaves(poll=False)

    def _cache_measured_from_slaves(self, *, poll: bool = False) -> None:
        """Update last_measured_joint_states from the first slave that responds."""
        for slave in self.slaves:
            try:
                if poll and hasattr(slave, "poll_measured_joint_states"):
                    m = slave.poll_measured_joint_states()
                elif hasattr(slave, "get_measured_joint_states"):
                    m = slave.get_measured_joint_states()
                else:
                    continue
                if m:
                    self.last_measured_joint_states = deepcopy(m)
                    return
            except Exception as e:  # noqa: BLE001
                if self._is_link_error(e):
                    raise
                log.debug("measured poll failed: %s", e)

    def _apply_safety(self, js: dict, dt: float) -> Optional[dict]:
        """Filter master input before mapping/sending to slave.

        Spike / slew limits MUST use master-space baselines. Comparing against
        last_output (mapped slave command) false-triggers after calibration.
        Returns slew-limited master joints, or None if a spike was detected.
        """
        last = self.last_safety_joint_states
        if last is None:
            self.last_safety_joint_states = deepcopy(js)
            return js
        spike = self.cfg.spike_threshold_rad
        for k, v in js.items():
            if k == "gripper" or k not in last:
                continue
            d = (
                abs(self._angular_delta(last[k], v))
                if self._is_circular_joint(k)
                else abs(float(v) - float(last[k]))
            )
            if d > spike:
                log.warning("Safety: spike on %s (Δ=%.3f rad), pausing", k, v - last[k])
                return None
        max_step = self.cfg.max_joint_vel_rad_s * dt
        out = {}
        for k, v in js.items():
            if k == "gripper" or k not in last:
                out[k] = v
                continue
            delta = (
                self._angular_delta(last[k], v)
                if self._is_circular_joint(k)
                else v - last[k]
            )
            if delta > max_step:
                out[k] = last[k] + max_step
            elif delta < -max_step:
                out[k] = last[k] - max_step
            else:
                out[k] = v
        self.last_safety_joint_states = deepcopy(out)
        return out

    def _rebase_teleop_baseline(self, master_js: Optional[dict]) -> None:
        """Reset safety + output baselines so the next follow tick won't false-spike."""
        if not master_js:
            return
        self.last_joint_states = deepcopy(master_js)
        self.last_safety_joint_states = deepcopy(master_js)
        self._reset_mapping_anchor(master_js)
        # last_output stays in slave/command space (mapped when calibration is on).
        self.last_output_joint_states = deepcopy(self._map_for_slave(master_js))
        # After unlock / rebase, ignore spike auto-pause briefly (soft approach).
        self._safety_grace_until = time.monotonic() + max(
            2.5, float(self.cfg.return_time_s) + 0.5
        )

    # ------------------------------------------------------------------
    # Public commands (called by REST handlers)
    # ------------------------------------------------------------------
    def start_record(self, name: Optional[str] = None) -> None:
        with self.lock:
            if self.mode == "record":
                raise ControllerError("Already recording")
            if self.mode == "calibrate":
                raise ControllerError("Finish or abort calibration before recording")
            if self.mode == "idle" or not self._calibration_mapping_enabled():
                raise ControllerError("需要先完成校准才能录制")
            self._stop_active_locked()
            self._calibrating = False
            self.record_buffer = []
            self.record_action_name = name
            self.record_start_time = time.monotonic()
            self.last_recorded_time = 0.0
            self.last_recorded_joint_states = None
            self.mode = "record"
            if self.last_joint_states is not None:
                init = deepcopy(self.last_joint_states)
                self.record_buffer.append({"t": 0.0, "joint_states": init})
                self.last_recorded_joint_states = deepcopy(init)
            log.info("Recording started (name=%s)", name)

    def stop_record(self) -> Action:
        with self.lock:
            if self.mode != "record":
                raise ControllerError("Not currently recording")
            frames = self.record_buffer
            # Append end-hold frame so the arm settles. Loop blend is now
            # synthesized at playback time, so don't bake the loop-back frame in.
            if frames:
                last = deepcopy(frames[-1])
                frames.append({
                    "t": last["t"] + self.cfg.end_hold_time_s,
                    "joint_states": deepcopy(last["joint_states"]),
                })
            name = self.record_action_name
            self.record_buffer = []
            self.record_action_name = None
            self.record_start_time = None
            self.last_recorded_time = None
            self.last_recorded_joint_states = None
            self.mode = "follow"
            if not frames:
                log.warning("Recording stopped with no frames; nothing saved")
                raise ControllerError("Recording produced no frames")
            action = self.library.create(frames=frames, name=name)
            log.info("Recording saved as action %s (%s)", action.id, action.name)
            return action

    def start_playback(self, action_id: str, mode: PlayMode) -> Action:
        action = self.library.get(action_id)
        if not action.frames:
            raise ControllerError(f"Action {action_id} has no frames")
        with self.lock:
            self._stop_active_locked()
            from_js = self._current_output_js()  # slave/hardware space
            # Action frames are recorded in MASTER space; convert the start
            # pose to slave space so the transition blend never mixes spaces.
            to_js = self._map_for_slave(action.frames[0]["joint_states"])
            if from_js is None:
                # No reference pose yet — start playback immediately.
                self._begin_playback_locked(action, mode)
                return action
            self.transition_start_time = time.monotonic()
            self.transition_from_js = from_js
            self.transition_to_js = to_js
            self.transition_target_action = action
            self.transition_target_mode = mode
            self.mode = "transition"
            log.info(
                "Transition → action %s (mode=%s, slave-space blend)",
                action.id,
                mode,
            )
            return action

    def stop_playback(self) -> None:
        with self.lock:
            if self.mode in ("playback", "transition", "return_to_follow"):
                self._stop_active_locked()
                self.mode = "follow"

    def goto_joint_states(self, joint_states: dict) -> None:
        """Blend from current slave output to a static pose (master-space joints).

        On blend complete, returns to follow (transition_target_action is None).
        """
        if not joint_states:
            raise ControllerError("goto_joint_states requires joint_states")
        with self.lock:
            if self.mode == "calibrate":
                raise ControllerError("校准中请先完成或取消校准")
            if self.mode == "record":
                raise ControllerError("录制中不能跳转到命名姿态")
            self._stop_active_locked()
            from_js = self._current_output_js()
            to_js = self._map_for_slave(deepcopy(joint_states))
            if from_js is None:
                # No reference — command pose immediately then follow.
                self.last_output_joint_states = deepcopy(to_js)
                self.mode = "follow"
                log.info("goto_joint_states: no prior pose; applied immediately")
                return
            self.transition_start_time = time.monotonic()
            self.transition_from_js = from_js
            self.transition_to_js = to_js
            self.transition_target_action = None
            self.transition_target_mode = None
            self.mode = "transition"
            log.info("Transition → named pose (then follow)")

    def force_follow(self) -> None:
        with self.lock:
            self._stop_active_locked()
            self._calibrating = False
            if not self._calibration_ready():
                self.mode = "idle"
                log.info("Forced idle (no valid calibration — teleop blocked)")
                return
            self.mode = "follow"
            log.info("Forced follow mode")
        for slave in self.slaves:
            try:
                if hasattr(slave, "enable_all"):
                    slave.enable_all()
            except Exception as e:  # noqa: BLE001
                log.warning("enable_all on force_follow: %s", e)

    def start_calibrate(self) -> None:
        """Enter calibrate mode: free-move follower; collect min/max ranges."""
        with self.lock:
            if self.mode == "calibrate":
                raise ControllerError("Already calibrating")
            if self.mode not in ("follow", "paused", "idle", "free_move"):
                raise ControllerError(f"Cannot start calibration from mode={self.mode}")
            self._stop_active_locked()
            self._reset_mapping_anchor(None)
            # Stop teleop commands BEFORE touching serial for free-move.
            self._calibrating = True
            self.mode = "calibrate"

        # Free-move both arms (outside lock; control loop no longer broadcasts).
        for slave in self.slaves:
            try:
                if hasattr(slave, "enter_free_move"):
                    slave.enter_free_move()
                elif hasattr(slave, "disable_all"):
                    slave.disable_all()
            except Exception as e:  # noqa: BLE001
                log.warning("enter_free_move failed: %s", e)

        with self.lock:
            if self.mode != "calibrate":
                return
            self._cache_measured_from_slaves(poll=True)
            master_js = self.last_joint_states
            if self.master:
                try:
                    master_js = self.master.get_fashionstar_joint_states() or master_js
                    if master_js:
                        self.last_joint_states = deepcopy(master_js)
                except Exception as e:  # noqa: BLE001
                    log.debug("master read at calibrate start: %s", e)
            slave_js = self.last_measured_joint_states or self.last_output_joint_states
            self.cal_master_ranges = seed_ranges(master_js)
            self.cal_slave_ranges = seed_ranges(slave_js)
            log.info(
                "Calibration started — hand-move BOTH arms through full range "
                "(follower MIT zero-torque free-move)"
            )

    def finish_calibrate(self) -> dict:
        """Validate swept ranges, persist, re-enable motors, return to follow."""
        with self.lock:
            if self.mode != "calibrate":
                raise ControllerError("Not currently calibrating")
            if not self._calibration_ready():
                raise ControllerError(
                    "校准范围不足：请把主臂、从臂每个关节都转到机械极限后再完成"
                )
            saved_at = save_calibration(
                self._calibration_path,
                self.cal_master_ranges,
                self.cal_slave_ranges,
            )
            self.cal_saved_at = saved_at
            self._calibrating = False
            self.mode = "follow"
            # Seed baselines from live master so first follow tick won't spike.
            master_js = self.last_joint_states
            if self.master:
                try:
                    master_js = self.master.get_fashionstar_joint_states() or master_js
                except Exception:  # noqa: BLE001
                    pass
            if master_js:
                self.last_joint_states = deepcopy(master_js)
            payload = ranges_payload(
                self.cal_master_ranges,
                self.cal_slave_ranges,
                active=False,
                saved_at=saved_at,
                mapping_enabled=True,
                joint_keys=self._active_joint_keys(),
            )
            log.info("Calibration finished (saved_at=%s, mapping ON)", saved_at)

        for slave in self.slaves:
            try:
                if hasattr(slave, "enable_all"):
                    slave.enable_all()
            except Exception as e:  # noqa: BLE001
                log.warning("enable_all failed: %s", e)

        with self.lock:
            self._rebase_teleop_baseline(self.last_joint_states)
        return payload

    def cancel_calibrate(self) -> None:
        """Abort calibration without saving; do NOT enable teleop / command poses."""
        with self.lock:
            if self.mode != "calibrate":
                raise ControllerError("Not currently calibrating")
            m_r, s_r, saved_at = load_calibration(self._calibration_path)
            self.cal_master_ranges = m_r
            self.cal_slave_ranges = s_r
            self.cal_saved_at = saved_at
            self._calibrating = False
            # Always idle after cancel — never snap follower to master pose.
            self.mode = "idle"
            log.info(
                "Calibration cancelled — discarded in-session ranges (idle, motors stay free)"
            )

        # Keep motors disabled / free; do not enable_all (would command POS_VEL).
        for slave in self.slaves:
            try:
                if hasattr(slave, "disable_all"):
                    slave.disable_all()
            except Exception as e:  # noqa: BLE001
                log.warning("disable_all after calibrate cancel: %s", e)

    def _calibration_mapping_enabled(self) -> bool:
        return (
            not self._calibrating
            and self._calibration_ready()
        )

    def set_motor_map(self, mapping: dict) -> dict:
        """Persist master→slave routing; soft-blend slave to the new mapped pose."""
        with self.lock:
            from_js = deepcopy(
                self.last_output_joint_states
                or self.last_measured_joint_states
                or {}
            )
            self.motor_map = save_motor_map(self._motor_map_path, mapping)
            # Do NOT snap last_output — blend in follow/record so remapped
            # motors ease into the new target instead of jumping.
            if (
                from_js
                and self.mode in ("follow", "record")
                and self.last_joint_states
            ):
                target = self._map_for_slave(self.last_joint_states)
                if self._joint_delta(from_js, target) > 1e-3:
                    self._mmap_blend_start = time.monotonic()
                    self._mmap_blend_from = from_js
                    log.info(
                        "Motor map updated; blending slave over %.1fs",
                        self.cfg.return_time_s,
                    )
                else:
                    self._mmap_blend_start = None
                    self._mmap_blend_from = None
                    log.info("Motor map updated (no pose change)")
            else:
                self._mmap_blend_start = None
                self._mmap_blend_from = None
                log.info("Motor map updated: %s", self.motor_map)
            return {k: self.motor_map.get(k) for k in MOTOR_MAP_KEYS}

    @staticmethod
    def _joint_delta(a: dict, b: dict) -> float:
        keys = set(a) | set(b)
        best = 0.0
        for k in keys:
            av, bv = a.get(k), b.get(k)
            if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
                delta = Controller._angular_delta(float(av), float(bv))
                if not Controller._is_circular_joint(k):
                    delta = float(bv) - float(av)
                best = max(best, abs(delta))
        return best

    def _apply_mmap_blend(self, target: dict) -> dict:
        """Ease slave from pose-at-remap toward the live remapped target."""
        start = self._mmap_blend_start
        from_js = self._mmap_blend_from
        if start is None or not from_js:
            return target
        T = max(0.05, float(self.cfg.return_time_s))
        elapsed = time.monotonic() - start
        if elapsed >= T:
            self._mmap_blend_start = None
            self._mmap_blend_from = None
            return target
        alpha = min(1.0, elapsed / T)
        alpha = alpha * alpha * (3 - 2 * alpha)
        keys = set(from_js) | set(target)
        out: dict[str, float] = {}
        for k in keys:
            a = from_js.get(k, target.get(k))
            b = target.get(k, from_js.get(k))
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                if self._is_circular_joint(k):
                    out[k] = self._circular_lerp(float(a), float(b), alpha)
                else:
                    out[k] = float(a) + alpha * (float(b) - float(a))
        return out

    def _clear_mmap_blend(self) -> None:
        self._mmap_blend_start = None
        self._mmap_blend_from = None

    def _map_for_slave(self, master_js: dict) -> dict:
        """Range-map (if calibrated) then route into slave motors per motor_map.

        Unmapped slave motors keep the last commanded / measured pose.
        """
        hold = self.last_output_joint_states or self.last_measured_joint_states or {}
        mapping = self.motor_map or default_motor_map()
        if self._calibration_mapping_enabled():
            aligned_master = self._align_master_for_mapping(master_js)
            routed = route_range_map(
                aligned_master,
                self.cal_master_ranges,
                self.cal_slave_ranges,
                mapping,
            )
            out: dict[str, float] = {}
            for sk in MOTOR_MAP_KEYS:
                if sk in routed:
                    out[sk] = float(routed[sk])
                elif isinstance(hold.get(sk), (int, float)):
                    out[sk] = float(hold[sk])
                else:
                    mv = master_js.get(sk)
                    out[sk] = float(mv) if isinstance(mv, (int, float)) else 0.0
            return out
        return apply_motor_map(master_js, mapping, hold=hold)

    def pause(self) -> None:
        """Emergency stop: stop sending commands to the slave so it holds at
        the last commanded pose. Master is still polled so we can resume into
        the operator's current pose, but its motion is not broadcast.
        Discards any in-progress record/playback/calibration.
        """
        with self.lock:
            if self.mode == "paused":
                return
            was_cal = self.mode == "calibrate"
            if was_cal:
                m_r, s_r, saved_at = load_calibration(self._calibration_path)
                self.cal_master_ranges = m_r
                self.cal_slave_ranges = s_r
                self.cal_saved_at = saved_at
                self._calibrating = False
                log.warning("Calibration aborted by pause")
            self._stop_active_locked()
            self._clear_mmap_blend()
            # Aborting calibrate → idle (no pose snap). Else pause holds last cmd.
            self.mode = "idle" if was_cal else "paused"
            log.warning("Mode after pause request: %s", self.mode)
        if was_cal:
            for slave in self.slaves:
                try:
                    if hasattr(slave, "disable_all"):
                        slave.disable_all()
                except Exception as e:  # noqa: BLE001
                    log.warning("disable_all after calibrate abort: %s", e)

    def start_free_move(self) -> None:
        """Stop teleop and unlock follower motors for hand-guiding (no damping)."""
        with self.lock:
            if self.mode == "free_move":
                return
            if self.mode == "calibrate":
                raise ControllerError("校准中请先完成或取消校准")
            self._stop_active_locked()
            self._clear_mmap_blend()
            self._calibrating = False
            self.mode = "free_move"
            self.last_error = None
            log.warning("Free-move: teleop stopped, unlocking motors")

        for slave in self.slaves:
            try:
                if hasattr(slave, "enter_free_move"):
                    slave.enter_free_move()
                elif hasattr(slave, "disable_all"):
                    slave.disable_all()
            except Exception as e:  # noqa: BLE001
                log.warning("enter_free_move on start_free_move: %s", e)

    def resume(self) -> None:
        """Leave paused/idle/free_move and return to teleop.

        After motor protection + power cycle the follower is often disabled and
        its encoder frame no longer matches ``last_output``. Resume therefore:
          1) parks the control loop (so free_move won't keep disabling J1)
          2) re-handshakes / enables all slave motors
          3) rebases software pose to measured encoders
          4) soft-blends in slave space toward the live mapped master pose
          5) enters follow
        """
        if not self.arms_ready():
            raise ControllerError("机械臂未就绪，请等待串口恢复后再解锁")
        with self.lock:
            if self.mode == "idle":
                if not self._calibration_ready():
                    raise ControllerError("需要先完成校准才能跟随")
                from_idle = True
                from_free_move = False
            elif self.mode in ("paused", "free_move"):
                if not self._calibration_ready():
                    self.mode = "idle"
                    log.info("Resume blocked — no valid calibration")
                    return
                from_idle = False
                from_free_move = self.mode == "free_move"
                # CRITICAL: leave free_move BEFORE serial re-enable. The control
                # loop's hold_free_move() periodically disable()s J1; racing that
                # against recover/enable leaves J1 dead while other joints work.
                self.mode = "paused"
                self._clear_mmap_blend()
            else:
                return

        if from_idle:
            for slave in self.slaves:
                try:
                    if hasattr(slave, "enable_all"):
                        slave.enable_all()
                except Exception as e:  # noqa: BLE001
                    log.warning("enable_all on resume from idle: %s", e)
            with self.lock:
                self.mode = "follow"
                self.last_error = None
                self._arms_hint = None
                self._rebase_teleop_baseline(self.last_joint_states)
            log.info("Idle → follow (valid calibration)")
            return

        # ---- paused / free_move → re-enable + soft approach to mapped pose ----
        log.warning(
            "Resume: re-handshake motors then soft approach (from_free_move=%s)",
            from_free_move,
        )
        for slave in self.slaves:
            try:
                # enable_all clears free-move flags and retries POS_VEL switch —
                # preferred after free_move (J1 was force-disabled there).
                if (
                    isinstance(slave, SO101Arm)
                    and hasattr(slave, "enable_all")
                ):
                    # SO-ARM101 uses raw STS3215 torque control.  Its
                    # recover() is deliberately read-only, so resume is the
                    # only explicit path that enables the follower.
                    slave.enable_all()
                elif from_free_move and hasattr(slave, "enable_all"):
                    slave.enable_all()
                elif hasattr(slave, "recover"):
                    slave.recover()
                elif hasattr(slave, "enable_all"):
                    slave.enable_all()
            except Exception as e:  # noqa: BLE001
                log.warning("resume handshake failed: %s", e)

        measured: dict = {}
        for _attempt in range(5):
            for slave in self.slaves:
                try:
                    if hasattr(slave, "poll_measured_joint_states"):
                        m = slave.poll_measured_joint_states() or {}
                    elif hasattr(slave, "get_measured_joint_states"):
                        m = slave.get_measured_joint_states() or {}
                    else:
                        m = {}
                    if m and any(isinstance(v, (int, float)) for v in m.values()):
                        measured = m
                        break
                except Exception as e:  # noqa: BLE001
                    log.warning("resume measured poll failed: %s", e)
            if measured:
                break
            time.sleep(0.05)

        master_js: Optional[dict] = None
        if self.master:
            try:
                master_js = self.master.get_fashionstar_joint_states()
            except Exception as e:  # noqa: BLE001
                log.warning("resume master poll failed: %s", e)

        with self.lock:
            if master_js:
                self.last_joint_states = deepcopy(master_js)
            else:
                master_js = (
                    deepcopy(self.last_joint_states)
                    if self.last_joint_states
                    else None
                )

            if measured:
                self.last_measured_joint_states = deepcopy(measured)
                self.last_output_joint_states = deepcopy(measured)
            from_js = deepcopy(
                self.last_output_joint_states
                or self.last_measured_joint_states
                or {}
            )
            target = (
                self._map_for_slave(master_js) if master_js else deepcopy(from_js)
            )
            self._stop_active_locked()
            self._clear_mmap_blend()
            if from_js and target and self._joint_delta(from_js, target) > 1e-3:
                self._mmap_blend_start = time.monotonic()
                self._mmap_blend_from = from_js
                log.info(
                    "Resume soft approach over %.1fs (maxΔ=%.3f rad)",
                    self.cfg.return_time_s,
                    self._joint_delta(from_js, target),
                )
            self.mode = "follow"
            self.last_error = None
            self._arms_hint = None
            self._rebase_teleop_baseline(master_js)
            log.info("→ follow (motors re-enabled after free_move/paused)")

    def set_safety(self, enabled: bool) -> None:
        with self.lock:
            self.safety_enabled = bool(enabled)
            log.warning("Safety mode %s", "ENABLED" if enabled else "DISABLED")
            if not enabled:
                self.last_error = None  # clear stale spike warning

    def recover(self) -> dict:
        """Two-phase recovery from a single DM motor's CAN/power line being
        unplugged and re-plugged. Designed to be safe against accidental presses:

        Phase 1 — slow-blend slave to home pose (all 6 arm joints = 0, gripper held).
                  If it was a false alarm, this is the entire visible effect: the
                  arm just goes to zero pose. The operator can press 解除锁定
                  during the blend to abort and skip Phase 2.
        Phase 2 — disable → switchControlMode → enable → refresh_motor_status
                  per motor, then rebase last_output_joint_states to measured.
        End state — controller stays paused. Operator presses 解除锁定 to
                    slow-blend back to live master via return_to_follow.

        NOTE: DM 4310/4340 single-turn encoders lose their zero on power loss.
        If the unplugged motor was without power, its post-recovery angle will
        be offset from the original frame by an unknown amount. Software cannot
        fix this — burn the zero pose to flash (``save_pos_zero``) once for a
        permanent fix, or accept the operational offset.
        """
        with self.lock:
            if self._recovering:
                log.info("Recover already in progress")
                return self.snapshot()
            self._recovering = True
            self._stop_active_locked()
            self.mode = "paused"
            self.last_error = None
            from_js = deepcopy(self.last_output_joint_states or self.last_joint_states or {})
        log.warning("Recover requested")

        try:
            # ---- Phase 1: slow-blend to home pose ----
            aborted = False
            if from_js:
                home_js = {k: 0.0 for k in from_js}
                if "gripper" in from_js:
                    home_js["gripper"] = from_js["gripper"]   # don't slam the gripper
                log.warning("Recover Phase 1: blend → home over %.1fs",
                            self.cfg.recover_blend_time_s)
                aborted = not self._blend_slave_during_recover(from_js, home_js)
                if aborted:
                    log.warning("Recover aborted during blend — skipping handshake")

            # ---- Phase 2: re-handshake (skip if aborted) ----
            if not aborted:
                log.warning("Recover Phase 2: re-handshaking slave motors")
                errors: list[str] = []
                for slave in self.slaves:
                    try:
                        slave.recover()
                    except Exception as e:  # noqa: BLE001
                        log.exception("[%s] recover failed", slave.name)
                        errors.append(f"{slave.name}: {e}")

                # ---- Phase 3: rebase software pose to measured ----
                measured: dict = {}
                for slave in self.slaves:
                    try:
                        m = slave.get_measured_joint_states() if hasattr(slave, "get_measured_joint_states") else {}
                        if m:
                            measured = m
                            break
                    except Exception as e:  # noqa: BLE001
                        log.warning("get_measured failed: %s", e)

                with self.lock:
                    base = dict(self.last_output_joint_states or self.last_joint_states or {})
                    for k, v in measured.items():
                        if k != "gripper":
                            base[k] = v
                    if base:
                        self.last_output_joint_states = base
                        log.info("Recover: rebased last_output to measured pose")
                    if errors:
                        self.last_error = "recover: " + "; ".join(errors)
        finally:
            with self.lock:
                self._recovering = False
        return self.snapshot()

    def _blend_slave_during_recover(self, from_js: dict, to_js: dict) -> bool:
        """Slow-blend slave from from_js → to_js, bypassing the control thread.

        Caller must have set mode=paused and self._recovering=True. The control
        thread won't write to the slave while paused, so this is the only writer.
        Returns True if blend completed, False if user aborted (resume pressed,
        which flipped mode to return_to_follow).
        """
        duration = self.cfg.recover_blend_time_s
        if duration <= 0 or not self.slaves:
            return True
        steps = max(1, int(duration * self.cfg.update_rate_hz))
        dt = duration / steps
        for step in range(1, steps + 1):
            with self.lock:
                if self.mode != "paused":
                    return False    # user aborted
            a = step / steps
            a = a * a * (3 - 2 * a)
            js = {}
            for k in from_js:
                fv = from_js.get(k)
                tv = to_js.get(k)
                if not isinstance(fv, (int, float)) or not isinstance(tv, (int, float)):
                    continue
                if self._is_circular_joint(k):
                    js[k] = self._circular_lerp(float(fv), float(tv), a)
                else:
                    js[k] = float(fv) + a * (float(tv) - float(fv))
            for slave in self.slaves:
                try:
                    slave.send_joint_states(js)
                except Exception as e:  # noqa: BLE001
                    log.warning("[%s] recover blend send failed: %s", slave.name, e)
            with self.lock:
                self.last_output_joint_states = deepcopy(js)
            time.sleep(dt)
        return True

    # ------------------------------------------------------------------
    # Internal mode transitions
    # ------------------------------------------------------------------
    def _stop_active_locked(self) -> None:
        """Drop any active record/playback/transition/return_to_follow state.
        Caller must hold self.lock and is responsible for setting the new mode.
        """
        if self.mode == "record":
            # Discard the in-progress recording; do not save a partial.
            self.record_buffer = []
            self.record_action_name = None
            self.record_start_time = None
            self.last_recorded_time = None
            self.last_recorded_joint_states = None
        self.current_action = None
        self.current_play_mode = None
        self.play_start_time = None
        self.play_index = 0
        self.transition_start_time = None
        self.transition_from_js = None
        self.transition_to_js = None
        self.transition_target_action = None
        self.transition_target_mode = None
        self._clear_mmap_blend()
        self.return_start_time = None
        self.return_from_js = None

    def _begin_playback_locked(self, action: Action, mode: PlayMode) -> None:
        self.current_action = action
        self.current_play_mode = mode
        self.play_start_time = time.monotonic()
        self.play_index = 0
        self.mode = "playback"
        log.info("Playback start: %s (%s, %.2fs, %s)",
                 action.id, action.name, action.duration_s, mode)

    def _begin_return_to_follow_locked(self) -> None:
        # Blend in MASTER space toward live master (never mix mapped last_output).
        self.return_start_time = time.monotonic()
        self.return_from_js = deepcopy(
            self.last_safety_joint_states
            or self.last_joint_states
            or {}
        )
        self.current_action = None
        self.current_play_mode = None
        self.play_start_time = None
        self.play_index = 0
        self.mode = "return_to_follow"
        log.info("Return-to-follow start (%.2fs)", self.cfg.return_time_s)

    # ------------------------------------------------------------------
    # Per-tick updates
    # ------------------------------------------------------------------
    def _update_recording(self, js: dict) -> None:
        with self.lock:
            if self.mode != "record" or self.record_start_time is None:
                return
            t_rel = time.monotonic() - self.record_start_time
            if (self.last_recorded_time is not None
                    and (t_rel - self.last_recorded_time) < self.cfg.min_record_interval_s):
                return
            filtered = self._filter(js, self.last_recorded_joint_states,
                                    alpha=self.cfg.record_filter_alpha)
            if not self._changed_enough(filtered, self.last_recorded_joint_states,
                                        threshold=self.cfg.min_joint_change_rad):
                return
            self.record_buffer.append({"t": t_rel, "joint_states": deepcopy(filtered)})
            self.last_recorded_joint_states = deepcopy(filtered)
            self.last_recorded_time = t_rel

    def _update_transition(self) -> Optional[dict]:
        with self.lock:
            if self.mode != "transition":
                return None
            if self.transition_from_js is None or self.transition_to_js is None:
                self._stop_active_locked()
                self.mode = "follow"
                return None
            elapsed = time.monotonic() - self.transition_start_time
            T = self.cfg.transition_time_s
            alpha = elapsed / T if T > 1e-9 else 1.0
            if alpha >= 1.0:
                action = self.transition_target_action
                mode = self.transition_target_mode or "loop"
                self.transition_start_time = None
                self.transition_from_js = None
                self.transition_to_js = None
                self.transition_target_action = None
                self.transition_target_mode = None
                if action is not None:
                    self._begin_playback_locked(action, mode)
                else:
                    self.mode = "follow"
                return None
            return self._blend(self.transition_from_js, self.transition_to_js, alpha)

    def _update_playback(self) -> Optional[dict]:
        with self.lock:
            if self.mode != "playback" or self.current_action is None:
                return None
            seq = self.current_action.frames
            if not seq:
                self._stop_active_locked()
                self.mode = "follow"
                return None
            if len(seq) == 1:
                return deepcopy(seq[0]["joint_states"])

            elapsed = time.monotonic() - self.play_start_time
            total = seq[-1]["t"]
            if total <= 1e-9:
                return deepcopy(seq[-1]["joint_states"])

            # End of sequence reached
            if elapsed > total:
                if self.current_play_mode == "loop":
                    blend_t = self.cfg.loop_blend_time_s
                    over = elapsed - total
                    if over < blend_t and blend_t > 1e-9:
                        # Smooth blend from last → first to hide the wrap discontinuity.
                        return self._blend(
                            seq[-1]["joint_states"],
                            seq[0]["joint_states"],
                            over / blend_t,
                        )
                    # Reset playback head past the blend window.
                    self.play_start_time = time.monotonic() - (over - blend_t)
                    self.play_index = 0
                    elapsed = over - blend_t
                else:  # "once"
                    self._begin_return_to_follow_locked()
                    return self.return_from_js or None

            # Advance index
            while (self.play_index < len(seq) - 1
                   and seq[self.play_index + 1]["t"] < elapsed):
                self.play_index += 1
            while self.play_index > 0 and seq[self.play_index]["t"] > elapsed:
                self.play_index -= 1

            i = self.play_index
            if i >= len(seq) - 1:
                return deepcopy(seq[-1]["joint_states"])
            return self._interpolate(seq[i], seq[i + 1], elapsed)

    def _update_return_to_follow(self) -> Optional[dict]:
        """Slow blend from playback-end pose → live master pose, then resume follow.

        Re-samples master each tick (option (b)): if the operator is moving the
        master during the return window, the slave tracks toward wherever the
        master currently is. Master read failure raises and is fatal.
        """
        if self.mode != "return_to_follow":
            return None
        # NB: read master OUTSIDE the lock (serial I/O can be slow), then update state.
        master_js = self.master.get_fashionstar_joint_states() if self.master else None
        with self.lock:
            if self.mode != "return_to_follow" or self.return_start_time is None:
                return None
            elapsed = time.monotonic() - self.return_start_time
            T = self.cfg.return_time_s
            alpha = min(1.0, elapsed / T) if T > 1e-9 else 1.0
            smooth = alpha * alpha * (3 - 2 * alpha)
            if not master_js:
                return deepcopy(self.last_output_joint_states or {})
            self.last_joint_states = deepcopy(master_js)
            if alpha >= 1.0:
                self._stop_active_locked()
                self.mode = "follow"
                self._rebase_teleop_baseline(master_js)
                return master_js
            return self._blend(self.return_from_js or master_js, master_js, smooth)

    # ------------------------------------------------------------------
    # State snapshot (for /api/state and WS broadcast)
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            recording_frames = (
                len(self.record_buffer) if self.mode == "record" else None
            )
            master_js = (
                dict(self.last_joint_states) if self.last_joint_states else {}
            )
            # During calibrate / free_move there is no slave command — show live master.
            if self.mode in ("calibrate", "free_move"):
                cmd_or_master = master_js
            elif self.last_output_joint_states:
                cmd_or_master = dict(self.last_output_joint_states)
            else:
                cmd_or_master = master_js
            out = {
                "ts": time.time(),
                "mode": self.mode,
                "safety_enabled": self.safety_enabled,
                "recovering": self._recovering,
                "active_action_id": (
                    self.current_action.id if self.current_action
                    else (self.transition_target_action.id if self.transition_target_action else None)
                ),
                "active_play_mode": (
                    self.current_play_mode
                    or self.transition_target_mode
                ),
                "frame_count": self.frame_count,
                "recording_frames": recording_frames,
                "joint_states": cmd_or_master,
                "master_joint_states": master_js,
                "slave_joint_states": (
                    dict(self.last_measured_joint_states)
                    if self.last_measured_joint_states
                    else {}
                ),
                "calibration": ranges_payload(
                    self.cal_master_ranges,
                    self.cal_slave_ranges,
                    active=self.mode == "calibrate",
                    saved_at=self.cal_saved_at,
                    mapping_enabled=self._calibration_mapping_enabled(),
                    joint_keys=self._active_joint_keys(),
                ),
                "motor_map": {
                    k: self.motor_map.get(k) for k in MOTOR_MAP_KEYS
                },
                "motor_map_blending": self._mmap_blend_start is not None,
                "last_error": self.last_error,
                "arms": {
                    "master": dict(self._arm_status["master"]),
                    "slave": dict(self._arm_status["slave"]),
                    "ready": self.arms_ready(),
                    "hint": self._arms_hint,
                },
                **self.profile_snapshot_fields(),
            }
            if self._voice_status_provider is not None:
                try:
                    out["voice"] = self._voice_status_provider()
                except Exception as e:  # noqa: BLE001
                    log.debug("voice status provider failed: %s", e)
            return out

    # ------------------------------------------------------------------
    # Main control loop (run in dedicated thread)
    # ------------------------------------------------------------------
    def run(self) -> None:
        update_interval = 1.0 / self.cfg.update_rate_hz
        push_interval = 1.0 / max(1, self.cfg.ws_push_hz)
        last_push = 0.0

        log.info("Control loop @ %dHz starting", self.cfg.update_rate_hz)
        try:
            last_probe = 0.0
            probe_interval = 0.8
            while self.running:
                loop_start = time.monotonic()
                try:
                    # Active probe even when paused (USB/power loss otherwise silent).
                    now = time.monotonic()
                    # Probe each healthy side independently.  Waiting for
                    # both arms to be healthy leaves the other side's stale
                    # success state frozen after one USB cable is removed.
                    if (
                        (self.arm_connected("master") or self.arm_connected("slave"))
                        and (now - last_probe) >= probe_interval
                    ):
                        last_probe = now
                        if self._probe_links() is not None:
                            continue

                    # Keep HTTP/WS alive across USB unplug: reconnect instead of exit.
                    if not self.arms_ready():
                        self._try_reconnect()
                        now = time.monotonic()
                        if now - last_push >= push_interval:
                            last_push = now
                            snap = self.snapshot()
                            for fn in self._listeners:
                                try:
                                    fn(snap)
                                except Exception as e:  # noqa: BLE001
                                    log.debug("listener error: %s", e)
                        time.sleep(update_interval)
                        continue

                    with self.lock:
                        mode = self.mode

                    out_js: Optional[dict] = None

                    if mode in ("follow", "record"):
                        try:
                            js = (
                                self.master.get_fashionstar_joint_states()
                                if self.master
                                else None
                            )
                        except Exception as e:  # noqa: BLE001
                            if self._is_link_error(e):
                                self._enter_link_fault("master", e)
                                continue
                            raise
                        if js:
                            self.last_joint_states = deepcopy(js)
                            self._notify_voice_master_js(js)
                            # Block teleop until a valid calibration exists.
                            if not self._calibration_mapping_enabled():
                                if self.mode == "follow":
                                    with self.lock:
                                        if self.mode == "follow" and not self._calibration_mapping_enabled():
                                            self.mode = "idle"
                                self.frame_count += 1
                            else:
                                send_js = (
                                    self._apply_safety(js, update_interval)
                                    if self.safety_enabled else js
                                )
                                if send_js is None:
                                    if time.monotonic() < self._safety_grace_until:
                                        log.warning(
                                            "Safety spike ignored during post-unlock grace"
                                        )
                                        self._rebase_teleop_baseline(js)
                                        send_js = js
                                    else:
                                        self.last_error = "safety: spike on master input"
                                        self.pause()
                                        send_js = None
                                if send_js is not None:
                                    mapped = self._apply_mmap_blend(
                                        self._map_for_slave(send_js)
                                    )
                                    self._broadcast_to_slaves(mapped)
                                    out_js = mapped
                                    if mode == "record":
                                        self._update_recording(send_js)
                                    self.frame_count += 1
                    elif mode == "idle":
                        # UI telemetry only — never command follower.
                        try:
                            js = (
                                self.master.get_fashionstar_joint_states()
                                if self.master
                                else None
                            )
                        except Exception as e:  # noqa: BLE001
                            if self._is_link_error(e):
                                self._enter_link_fault("master", e)
                                continue
                            raise
                        if js:
                            self.last_joint_states = deepcopy(js)
                            self.frame_count += 1
                    elif mode == "free_move":
                        # Unlock motors: MIT zero / disable, no teleop broadcast.
                        try:
                            js = (
                                self.master.get_fashionstar_joint_states()
                                if self.master
                                else None
                            )
                        except Exception as e:  # noqa: BLE001
                            if self._is_link_error(e):
                                self._enter_link_fault("master", e)
                                continue
                            raise
                        if js:
                            self.last_joint_states = deepcopy(js)
                            self.frame_count += 1
                        link_fault = False
                        for slave in self.slaves:
                            try:
                                if hasattr(slave, "hold_free_move"):
                                    m = slave.hold_free_move()
                                    if m:
                                        self.last_measured_joint_states = deepcopy(m)
                                        break
                                else:
                                    self._cache_measured_from_slaves(poll=True)
                                    break
                            except Exception as e:  # noqa: BLE001
                                if self._is_link_error(e):
                                    self._enter_link_fault("slave", e)
                                    link_fault = True
                                    break
                                log.debug("hold_free_move: %s", e)
                        if link_fault:
                            continue
                    elif mode == "calibrate":
                        # Free-move: keep follower at MIT zero torque; read both sides
                        # and expand min/max for later range mapping.
                        try:
                            js = (
                                self.master.get_fashionstar_joint_states()
                                if self.master
                                else None
                            )
                        except Exception as e:  # noqa: BLE001
                            if self._is_link_error(e):
                                self._enter_link_fault("master", e)
                                continue
                            raise
                        if js:
                            self.last_joint_states = deepcopy(js)
                            expand_ranges(self.cal_master_ranges, js)
                            self.frame_count += 1
                        # Continuous zero-torque frames (also refresh encoders).
                        link_fault = False
                        for slave in self.slaves:
                            try:
                                if hasattr(slave, "hold_free_move"):
                                    m = slave.hold_free_move()
                                    if m:
                                        self.last_measured_joint_states = deepcopy(m)
                                        break
                                else:
                                    self._cache_measured_from_slaves(poll=True)
                                    break
                            except Exception as e:  # noqa: BLE001
                                if self._is_link_error(e):
                                    self._enter_link_fault("slave", e)
                                    link_fault = True
                                    break
                                log.debug("hold_free_move: %s", e)
                        if link_fault:
                            continue
                        expand_ranges(
                            self.cal_slave_ranges,
                            self.last_measured_joint_states,
                        )
                    elif mode == "transition":
                        # Sample master so follow_first can preempt voice.
                        try:
                            mjs = (
                                self.master.get_fashionstar_joint_states()
                                if self.master
                                else None
                            )
                            if mjs:
                                self.last_joint_states = deepcopy(mjs)
                                self._notify_voice_master_js(mjs)
                        except Exception as e:  # noqa: BLE001
                            if self._is_link_error(e):
                                self._enter_link_fault("master", e)
                                continue
                            log.debug("master sample in transition: %s", e)
                        # Blend endpoints are already in slave/hardware space
                        # (see start_playback). Do NOT run _map_for_slave again.
                        js = self._update_transition()
                        if js:
                            self._broadcast_to_slaves(js)
                            out_js = js
                            self.frame_count += 1
                    elif mode == "playback":
                        try:
                            mjs = (
                                self.master.get_fashionstar_joint_states()
                                if self.master
                                else None
                            )
                            if mjs:
                                self.last_joint_states = deepcopy(mjs)
                                self._notify_voice_master_js(mjs)
                        except Exception as e:  # noqa: BLE001
                            if self._is_link_error(e):
                                self._enter_link_fault("master", e)
                                continue
                            log.debug("master sample in playback: %s", e)
                        js = self._update_playback()
                        if js:
                            self._broadcast_to_slaves(self._map_for_slave(js))
                            out_js = js
                            self.frame_count += 1
                    elif mode == "return_to_follow":
                        js = self._update_return_to_follow()
                        if js:
                            self._broadcast_to_slaves(self._map_for_slave(js))
                            out_js = js
                            self.frame_count += 1
                        if self.last_joint_states:
                            self._notify_voice_master_js(self.last_joint_states)
                    elif mode == "paused":
                        # Safety lockout: do not write to slave. Slave's PID
                        # holds the last commanded pose. Master is intentionally
                        # NOT polled so a child playing with it has zero effect
                        # on slave hardware.
                        pass

                    # Push snapshot to listeners at ws_push_hz (not every tick).
                    now = time.monotonic()
                    if now - last_push >= push_interval:
                        last_push = now
                        if mode not in ("calibrate", "free_move"):
                            # Always poll encoders for UI (POS_VEL feedback can miss
                            # under burst writes; refresh is the reliable read).
                            self._cache_measured_from_slaves(poll=True)
                        # calibrate: ranges already expanded each tick via hold_free_move
                        snap = self.snapshot()
                        for fn in self._listeners:
                            try:
                                fn(snap)
                            except Exception as e:  # noqa: BLE001
                                log.debug("listener error: %s", e)

                    sleep_time = max(0.0, update_interval - (time.monotonic() - loop_start))
                    time.sleep(sleep_time)

                except KeyboardInterrupt:
                    log.info("Control loop interrupted")
                    break
                except Exception as e:  # noqa: BLE001
                    if self._is_link_error(e):
                        # Attribute unknown link loss to both arms and reconnect.
                        self._enter_link_fault("both", e)
                        continue
                    log.exception("Unexpected error in control loop")
                    self.last_error = str(e)
                    time.sleep(0.5)
        finally:
            self.cleanup()
