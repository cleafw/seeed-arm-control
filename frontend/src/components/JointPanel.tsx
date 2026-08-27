import { MODE_STYLE } from "../modeStyle";
import { portColor } from "../portColor";
import type { ControllerMode, MotorMapEntry } from "../types";
import { MotorMapTable } from "./MotorMapTable";

interface Props {
  joints: Record<string, number> | undefined;
  slaveJoints?: Record<string, number> | undefined;
  mode: ControllerMode;
  isSo101?: boolean;
  leaderPort?: string | null;
  followerPort?: string | null;
  /** When true, first bar is live master pose (calibrate), not command. */
  masterLabel?: boolean;
  motorMap?: Record<string, MotorMapEntry | string | null>;
  motorMapBlending?: boolean;
  onMotorMapChange?: () => void;
  onMotorMapSaved?: () => void;
  onMotorMapError?: (msg: string) => void;
}

const JOINTS: Array<{ key: string; label: string }> = [
  { key: "joint1", label: "J1" },
  { key: "joint2", label: "J2" },
  { key: "joint3", label: "J3" },
  { key: "joint4", label: "J4" },
  { key: "joint5", label: "J5" },
  { key: "joint6", label: "J6" },
  { key: "gripper", label: "GRIP" },
];

const TAU = Math.PI * 2;
function phaseDeg(v: number) {
  return ((((v % TAU) + TAU) % TAU) * 180) / Math.PI;
}

function fmt(v: number | undefined) {
  return typeof v === "number" && isFinite(v) ? v.toFixed(2) : "—";
}

export function JointPanel({
  joints,
  slaveJoints,
  mode,
  isSo101 = false,
  leaderPort,
  followerPort,
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
  const leaderColor = isSo101
    ? portColor(leaderPort, [leaderPort, followerPort])
    : accent;
  const followerColor = isSo101
    ? portColor(followerPort, [leaderPort, followerPort])
    : "var(--text-dim)";

  return (
    <aside className="joint-panel">
      <div className="joint-panel__top">
        <div className="joint-panel__header">关节遥测</div>
        <div className="joint-panel__legend">
          <span className="joint-panel__legend-item">
            <i
              className="joint-panel__swatch joint-panel__swatch--cmd"
              style={{ background: leaderColor }}
            />
            {isSo101 && leaderPort ? leaderPort : primaryLabel}
          </span>
          <span className="joint-panel__legend-item">
            <i className="joint-panel__swatch" style={{ background: followerColor }} />
            {isSo101 && followerPort ? followerPort : "从臂"}
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
        {(isSo101 ? JOINTS.filter((j) => j.key !== "joint6") : JOINTS).map((j) => {
          const c = cmd[j.key];
          const s = slave[j.key];
          const cmdOk = typeof c === "number" && isFinite(c);
          const slaveOk = typeof s === "number" && isFinite(s);
          return (
            <div className="joint-bar" key={j.key}>
              <div className="joint-bar__row">
                <span className="joint-bar__label">{j.label}</span>
                <span className="joint-bar__value">
                  <span style={{ color: cmdOk ? leaderColor : undefined }}>{fmt(c)}</span>
                  <span className="joint-bar__sep">/</span>
                  <span
                    className="joint-bar__slave-val"
                    style={{ color: slaveOk ? followerColor : undefined }}
                  >
                    {fmt(s)}
                  </span>
                </span>
              </div>
              <svg className="joint-ring" viewBox="0 0 48 48" role="img" aria-label={`${j.label} 循环角度`}>
                <circle className="joint-ring__track" cx="24" cy="24" r="17" />
                <circle className="joint-ring__track joint-ring__track--inner" cx="24" cy="24" r="11" />
                {cmdOk ? <g transform={`rotate(${phaseDeg(c as number)} 24 24)`} style={{ color: leaderColor }}><line className="joint-ring__hand" x1="24" y1="24" x2="24" y2="7" /><circle className="joint-ring__dot" cx="24" cy="7" r="2" /></g> : null}
                {slaveOk ? <g transform={`rotate(${phaseDeg(s as number)} 24 24)`} style={{ color: followerColor }}><line className="joint-ring__hand joint-ring__hand--inner" x1="24" y1="24" x2="24" y2="13" /><circle className="joint-ring__dot" cx="24" cy="13" r="1.6" /></g> : null}
                <circle className="joint-ring__hub" cx="24" cy="24" r="2" />
              </svg>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
