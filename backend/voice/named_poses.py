# -*- coding: utf-8 -*-
"""Named pose persistence for voice goto (V2)."""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_\u4e00-\u9fff\-]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(name: str) -> str:
    s = _SLUG_RE.sub("_", name.strip()).strip("_")
    return s or uuid.uuid4().hex[:8]


class NamedPoseStore:
    """JSON file of named poses under recordings/named_poses.json."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "named_poses.json"
        self._lock = threading.RLock()
        if not self.path.exists():
            self._write({"poses": {}})

    def _read(self) -> dict[str, Any]:
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"poses": {}}
        poses = data.get("poses")
        if not isinstance(poses, dict):
            data["poses"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            poses = self._read().get("poses", {})
            items = list(poses.values())
            items.sort(key=lambda p: p.get("updated_at") or p.get("created_at") or "")
            return items

    def get(self, pose_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read().get("poses", {}).get(pose_id)

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        needle = name.strip().casefold()
        if not needle:
            return None
        with self._lock:
            for pose in self._read().get("poses", {}).values():
                if str(pose.get("name", "")).strip().casefold() == needle:
                    return pose
                for alias in pose.get("aliases") or []:
                    if str(alias).strip().casefold() == needle:
                        return pose
                if str(pose.get("id", "")).strip().casefold() == needle:
                    return pose
        return None

    def upsert(
        self,
        *,
        name: str,
        joint_states: dict[str, float],
        pose_id: str | None = None,
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("pose name required")
        if not joint_states:
            raise ValueError("joint_states required")
        with self._lock:
            data = self._read()
            poses: dict[str, Any] = data.setdefault("poses", {})
            pid = (pose_id or _slug(name)).strip()
            now = _now_iso()
            prev = poses.get(pid) or {}
            pose = {
                "id": pid,
                "name": name.strip(),
                "aliases": list(aliases) if aliases is not None else list(prev.get("aliases") or []),
                "joint_states": {k: float(v) for k, v in joint_states.items()},
                "created_at": prev.get("created_at") or now,
                "updated_at": now,
            }
            poses[pid] = pose
            self._write(data)
            log.info("Named pose saved: %s (%s)", pid, pose["name"])
            return pose

    def delete(self, pose_id: str) -> bool:
        with self._lock:
            data = self._read()
            poses = data.get("poses", {})
            if pose_id not in poses:
                return False
            del poses[pose_id]
            self._write(data)
            return True
