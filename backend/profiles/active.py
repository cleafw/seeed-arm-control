"""Persist active leader/follower profile selection (phase 1.3)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ACTIVE_FILENAME = "active_profiles.json"
ACTIVE_PORTS_FILENAME = "active_ports.json"
SCHEMA_VERSION = 1


def active_profiles_path(recordings_dir: Path) -> Path:
    return recordings_dir / ACTIVE_FILENAME


def active_ports_path(recordings_dir: Path) -> Path:
    return recordings_dir / ACTIVE_PORTS_FILENAME


def load_active_ports(recordings_dir: Path) -> Optional[tuple[str, str]]:
    path = active_ports_path(recordings_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        leader = str(data["leader_port"]).strip()
        follower = str(data["follower_port"]).strip()
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        log.warning("Failed to read %s: %s", path, exc)
        return None
    return (leader, follower) if leader and follower and leader != follower else None


def save_active_ports(recordings_dir: Path, leader_port: str, follower_port: str) -> Path:
    recordings_dir.mkdir(parents=True, exist_ok=True)
    path = active_ports_path(recordings_dir)
    tmp = path.with_suffix(".json.tmp")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "leader_port": leader_port,
        "follower_port": follower_port,
    }
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    log.info("Saved active ports → %s (leader=%s follower=%s)", path, leader_port, follower_port)
    return path


def load_active_profiles(recordings_dir: Path) -> Optional[tuple[str, str]]:
    """Return (leader_id, follower_id) from disk, or None if missing/invalid JSON."""
    path = active_profiles_path(recordings_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Failed to read %s: %s", path, e)
        return None
    leader = data.get("leader_profile")
    follower = data.get("follower_profile")
    if not isinstance(leader, str) or not isinstance(follower, str):
        log.warning("Invalid active_profiles.json shape in %s", path)
        return None
    leader, follower = leader.strip(), follower.strip()
    if not leader or not follower:
        return None
    return leader, follower


def save_active_profiles(
    recordings_dir: Path,
    leader_id: str,
    follower_id: str,
    *,
    pair_id: str,
) -> Path:
    """Atomic-ish write of the active pairing selection."""
    recordings_dir.mkdir(parents=True, exist_ok=True)
    path = active_profiles_path(recordings_dir)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "leader_profile": leader_id,
        "follower_profile": follower_id,
        "pair_id": pair_id,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    log.info("Saved active profiles → %s (%s)", path, pair_id)
    return path
