"""Arm profile registry — supported leader/follower models (phase 1.1).

Later phases wire detectors (1.2/2.x) and driver factories (4.x) into the
placeholders defined on each :class:`ArmProfile`.
"""
from __future__ import annotations

from .registry import (
    REGISTRY,
    ProfileError,
    get_profile,
    list_followers,
    list_leaders,
    list_profiles,
    pair_id,
    register,
)
from .types import ArmProfile, ArmRole, Capability, UsbMatchHint
from .active import load_active_profiles, save_active_profiles

# Side-effect: register built-in profiles on import.
from . import builtin as _builtin  # noqa: F401

__all__ = [
    "ArmProfile",
    "ArmRole",
    "Capability",
    "ProfileError",
    "REGISTRY",
    "UsbMatchHint",
    "get_profile",
    "list_followers",
    "list_leaders",
    "list_profiles",
    "load_active_profiles",
    "pair_id",
    "register",
    "save_active_profiles",
]
