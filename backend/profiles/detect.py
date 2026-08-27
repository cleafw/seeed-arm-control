"""USB serial → arm profile matching (phase 2.1 slim)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import serial.tools.list_ports

from .registry import list_followers, list_leaders
from .types import ArmProfile, UsbMatchHint

log = logging.getLogger(__name__)


@dataclass
class ProfileMatch:
    profile_id: str
    port: str
    label: str = ""


@dataclass
class DetectResult:
    """Outcome of one detect pass (no side effects)."""

    status: str  # ok | partial | none | ambiguous
    message: str
    leader_id: Optional[str] = None
    follower_id: Optional[str] = None
    leader_port: Optional[str] = None
    follower_port: Optional[str] = None
    leader_candidates: list[ProfileMatch] = field(default_factory=list)
    follower_candidates: list[ProfileMatch] = field(default_factory=list)
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "leader_id": self.leader_id,
            "follower_id": self.follower_id,
            "leader_port": self.leader_port,
            "follower_port": self.follower_port,
            "leader_candidates": [
                {"profile_id": m.profile_id, "port": m.port, "label": m.label}
                for m in self.leader_candidates
            ],
            "follower_candidates": [
                {"profile_id": m.profile_id, "port": m.port, "label": m.label}
                for m in self.follower_candidates
            ],
            "applied": self.applied,
        }


def _hint_has_criteria(hint: UsbMatchHint) -> bool:
    return any(
        (
            hint.vid is not None,
            hint.pid is not None,
            bool(hint.manufacturer_contains),
            bool(hint.product_prefix),
        )
    )


def port_matches_hint(port: Any, hint: UsbMatchHint) -> bool:
    """AND within one hint; note-only hints never match."""
    if not _hint_has_criteria(hint):
        return False
    if hint.vid is not None and port.vid != hint.vid:
        return False
    if hint.pid is not None and port.pid != hint.pid:
        return False
    if hint.manufacturer_contains:
        mfr = (port.manufacturer or "").lower()
        if hint.manufacturer_contains.lower() not in mfr:
            return False
    if hint.product_prefix:
        product = (port.product or "").upper()
        if not product.startswith(hint.product_prefix.upper()):
            return False
    return True


def profile_matches_port(profile: ArmProfile, port: Any) -> bool:
    """OR across hints on a profile."""
    if not profile.usb_hints:
        return False
    return any(port_matches_hint(port, h) for h in profile.usb_hints)


def _collect_matches(role_profiles: list[ArmProfile], ports: list) -> list[ProfileMatch]:
    hits: list[ProfileMatch] = []
    for profile in role_profiles:
        for port in ports:
            if profile_matches_port(profile, port):
                hits.append(
                    ProfileMatch(
                        profile_id=profile.id,
                        port=port.device,
                        label=profile.label_zh or profile.label,
                    )
                )
    return hits


def _unique_by_profile(matches: list[ProfileMatch]) -> tuple[list[ProfileMatch], bool]:
    """Collapse to one entry per profile_id (first port). ambiguous if >1 profile."""
    by_id: dict[str, ProfileMatch] = {}
    for m in matches:
        if m.profile_id not in by_id:
            by_id[m.profile_id] = m
    unique = list(by_id.values())
    return unique, len(unique) > 1


def detect_arm_profiles(
    *,
    live_so101_ports: Optional[list[str]] = None,
    live_profiles: Optional[dict[str, list[str]]] = None,
) -> DetectResult:
    """Enumerate every USB serial port and report detected arm profiles.

    ``live_so101_ports`` is populated by the controller's read-only Feetech
    probe.  It is intentionally independent of the currently selected pair:
    a USB adapter is not considered an SO-ARM101 until IDs 1–6 answer a live
    position read.
    """
    ports = list(serial.tools.list_ports.comports())
    log.info("Profile detect: %d serial port(s)", len(ports))
    for p in ports:
        vid = f"{p.vid:04x}" if p.vid else "----"
        pid = f"{p.pid:04x}" if p.pid else "----"
        log.info(
            "  %s  %s:%s  mfr=%r  product=%r",
            p.device,
            vid,
            pid,
            p.manufacturer,
            p.product,
        )

    live_profiles = dict(live_profiles or {})
    if live_profiles:
        # Position-feedback probes are authoritative. Do not mix in a mere
        # USB VID/PID match once live probes have been supplied.
        leader_ids = {profile.id: profile for profile in list_leaders()}
        follower_ids = {profile.id: profile for profile in list_followers()}
        leaders_raw = [
            ProfileMatch(profile_id, port, leader_ids[profile_id].label_zh or leader_ids[profile_id].label)
            for profile_id, matches in live_profiles.items()
            if profile_id in leader_ids
            for port in matches
        ]
        followers_raw = [
            ProfileMatch(profile_id, port, follower_ids[profile_id].label_zh or follower_ids[profile_id].label)
            for profile_id, matches in live_profiles.items()
            if profile_id in follower_ids
            for port in matches
        ]
    else:
        leaders_raw = _collect_matches(list_leaders(), ports)
        followers_raw = _collect_matches(list_followers(), ports)
    live_so101_ports = list(dict.fromkeys(live_so101_ports or []))
    if live_so101_ports and not live_profiles:
        # A leader and follower have identical Feetech hardware.  The probe
        # establishes the model, while role assignment remains an explicit
        # port choice in the UI when two boards are present.
        leaders_raw.extend(
            ProfileMatch("so101_leader", port, "主臂 SO-ARM101")
            for port in live_so101_ports
        )
        followers_raw.extend(
            ProfileMatch("so101_follower", port, "从臂 SO-ARM101")
            for port in live_so101_ports
        )
    leaders, lead_amb = _unique_by_profile(leaders_raw)
    followers, fol_amb = _unique_by_profile(followers_raw)

    result = DetectResult(
        status="none",
        message="",
        leader_candidates=leaders,
        follower_candidates=followers,
    )

    if live_so101_ports:
        result.leader_id = "so101_leader"
        result.follower_id = "so101_follower"
        result.leader_port = live_so101_ports[0]
        result.follower_port = (
            live_so101_ports[1] if len(live_so101_ports) > 1 else None
        )
        result.status = "ok" if len(live_so101_ports) > 1 else "partial"
        ports_text = "、".join(live_so101_ports)
        result.message = (
            f"已通过关节位置读取发现 SO-ARM101：{ports_text}。"
            "主从角色请按端口选择。"
        )
        return result

    if lead_amb or fol_amb:
        result.status = "ambiguous"
        parts = []
        if lead_amb:
            parts.append(
                "主臂候选: " + ", ".join(f"{m.label}@{m.port}" for m in leaders)
            )
        if fol_amb:
            parts.append(
                "从臂候选: " + ", ".join(f"{m.label}@{m.port}" for m in followers)
            )
        result.message = "检测到多个匹配，无法唯一判定 — " + "；".join(parts)
        return result

    if len(leaders) == 1:
        result.leader_id = leaders[0].profile_id
        result.leader_port = leaders[0].port
    if len(followers) == 1:
        result.follower_id = followers[0].profile_id
        result.follower_port = followers[0].port

    so101_ports = list(dict.fromkeys(live_profiles.get("so101_leader", [])))
    if (
        result.leader_id == "so101_leader"
        and result.follower_id == "so101_follower"
    ):
        # Hardware is identical on both sides; preserve both physical ports
        # and leave role confirmation to the UI's explicit port selectors.
        result.leader_port = so101_ports[0]
        result.follower_port = so101_ports[1] if len(so101_ports) >= 2 else None

    if (
        result.leader_id == "so101_leader"
        and result.follower_id == "so101_follower"
        and len(so101_ports) < 2
    ):
        result.status = "partial"
        result.message = f"已读取 SO-ARM101 位置数据：{so101_ports[0]}；等待另一条总线"
        return result

    if result.leader_id and result.follower_id:
        result.status = "ok"
        result.message = (
            f"已识别 {leaders[0].label} @ {result.leader_port} → "
            f"{followers[0].label} @ {result.follower_port}"
        )
    elif result.leader_id or result.follower_id:
        result.status = "partial"
        missing = "从臂" if result.leader_id else "主臂"
        result.message = f"仅识别到{'主臂' if result.leader_id else '从臂'}，未找到{missing}"
    else:
        result.status = "none"
        result.message = "未检测到已登记的主臂/从臂串口（请确认接线与供电）"

    return result
