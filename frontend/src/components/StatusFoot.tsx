import { useState } from "react";
import { api } from "../api";
import type { ArmLinkStatus, ArmStatus, StateSnapshot } from "../types";

interface Props {
  connected: boolean;
  snapshot: StateSnapshot | null;
}

const STATUS_LABEL: Record<ArmLinkStatus, string> = {
  ok: "正常",
  missing: "未接入",
  error: "串口异常",
  reconnecting: "重连中",
  initializing: "初始化中",
};

function armDotClass(status: ArmLinkStatus | undefined): string {
  if (status === "ok") return "foot-bar__dot--ok";
  if (status === "reconnecting" || status === "initializing") {
    return "foot-bar__dot--warn";
  }
  if (status === "error" || status === "missing") return "foot-bar__dot--err";
  return "foot-bar__dot--off";
}

function ArmChip({ arm }: { arm: ArmStatus | undefined }) {
  if (!arm) {
    return (
      <div className="arm-chip arm-chip--off">
        <span className="foot-bar__dot foot-bar__dot--off" />
        <span className="arm-chip__label">—</span>
      </div>
    );
  }
  const st = arm.status;
  const detail =
    st === "ok"
      ? arm.port || "已连接"
      : arm.detail || STATUS_LABEL[st] || st;
  return (
    <div
      className={`arm-chip arm-chip--${st}`}
      title={arm.detail || arm.port || undefined}
    >
      <span className={`foot-bar__dot ${armDotClass(st)}`} />
      <span className="arm-chip__label">{arm.label}</span>
      <span className="arm-chip__status">{STATUS_LABEL[st] ?? st}</span>
      <span className="arm-chip__detail">{detail}</span>
    </div>
  );
}

export function StatusFoot({ connected, snapshot }: Props) {
  const [busy, setBusy] = useState(false);
  const wsClass = connected ? "foot-bar__dot--ok" : "foot-bar__dot--warn";
  const safety = snapshot?.safety_enabled ?? true;
  const arms = snapshot?.arms;
  const armsReady = arms?.ready ?? false;
  const hint = arms?.hint;

  const toggleSafety = async () => {
    if (busy || !snapshot) return;
    setBusy(true);
    try {
      await api.setSafety(!safety);
    } finally {
      setBusy(false);
    }
  };

  const triggerRecover = async () => {
    if (busy || !snapshot) return;
    if (!window.confirm("复位将停止从臂、重新使能所有电机，并同步软件位姿。继续？")) return;
    setBusy(true);
    try {
      await api.recover();
    } finally {
      setBusy(false);
    }
  };

  const footStyle = !safety
    ? { background: "var(--accent-rec)", color: "#fff" }
    : !armsReady && snapshot
      ? { background: "rgba(180, 60, 40, 0.12)" }
      : undefined;

  return (
    <div className="foot-bar" style={footStyle}>
      <div className="arm-status-window" role="status" aria-live="polite">
        <div className="arm-status-window__title">机械臂状态</div>
        <ArmChip arm={arms?.master} />
        <ArmChip arm={arms?.slave} />
        {hint ? <div className="arm-status-window__hint">{hint}</div> : null}
      </div>

      <div className="foot-bar__item">
        <span className={`foot-bar__dot ${wsClass}`} />
        <span>{connected ? "服务已连接" : "服务重连中"}</span>
      </div>
      {snapshot?.pair_id ? (
        <div className="foot-bar__item" title="当前臂型配套">
          {snapshot.pair_id}
        </div>
      ) : null}
      {snapshot ? (
        <div className="foot-bar__item">{snapshot.frame_count.toLocaleString()} ticks</div>
      ) : null}
      <div className="foot-bar__spacer" />
      {snapshot?.last_error && !hint ? (
        <div className="foot-bar__item" style={{ color: safety ? "var(--accent-rec)" : "#fff" }}>
          ⚠ {snapshot.last_error}
        </div>
      ) : null}
      <button
        type="button"
        className="foot-bar__toggle"
        onClick={triggerRecover}
        disabled={busy || !snapshot || !armsReady || (snapshot?.recovering ?? false)}
        title="电机线被拔后重插：先慢混回零位 → 重新握手 → 同步软件位姿（误按时可在 paused 页面解除锁定中止）"
      >
        🔧 复位
      </button>
      <button
        type="button"
        className="foot-bar__toggle"
        onClick={toggleSafety}
        disabled={busy || !snapshot}
        title={safety
          ? "安全模式：限速 + 突变检测。点击关闭后将完全跟随主臂。"
          : "⚠ 安全模式已关闭：主臂任何动作都会原样传递到从臂"}
      >
        {safety ? "🛡 安全模式" : "⚠ 完全跟随 (不安全)"}
      </button>
    </div>
  );
}
