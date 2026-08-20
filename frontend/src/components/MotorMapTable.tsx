import { useEffect, useState } from "react";
import { api } from "../api";
import type { MotorMapEntry } from "../types";

const JOINTS: Array<{ key: string; label: string }> = [
  { key: "joint1", label: "J1" },
  { key: "joint2", label: "J2" },
  { key: "joint3", label: "J3" },
  { key: "joint4", label: "J4" },
  { key: "joint5", label: "J5" },
  { key: "joint6", label: "J6" },
  { key: "gripper", label: "GR" },
];

const NONE = "__none__";
const LABEL: Record<string, string> = Object.fromEntries(
  JOINTS.map((j) => [j.key, j.label]),
);

type LocalMap = Record<string, MotorMapEntry>;

function defaultMap(): LocalMap {
  const m: LocalMap = {};
  for (const j of JOINTS) m[j.key] = { slave: j.key, dir: 1 };
  return m;
}

function normalizeIncoming(
  motorMap?: Record<string, MotorMapEntry | string | null>,
): LocalMap {
  const next = defaultMap();
  if (!motorMap || typeof motorMap !== "object") return next;
  for (const j of JOINTS) {
    if (!(j.key in motorMap)) continue;
    const v = motorMap[j.key];
    if (v === null || v === undefined) {
      next[j.key] = { slave: null, dir: 1 };
    } else if (typeof v === "string") {
      next[j.key] = { slave: v, dir: 1 };
    } else {
      next[j.key] = {
        slave: v.slave === undefined ? j.key : v.slave,
        dir: v.dir === -1 ? -1 : 1,
      };
    }
  }
  return next;
}

/** Enforce one-to-one: if `slave` is taken by another master, clear that master. */
function exclusiveAssign(
  current: LocalMap,
  masterKey: string,
  slaveKey: string | null,
): LocalMap {
  const next: LocalMap = { ...current };
  for (const j of JOINTS) {
    next[j.key] = { ...current[j.key] };
  }
  if (slaveKey !== null) {
    for (const j of JOINTS) {
      if (j.key !== masterKey && next[j.key].slave === slaveKey) {
        next[j.key] = { ...next[j.key], slave: null };
      }
    }
  }
  next[masterKey] = { ...next[masterKey], slave: slaveKey };
  return next;
}

function mapEqual(a: LocalMap, b: LocalMap): boolean {
  for (const j of JOINTS) {
    if (a[j.key].slave !== b[j.key].slave) return false;
    if (a[j.key].dir !== b[j.key].dir) return false;
  }
  return true;
}

function confirmMessage(
  prev: LocalMap,
  next: LocalMap,
  masterKey: string,
): string {
  const master = LABEL[masterKey] ?? masterKey;
  const to = next[masterKey].slave;
  const toLabel = to === null ? "无" : LABEL[to] ?? to;
  const dirLabel = next[masterKey].dir < 0 ? "反向" : "正向";
  const lines = [
    `确认修改电机映射？`,
    ``,
    `主臂 ${master} → 从臂 ${toLabel}（${dirLabel}）`,
  ];
  for (const j of JOINTS) {
    if (j.key === masterKey) continue;
    if (prev[j.key].slave !== next[j.key].slave) {
      const cleared =
        next[j.key].slave === null
          ? "无"
          : LABEL[next[j.key].slave!] ?? next[j.key].slave;
      lines.push(`（主臂 ${j.label} 将改为「${cleared}」，避免一对多）`);
    }
  }
  lines.push(``, `确认后从臂将缓慢移动到新映射位置，请确认周围安全。`);
  return lines.join("\n");
}

interface Props {
  motorMap?: Record<string, MotorMapEntry | string | null>;
  blending?: boolean;
  onChange?: () => void;
  onSaved?: () => void;
  onError?: (msg: string) => void;
}

export function MotorMapTable({
  motorMap,
  blending,
  onChange,
  onSaved,
  onError,
}: Props) {
  const [local, setLocal] = useState<LocalMap>(defaultMap);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    // Only sync from server when we actually have a payload — avoid
    // briefly flashing identity defaults before the first WS snapshot.
    if (motorMap && typeof motorMap === "object") {
      setLocal(normalizeIncoming(motorMap));
    }
  }, [motorMap]);

  async function commit(next: LocalMap) {
    setLocal(next);
    setSaving(true);
    try {
      await api.setMotorMap(next);
      onSaved?.();
      onChange?.();
    } catch (e) {
      onError?.(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  function onSelectSlave(masterKey: string, value: string) {
    const slave = value === NONE ? null : value;
    const next = exclusiveAssign(local, masterKey, slave);
    if (mapEqual(next, local)) return;
    // Mapping to「无」is low-risk (hold pose) — no confirm dialog.
    if (slave !== null) {
      if (!window.confirm(confirmMessage(local, next, masterKey))) {
        return;
      }
    }
    void commit(next);
  }

  function onSelectDir(masterKey: string, value: string) {
    const dir: 1 | -1 = value === "-1" ? -1 : 1;
    if (local[masterKey].dir === dir) return;
    const next: LocalMap = { ...local };
    for (const j of JOINTS) next[j.key] = { ...local[j.key] };
    next[masterKey] = { ...next[masterKey], dir };
    // Direction flip moves the arm when mapped — confirm. Unmapped: no dialog.
    if (next[masterKey].slave !== null) {
      if (!window.confirm(confirmMessage(local, next, masterKey))) {
        return;
      }
    }
    void commit(next);
  }

  const busy = saving || !!blending;

  return (
    <div className="motor-map">
      <div className="motor-map__header">
        <span>电机映射</span>
        {blending ? (
          <span className="motor-map__saving">缓移中…</span>
        ) : saving ? (
          <span className="motor-map__saving">保存中…</span>
        ) : null}
      </div>
      <div className="motor-map__cols">
        <span className="motor-map__col-h">主臂</span>
        <span className="motor-map__col-h">从臂</span>
        <span className="motor-map__col-h">方向</span>
      </div>
      <div className="motor-map__rows">
        {JOINTS.map((j) => {
          const entry = local[j.key];
          const selectValue =
            entry.slave === null ? NONE : entry.slave ?? j.key;
          return (
            <div className="motor-map__row" key={j.key}>
              <span className="motor-map__master">{j.label}</span>
              <select
                className="motor-map__select"
                value={selectValue}
                disabled={busy}
                aria-label={`${j.label} 从臂映射`}
                onChange={(e) => onSelectSlave(j.key, e.target.value)}
              >
                {JOINTS.map((opt) => (
                  <option key={opt.key} value={opt.key}>
                    {opt.label}
                  </option>
                ))}
                <option value={NONE}>无</option>
              </select>
              <select
                className="motor-map__select motor-map__select--dir"
                value={entry.dir < 0 ? "-1" : "1"}
                disabled={busy || entry.slave === null}
                aria-label={`${j.label} 方向`}
                onChange={(e) => onSelectDir(j.key, e.target.value)}
              >
                <option value="1">正向</option>
                <option value="-1">反向</option>
              </select>
            </div>
          );
        })}
      </div>
    </div>
  );
}
