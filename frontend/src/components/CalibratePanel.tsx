import { useCallback, useState } from "react";
import { api } from "../api";
import type { CalibrationInfo, StateSnapshot } from "../types";

interface Props {
  snapshot: StateSnapshot | null;
  onToast: (kind: "ok" | "err" | "info" | "warn", msg: string) => void;
}

const JOINTS: Array<{ key: string; label: string }> = [
  { key: "joint1", label: "J1" },
  { key: "joint2", label: "J2" },
  { key: "joint3", label: "J3" },
  { key: "joint4", label: "J4" },
  { key: "joint5", label: "J5" },
  { key: "joint6", label: "J6" },
  { key: "gripper", label: "GR" },
];

const MIN_SPAN = 0.05;

function fmt(v: number | undefined) {
  return typeof v === "number" && isFinite(v) ? v.toFixed(2) : "—";
}

/** Position of current within [lo, hi] as 0..100%. Near-zero span → use wider pad. */
function pctInRange(v: number | undefined, lo: number, hi: number) {
  if (typeof v !== "number" || !isFinite(v)) return 50;
  let a = lo;
  let b = hi;
  if (b - a < MIN_SPAN) {
    const mid = (a + b) / 2;
    a = mid - 0.5;
    b = mid + 0.5;
  }
  return Math.max(0, Math.min(100, ((v - a) / (b - a)) * 100));
}

function SideTrack({
  label,
  lo,
  hi,
  current,
  accentClass,
}: {
  label: string;
  lo: number;
  hi: number;
  current: number | undefined;
  accentClass: string;
}) {
  const span = Math.max(0, hi - lo);
  const valid = span >= MIN_SPAN;
  const pct = pctInRange(current, lo, hi);
  const fillLeft = valid ? 0 : Math.max(0, pct - 2);
  const fillWidth = valid ? 100 : 4;
  return (
    <div className={`cal-track ${accentClass}`}>
      <div className="cal-track__meta">
        <span className="cal-track__side">{label}</span>
        <span className="cal-track__nums">
          [{fmt(lo)}, {fmt(hi)}]
          <span className={`cal-track__span${valid ? "" : " cal-track__span--weak"}`}>
            {" "}Δ{span.toFixed(2)}
            {valid ? "" : " 未扫满"}
          </span>
        </span>
      </div>
      <div className="cal-track__bar">
        <div
          className="cal-track__fill"
          style={{ left: `${fillLeft}%`, width: `${fillWidth}%`, opacity: valid ? 1 : 0.35 }}
        />
        <div
          className="cal-track__dot"
          style={{ left: `${pct}%` }}
          title={fmt(current)}
        />
      </div>
    </div>
  );
}

