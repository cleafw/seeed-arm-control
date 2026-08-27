"""Seeed SO-ARM101 / Feetech STS3215 bus adapter.

Both leader feedback and follower control use the manufacturer's position
servo mode.  Circular math is handled on the host without changing the
servo's persistent operating mode.
"""
from __future__ import annotations

import math
import threading
from typing import Final

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode

JOINT_KEYS: Final = ("joint1", "joint2", "joint3", "joint4", "joint5", "gripper")
_ENCODER_TICKS_PER_TURN: Final = 4096
# A relative command is deliberately kept small.  Apart from making recovery
# gentler, this prevents a malformed mapping from becoming one large hardware
# step in a single control frame.
_MAX_POSITION_STEP_TICKS: Final = round(_ENCODER_TICKS_PER_TURN * 12.0 / 360.0)


class SO101Arm:
    """Six-servo SO-ARM101 link with explicit, calibration-gated control."""

    def __init__(
        self, port: str, *, name: str, configure_position_mode: bool = False
    ) -> None:
        self.port = port
        self.name = name
        self._configure_position_mode = configure_position_mode
        self._motors = {
            key: Motor(index, "sts3215", MotorNormMode.DEGREES)
            for index, key in enumerate(JOINT_KEYS, start=1)
        }
        self._bus = FeetechMotorsBus(port=port, motors=self._motors)
        self._connected = False
        self._io_lock = threading.Lock()
        self._last_joint_states: dict[str, float] | None = None
        self._last_raw_positions: dict[str, int] = {}
        self._last_wrapped_angles: dict[str, float] = {}
        self._unwrapped_angles: dict[str, float] = {}
        self.last_warning: str | None = None
        self._protected_keys: set[str] = set()
        self._boundary_blocked_keys: set[str] = set()
        self._torque_enabled = False

    def setup(self) -> None:
        # handshake=False avoids any setup or torque write.  The subsequent
        # read in check_link is the only evidence accepted as a live arm.
        with self._io_lock:
            self._bus.connect(handshake=False)
            self._connected = True
            if self._configure_position_mode:
                self._ensure_position_mode_locked()
            self._read_joint_states_locked()

    @staticmethod
    def _raw_to_radians(raw: int | float) -> float:
        """Convert one STS3215 turn to its canonical [-π, π) angle."""
        return ((float(raw) % _ENCODER_TICKS_PER_TURN) / _ENCODER_TICKS_PER_TURN) * math.tau - math.pi

    def _unwrap_position(self, key: str, raw: int | float) -> float:
        """Keep a circular encoder continuous when it crosses 0 / 4095.

        The first sample establishes the display branch.  Every later sample
        adds the shortest signed movement, so 4095 → 0 is a tiny positive step
        rather than an almost-full-turn jump from the right end to the left.
        """
        wrapped = self._raw_to_radians(raw)
        previous = self._last_wrapped_angles.get(key)
        if previous is None:
            continuous = wrapped
        else:
            delta = (wrapped - previous + math.pi) % math.tau - math.pi
            continuous = self._unwrapped_angles[key] + delta
        self._last_wrapped_angles[key] = wrapped
        self._unwrapped_angles[key] = continuous
        return continuous

    def read_joint_states(self) -> dict[str, float]:
        with self._io_lock:
            return self._read_joint_states_locked()

    def _read_joint_states_locked(self) -> dict[str, float]:
        if not self._connected:
            raise RuntimeError(f"{self.name} is not connected")
        raw: dict[str, int | float] = {}
        protected: list[str] = []
        first_transport_error: Exception | None = None
        for key in JOINT_KEYS:
            try:
                raw[key] = self._bus.read("Present_Position", key, normalize=False)
                self._protected_keys.discard(key)
            except Exception as exc:  # noqa: BLE001
                message = str(exc).lower()
                if "overload error" in message or "input voltage error" in message:
                    self._protected_keys.add(key)
                    protected.append(key)
                elif first_transport_error is None:
                    first_transport_error = exc

        # A protection bit on one servo is not a serial disconnection.  At
        # least one fresh position response proves the bus is alive; retain
        # the last known value for a protected axis and expose a clear warning.
        if not raw:
            if first_transport_error is not None:
                raise first_transport_error
            raise RuntimeError(f"{self.name}: no servo position response")

        states: dict[str, float] = {}
        for key, value in raw.items():
            self._last_raw_positions[key] = int(value) % _ENCODER_TICKS_PER_TURN
            states[key] = self._unwrap_position(key, value)
        if self._last_joint_states:
            for key in protected:
                if key in self._last_joint_states:
                    states[key] = self._last_joint_states[key]
        self._last_joint_states = states
        if protected:
            labels = ", ".join(key.replace("joint", "J") for key in protected)
            self.last_warning = f"{self.name} 舵机保护告警（{labels} 过载/电压），该轴已暂停命令"
        elif self._boundary_blocked_keys:
            labels = ", ".join(
                key.replace("joint", "J") for key in sorted(self._boundary_blocked_keys)
            )
            self.last_warning = (
                f"{self.name} {labels} 已到编码器零点边界；为防止反转整圈，该轴已暂停命令，请重新校准置中"
            )
        else:
            self.last_warning = None
        return dict(states)

    def check_link(self) -> None:
        # Reading all IDs verifies a complete arm, not merely a USB adapter.
        self.read_joint_states()

    def get_fashionstar_joint_states(self) -> dict[str, float]:
        return self.read_joint_states()

    def get_measured_joint_states(self) -> dict[str, float]:
        return self.read_joint_states()

    def poll_measured_joint_states(self) -> dict[str, float]:
        return self.read_joint_states()

    def disable_all(self) -> None:
        with self._io_lock:
            # Do not use LeRobot's convenience method here: in v0.6 it routes
            # through its *motor angle* calibration guard even for this raw
            # torque register.  This app owns range calibration separately;
            # Torque_Enable is a raw STS3215 byte and needs no angle mapping.
            self._bus.sync_write(
                "Torque_Enable", {key: 0 for key in JOINT_KEYS}, normalize=False
            )
            self._torque_enabled = False

    def enter_free_move(self) -> None:
        """Calibration state: explicitly release follower torque."""
        self.disable_all()

    def hold_free_move(self) -> dict[str, float]:
        return self.read_joint_states()

    def enable_all(self) -> None:
        with self._io_lock:
            self._ensure_position_mode_locked()
            self._read_joint_states_locked()
            # See disable_all(): keep the torque write independent of the
            # FeetechMotorsBus calibration registry.
            self._bus.sync_write(
                "Torque_Enable",
                {key: 1 for key in JOINT_KEYS if key not in self._protected_keys},
                normalize=False,
            )
            self._torque_enabled = True

    def _ensure_position_mode_locked(self) -> None:
        """Restore the official STS3215 absolute position-servo mode.

        Operating mode is persistent EEPROM state.  An earlier implementation
        changed followers to mode 3, in which this hardware returned a fixed
        zero while hand-moved.  Always repair that state before accepting
        feedback or enabling torque.
        """
        for key in JOINT_KEYS:
            mode = int(self._bus.read("Operating_Mode", key, normalize=False))
            if mode == OperatingMode.POSITION.value:
                continue
            self._bus.write("Torque_Enable", key, 0, normalize=False)
            self._bus.write("Lock", key, 0, normalize=False)
            self._bus.write(
                "Operating_Mode", key, OperatingMode.POSITION.value, normalize=False
            )
            verified = int(self._bus.read("Operating_Mode", key, normalize=False))
            if verified != OperatingMode.POSITION.value:
                raise RuntimeError(
                    f"{self.name} {key}: failed to restore STS3215 position mode"
                )
            self._bus.write("Lock", key, 1, normalize=False)

    @staticmethod
    def _radians_to_raw(value: float) -> int:
        return int(round((value + math.pi) / math.tau * _ENCODER_TICKS_PER_TURN)) % _ENCODER_TICKS_PER_TURN

    @staticmethod
    def _shortest_tick_delta(source: int, target: int) -> int:
        return (target - source + _ENCODER_TICKS_PER_TURN // 2) % _ENCODER_TICKS_PER_TURN - _ENCODER_TICKS_PER_TURN // 2

    def send_joint_states(self, joint_states: dict[str, float]) -> None:
        """Write calibrated follower targets only after torque was explicitly enabled.

        Goal_Position is an absolute mode-0 encoder target.  Each update is
        limited to a short circular delta.  A command that would cross the raw
        0/4095 boundary is withheld because this firmware can interpret that
        crossing as a nearly full reverse turn; calibration/homing must keep
        the mechanical range away from that boundary.
        """
        if not self._torque_enabled:
            raise RuntimeError("SO-ARM101 follower torque is disabled; calibrate before following")
        with self._io_lock:
            if not self._last_raw_positions:
                self._read_joint_states_locked()
            goals: dict[str, int] = {}
            self._boundary_blocked_keys.clear()
            for key, value in joint_states.items():
                if (
                    key not in self._motors
                    or key in self._protected_keys
                    or not isinstance(value, (int, float))
                    or key not in self._last_raw_positions
                ):
                    continue
                current = self._last_raw_positions[key]
                target = self._radians_to_raw(float(value))
                delta = self._shortest_tick_delta(current, target)
                delta = max(-_MAX_POSITION_STEP_TICKS, min(_MAX_POSITION_STEP_TICKS, delta))
                candidate = current + delta
                if candidate < 0 or candidate >= _ENCODER_TICKS_PER_TURN:
                    self._boundary_blocked_keys.add(key)
                    continue
                if delta:
                    goals[key] = candidate
            if goals:
                self._bus.sync_write("Goal_Position", goals, normalize=False)

    def recover(self) -> None:
        """Verify the link only; torque must be enabled by an explicit resume."""
        self.check_link()

    def safe_shutdown(self) -> None:
        try:
            self.disable_all()
        finally:
            self.close()

    def close(self) -> None:
        # Do not call FeetechMotorsBus.disconnect(): it writes Torque_Enable=0
        # to every configured ID.  Closing the serial transport is enough.
        with self._io_lock:
            self._connected = False
            self._bus.port_handler.closePort()
