"""Arm profile data types (no I/O)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

ArmRole = Literal["leader", "follower"]

# Feature flags consumed by UI / controller in later phases.
Capability = Literal[
    "read_joints",
    "write_joints",
    "calibration",
    "motor_map",
    "free_move",
    "named_poses",
]

# Phase 2: (port_device: str, hints) -> bool | raises
DetectorFn = Callable[..., bool]
# Phase 4: (**kwargs) -> driver instance
DriverFactory = Callable[..., Any]


@dataclass(frozen=True)
class UsbMatchHint:
    """USB enumeration clues for auto-detect (phase 2).

    Matching is OR across hints on a profile; within one hint, set fields are AND.
    """

    vid: Optional[int] = None
    pid: Optional[int] = None
    manufacturer_contains: Optional[str] = None  # case-insensitive substring
    product_prefix: Optional[str] = None  # case-insensitive prefix on product string
    note: str = ""


@dataclass(frozen=True)
class ArmProfile:
    """Registered arm model.

    ``detector`` / ``driver_factory`` are placeholders until detect (phase 2)
    and adapter (phase 4) land. Callers must treat ``None`` as “not wired yet”.
    """

    id: str
    role: ArmRole
    label: str
    label_zh: str
    description: str = ""
    default_baudrate: Optional[int] = None
    usb_hints: tuple[UsbMatchHint, ...] = ()
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    # Placeholders — do not call until implemented.
    detector: Optional[DetectorFn] = None
    driver_factory: Optional[DriverFactory] = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly summary for future REST (phase 1.2)."""
        return {
            "id": self.id,
            "role": self.role,
            "label": self.label,
            "label_zh": self.label_zh,
            "description": self.description,
            "default_baudrate": self.default_baudrate,
            "capabilities": sorted(self.capabilities),
            "usb_hints": [
                {
                    "vid": f"{h.vid:04x}" if h.vid is not None else None,
                    "pid": f"{h.pid:04x}" if h.pid is not None else None,
                    "manufacturer_contains": h.manufacturer_contains,
                    "product_prefix": h.product_prefix,
                    "note": h.note,
                }
                for h in self.usb_hints
            ],
            "has_detector": self.detector is not None,
            "has_driver_factory": self.driver_factory is not None,
        }
