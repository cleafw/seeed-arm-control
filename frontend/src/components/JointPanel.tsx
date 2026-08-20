import { MODE_STYLE } from "../modeStyle";
import type { ControllerMode, MotorMapEntry } from "../types";
import { MotorMapTable } from "./MotorMapTable";

interface Props {
  joints: Record<string, number> | undefined;
  slaveJoints?: Record<string, number> | undefined;
  mode: ControllerMode;
  /** When true, first bar is live master pose (calibrate), not command. */
  masterLabel?: boolean;
  motorMap?: Record<string, MotorMapEntry | string | null>;
  motorMapBlending?: boolean;
  onMotorMapChange?: () => void;
  onMotorMapSaved?: () => void;
  onMotorMapError?: (msg: string) => void;
}

const JOINTS: Array<{ key: string; label: string; range: [number, number] }> = [
  { key: "joint1", label: "J1", range: [-2.6, 2.6] },
  { key: "joint2", label: "J2", range: [-1.8, 1.8] },
  { key: "joint3", label: "J3", range: [-2.6, 2.6] },
  { key: "joint4", label: "J4", range: [-1.8, 1.8] },
  { key: "joint5", label: "J5", range: [-2.6, 2.6] },
  { key: "joint6", label: "J6", range: [-1.8, 1.8] },
  { key: "gripper", label: "GRIP", range: [-1.2, 1.2] },
];

/** Highlight when |cmd − measured| exceeds this (rad; gripper uses same units). */
const LAG_WARN = 0.08;

function normalise(v: number, [lo, hi]: [number, number]) {
  if (hi === lo) return 0;
  return Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
}

function fmt(v: number | undefined) {
  return typeof v === "number" && isFinite(v) ? v.toFixed(2) : "—";
}

export function JointPanel({
  joints,
  slaveJoints,
  mode,
  masterLabel,
  motorMap,
  motorMapBlending: blending,
  onMotorMapChange,
  onMotorMapSaved,
  onMotorMapError,
}: Props) {
  const accent = MODE_STYLE[mode].accent;
  const cmd = joints ?? {};
  const slave = slaveJoints ?? {};
  const primaryLabel = masterLabel ? "主臂" : "命令";

  return (
    <aside className="joint-panel">
      <div className="joint-panel__top">
        <div className="joint-panel__header">关节遥测</div>
        <div className="joint-panel__legend">
          <span className="joint-panel__legend-item">
            <i
              className="joint-panel__swatch joint-panel__swatch--cmd"
              style={{ background: accent }}
            />
            {primaryLabel}
          </span>
          <span className="joint-panel__legend-item">
            <i className="joint-panel__swatch joint-panel__swatch--slave" />
            从臂
          </span>
        </div>
      </div>
      <MotorMapTable
        motorMap={motorMap}
        blending={blending}
        onChange={onMotorMapChange}
        onSaved={onMotorMapSaved}
        onError={onMotorMapError}
      />
      <div className="joint-panel__grid">
        {JOINTS.map((j) => {
          const c = cmd[j.key];
          const s = slave[j.key];
          const cmdOk = typeof c === "number" && isFinite(c);
          const slaveOk = typeof s === "number" && isFinite(s);
          const cmdNorm = cmdOk ? normalise(c, j.range) : 0;
          const slaveNorm = slaveOk ? normalise(s, j.range) : 0;
          const lag =
            cmdOk && slaveOk ? Math.abs((c as number) - (s as number)) : 0;
          const lagging = !masterLabel && lag > LAG_WARN;
          return (
            <div
              className={`joint-bar${lagging ? " joint-bar--lag" : ""}`}
              key={j.key}
            >
              <div className="joint-bar__row">
                <span className="joint-bar__label">{j.label}</span>
                <span className="joint-bar__value">
                  {fmt(c)}
                  <span className="joint-bar__sep">/</span>
                  <span
                    className={
                      lagging
                        ? "joint-bar__slave-val joint-bar__slave-val--lag"
                        : "joint-bar__slave-val"
                    }
                  >
                    {fmt(s)}
                  </span>
                  {lagging ? (
                    <span className="joint-bar__delta"> Δ{lag.toFixed(2)}</span>
                  ) : null}
                </span>
              </div>
              <div className="joint-bar__tracks">
                <div className="joint-bar__track" title={primaryLabel}>
                  <div
                    className="joint-bar__fill"
                    style={{
                      width: `${cmdNorm * 100}%`,
                      background: cmdOk ? accent : "var(--text-faint)",
                    }}
                  />
                  {j.range[0] < 0 && j.range[1] > 0 && (
                    <div className="joint-bar__center" />
                  )}
                </div>
                <div
                  className="joint-bar__track joint-bar__track--slave"
                  title="从臂实测"
                >
                  <div
                    className="joint-bar__fill"
                    style={{
                      width: `${slaveNorm * 100}%`,
                      background: slaveOk
                        ? lagging
                          ? "var(--danger, #e85d5d)"
                          : "var(--text-dim)"
                        : "var(--text-faint)",
                    }}
                  />
                  {j.range[0] < 0 && j.range[1] > 0 && (
                    <div className="joint-bar__center" />
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
