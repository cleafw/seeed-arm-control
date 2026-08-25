"""In-process registry of :class:`ArmProfile` entries."""
from __future__ import annotations

from typing import Iterable, Optional

from .types import ArmProfile, ArmRole

# id → profile (mutable module map; treat as read-only after builtin load)
REGISTRY: dict[str, ArmProfile] = {}


class ProfileError(KeyError):
    """Unknown or invalid arm profile id."""


def register(profile: ArmProfile, *, replace: bool = False) -> None:
    """Register a profile. Raises if id already exists unless ``replace``."""
    if not profile.id:
        raise ValueError("ArmProfile.id must be non-empty")
    if profile.id in REGISTRY and not replace:
        raise ValueError(f"ArmProfile already registered: {profile.id}")
    REGISTRY[profile.id] = profile


def get_profile(profile_id: str) -> ArmProfile:
    try:
        return REGISTRY[profile_id]
    except KeyError as e:
        raise ProfileError(f"unknown arm profile: {profile_id}") from e


def list_profiles(role: Optional[ArmRole] = None) -> list[ArmProfile]:
    items: Iterable[ArmProfile] = REGISTRY.values()
    if role is not None:
        items = (p for p in items if p.role == role)
    return sorted(items, key=lambda p: p.id)


def list_leaders() -> list[ArmProfile]:
    return list_profiles(role="leader")


def list_followers() -> list[ArmProfile]:
    return list_profiles(role="follower")


def pair_id(leader_id: str, follower_id: str) -> str:
    """Stable pairing key: ``{leader}__{follower}`` (phase 2 store)."""
    if not leader_id or not follower_id:
        raise ValueError("leader_id and follower_id required")
    # Ensure both are known (catches typos early).
    leader = get_profile(leader_id)
    follower = get_profile(follower_id)
    if leader.role != "leader":
        raise ValueError(f"{leader_id} is not a leader profile")
    if follower.role != "follower":
        raise ValueError(f"{follower_id} is not a follower profile")
    return f"{leader.id}__{follower.id}"
