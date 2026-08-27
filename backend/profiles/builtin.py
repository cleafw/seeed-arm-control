"""Built-in arm profiles for phase 1 (registry only; no live detect/drivers)."""
from __future__ import annotations

from .registry import register
from .types import ArmProfile, UsbMatchHint

# ---- leader ----

VIOLIN_102 = ArmProfile(
    id="violin_102",
    role="leader",
    label="StarAi Violin / Arm-102",
    label_zh="主臂 Violin / Arm-102",
    description="FashionStar UART leader via motorbridge-smart-servo (1M baud).",
    default_baudrate=1_000_000,
    usb_hints=(
        UsbMatchHint(
            vid=0x1A86,
            pid=0x7523,
            note="QinHeng CH340 serial converter",
        ),
    ),
    capabilities=frozenset({"read_joints"}),
    detector=None,  # phase 2
    driver_factory=None,  # phase 4 → wrap PiPER_MateAgilex
)

# Seeed SO-ARM101 kit = so101_leader + so101_follower (LeRobot teleop.type / robot.type).
SO101_LEADER = ArmProfile(
    id="so101_leader",
    role="leader",
    label="SO-ARM101 Leader",
    label_zh="主臂 SO-ARM101",
    description=(
        "Seeed / TheRobotStudio SO-ARM101 leader (Feetech ST3215). "
        "LeRobot teleop.type=so101_leader. Driver wired in phase 4."
    ),
    default_baudrate=1_000_000,
    usb_hints=(
        UsbMatchHint(
            note="Bus Servo Driver Board USB-serial; confirm with lerobot-find-port",
        ),
    ),
    capabilities=frozenset({"read_joints"}),
    detector=None,
    driver_factory=None,
)

# ---- followers ----

B601_DM = ArmProfile(
    id="b601_dm",
    role="follower",
    label="reBot B601-DM",
    label_zh="从臂 B601-DM",
    description="Damiao motors over HDSC CDC / u2can DM_CAN.",
    default_baudrate=921_600,
    usb_hints=(
        UsbMatchHint(
            manufacturer_contains="HDSC",
            product_prefix="CDC",
            note="HDSC CDC Damiao serial bridge",
        ),
        UsbMatchHint(
            vid=0x1A86,
            pid=0x55D3,
            note="Optional CH343 path on some units",
        ),
    ),
    capabilities=frozenset(
        {"write_joints", "calibration", "motor_map", "free_move"}
    ),
    detector=None,
    driver_factory=None,  # phase 4 → wrap SlaveArm
)

SO101_FOLLOWER = ArmProfile(
    id="so101_follower",
    role="follower",
    label="SO-ARM101 Follower",
    label_zh="从臂 SO-ARM101",
    description=(
        "Seeed / TheRobotStudio SO-ARM101 follower (Feetech ST3215). "
        "LeRobot robot.type=so101_follower. Driver wired in phase 4."
    ),
    default_baudrate=1_000_000,
    usb_hints=(
        # Exact VID/PID varies by USB-serial adapter; refine in phase 2.
        UsbMatchHint(
            note="Bus Servo Driver Board USB-serial; confirm with lerobot-find-port",
        ),
    ),
    capabilities=frozenset({"write_joints", "named_poses", "free_move"}),
    detector=None,
    driver_factory=None,
)


def load_builtins() -> None:
    register(VIOLIN_102)
    register(SO101_LEADER)
    register(B601_DM)
    register(SO101_FOLLOWER)


load_builtins()