export function CalibratePanel({ snapshot, onToast }: Props) {
  const [busy, setBusy] = useState(false);
  const mode = snapshot?.mode ?? "follow";
  const active = mode === "calibrate";
  const cal: CalibrationInfo = snapshot?.calibration ?? {
    active: false,
    saved_at: null,
    master: {},
    slave: {},
  };
  const mappingOn = Boolean(cal.mapping_enabled);
  const ready = Boolean(cal.ready);
  const masterJs =
    snapshot?.master_joint_states ?? snapshot?.joint_states ?? {};
  const slaveJs = snapshot?.slave_joint_states ?? {};
  const canStart = mode === "follow" || mode === "paused" || mode === "idle" || mode === "free_move";
  const canFinish = active;
  // SO-ARM101 has five arm joints plus the gripper.  `joint6` remains in the
  // generic legacy model for other arm profiles, but is not a physical motor
  // on this profile and must not look like an incomplete calibration axis.
  const isSo101 =
    snapshot?.leader_profile === "so101_leader" &&
    snapshot?.follower_profile === "so101_follower";
  const displayedJoints = isSo101
    ? JOINTS.filter((joint) => joint.key !== "joint6")
    : JOINTS;
  const startLabel = cal.saved_at ? "重新校准" : "开始校准";

  const start = useCallback(async () => {
    setBusy(true);
    try {
      await api.startCalibrate();
      onToast(
        "info",
        "校准开始：从臂已零力矩/失能，请用手分别把主臂、从臂每个关节转到极限"
      );
    } catch (e) {
      onToast("err", `无法开始校准：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [onToast]);

  const finish = useCallback(async () => {
    setBusy(true);
    try {
      await api.finishCalibrate();
      onToast("ok", "校准已保存，跟随将按主→从范围映射");
    } catch (e) {
      onToast("err", `无法完成校准：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [onToast]);

  const cancel = useCallback(async () => {
    setBusy(true);
    try {
      await api.cancelCalibrate();
      onToast("info", "已取消校准：从臂保持自由，未跟随主臂");
    } catch (e) {
      onToast("err", `无法取消校准：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [onToast]);

  return (
    <section className={`cal-panel${active ? " cal-panel--active" : ""}`}>
      <div className="cal-panel__top">
        <div className="cal-panel__header">
          <span>关节校准</span>
          {mappingOn ? (
            <span className="cal-panel__saved" title={cal.saved_at ?? undefined}>
              映射已启用
            </span>
          ) : cal.saved_at ? (
            <span className="cal-panel__saved cal-panel__saved--none" title={cal.saved_at}>
              范围不足
            </span>
          ) : (
            <span className="cal-panel__saved cal-panel__saved--none">未校准</span>
          )}
        </div>
        {active ? (
          <div className="cal-panel__actions">
            <button
              type="button"
              className="cal-panel__btn cal-panel__btn--finish"
              disabled={busy || !canFinish}
              onClick={finish}
            >
              {busy ? "…" : "完成校准"}
            </button>
            <button
              type="button"
              className="cal-panel__btn cal-panel__btn--cancel"
              disabled={busy}
              onClick={cancel}
            >
              取消校准
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="cal-panel__btn"
            disabled={busy || !canStart}
            onClick={start}
          >
            {busy ? "…" : startLabel}
          </button>
        )}
      </div>

      <p className={`cal-panel__hint${!active && !mappingOn ? " cal-panel__hint--warn" : ""}`}>
        {active
          ? "从臂已切到 MIT 零力矩（无主动阻尼）。请分别拖动主臂与从臂，把每个关节扫到机械极限；条上 Δ 需≥0.05 才可完成。"
          : mappingOn
            ? "已按主/从各自范围做线性映射跟随。重新校准会覆盖保存的范围。"
            : "首次运行需要先校准：请点击「开始校准」，分别拖动主臂与从臂扫满各轴行程后再完成。"}
      </p>
      {!active && !mappingOn ? (
        <div className="cal-panel__notice" role="status">
          未检测到有效校准数据，请先完成关节校准后再正常遥操。
        </div>
      ) : null}
      {active && !ready ? (
        <p className="cal-panel__hint cal-panel__hint--warn">
          还有关节未扫满，请继续拖动直到各轴显示有效 Δ。
        </p>
      ) : null}

      <div className="cal-panel__joints">
        {displayedJoints.map((j) => {
          const mr = cal.master?.[j.key] ?? { min: 0, max: 0 };
          const sr = cal.slave?.[j.key] ?? { min: 0, max: 0 };
          return (
            <div className="cal-joint" key={j.key}>
              <div className="cal-joint__label">{j.label}</div>
              <div className="cal-joint__tracks">
                <SideTrack
                  label="主"
                  lo={mr.min}
                  hi={mr.max}
                  current={masterJs[j.key]}
                  accentClass="cal-track--master"
                />
                <SideTrack
                  label="从"
                  lo={sr.min}
                  hi={sr.max}
                  current={slaveJs[j.key]}
                  accentClass="cal-track--slave"
                />
              </div>
              <div className="cal-joint__now">
                <span>主 {fmt(masterJs[j.key])}</span>
                <span>从 {fmt(slaveJs[j.key])}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
