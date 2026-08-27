import { MODE_STYLE } from "../modeStyle";
import type { ActionMeta, StateSnapshot } from "../types";

interface Props {
  connected: boolean;
  snapshot: StateSnapshot | null;
  modeStartTs: number | null;
  actions: ActionMeta[];
  onPause: () => void;
  onResume: () => void;
  onFreeMove: () => void;
}

function fmtClock(s: number): string {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const r = Math.floor(s - m * 60);
  return `${m}:${String(r).padStart(2, "0")}`;
}

/** Toggle: 停止运行 (unlock motors) ↔ 恢复跟随 */
function RunToggleButton({
  freeMove,
  onFreeMove,
  onResume,
  disabled,
}: {
  freeMove: boolean;
  onFreeMove: () => void;
  onResume: () => void;
  disabled?: boolean;
}) {
  if (freeMove) {
    return (
      <button
        type="button"
        className="estop-btn estop-btn--resume"
        onClick={onResume}
        disabled={disabled}
        title="重新使能电机，缓慢移动到跟随位姿后继续遥操"
      >
        ▶ 恢复跟随
      </button>
    );
  }
  return (
    <button
      type="button"
      className="estop-btn estop-btn--stop-run"
      onClick={onFreeMove}
      disabled={disabled}
      title="停止跟随：解锁所有电机，可自由拖动（无阻尼）"
    >
      ■ 停止运行
    </button>
  );
}

function EstopButton({
  paused,
  onPause,
  onResume,
  disabled,
}: {
  paused: boolean;
  onPause: () => void;
  onResume: () => void;
  disabled?: boolean;
}) {
  if (paused) {
    return (
      <button
        type="button"
        className="estop-btn estop-btn--resume"
        onClick={onResume}
        disabled={disabled}
        title="重新使能电机，并缓慢移动到主臂映射位姿后继续跟随"
      >
        ▶ 解除锁定
      </button>
    );
  }
  return (
    <button
      type="button"
      className="estop-btn"
      onClick={onPause}
      disabled={disabled}
      title="紧急停止：从臂保持当前位姿，主臂活动不再被传递"
    >
      ■ 急停
    </button>
  );
}

export function StatusBanner({
  connected,
  snapshot,
  modeStartTs,
  actions,
  onPause,
  onResume,
  onFreeMove,
}: Props) {
  const paused = snapshot?.mode === "paused";
  const freeMove = snapshot?.mode === "free_move";
  const armsReady = snapshot?.arms?.ready !== false;
  const armsHint = snapshot?.arms?.hint;
  const linkDown = snapshot != null && snapshot.arms?.ready === false;

  if (!snapshot) {
    return (
      <div className="status-banner--idle">
        <span className="brand">rebot 录制管理</span>
        <span className="status-banner__center">{connected ? "等待状态..." : "离线"}</span>
        <span className="status-banner__action">
          <RunToggleButton
            freeMove={false}
            onFreeMove={onFreeMove}
            onResume={onResume}
            disabled={!connected}
          />
        </span>
      </div>
    );
  }

  if (linkDown) {
    const m = snapshot.arms?.master;
    const s = snapshot.arms?.slave;
    const armLinkText = (arm: typeof m, fallback: string) => {
      if (!arm) return fallback;
      const label =
        arm.status === "ok"
          ? "正常"
          : arm.status === "reconnecting"
            ? "重连中"
            : arm.status === "missing"
              ? "未接入"
              : "异常";
      return `${arm.label}·${label}`;
    };
    const mText = armLinkText(m, "主臂·?");
    const sText = armLinkText(s, "从臂·?");
    return (
      <div className="status-banner--idle status-banner--link-down">
        <span className="brand">rebot 录制管理</span>
        <span className="status-banner__center">
          {armsHint || "串口异常，等待机械臂重新接入"}
          <span className="status-banner__arms-inline">{mText} · {sText}</span>
        </span>
        <span className="status-banner__action status-banner__action--pair">
          <RunToggleButton
            freeMove={false}
            onFreeMove={onFreeMove}
            onResume={onResume}
            disabled
          />
          <EstopButton paused onPause={onPause} onResume={onResume} disabled />
        </span>
      </div>
    );
  }

  if (snapshot.mode === "follow") {
    return (
      <div className="status-banner--idle">
        <span className="brand">rebot 录制管理</span>
        <span className="status-banner__center">主臂 → 从臂 跟随中</span>
        <span className="status-banner__action status-banner__action--pair">
          <RunToggleButton
            freeMove={false}
            onFreeMove={onFreeMove}
            onResume={onResume}
          />
          <EstopButton paused={false} onPause={onPause} onResume={onResume} />
        </span>
      </div>
    );
  }

  if (snapshot.mode === "free_move") {
    return (
      <div className="status-banner--idle">
        <span className="brand">rebot 录制管理</span>
        <span className="status-banner__center">已停止 — 电机已解锁，可自由拖动</span>
        <span className="status-banner__action">
          <RunToggleButton
            freeMove
            onFreeMove={onFreeMove}
            onResume={onResume}
            disabled={!armsReady}
          />
        </span>
      </div>
    );
  }

  if (snapshot.mode === "idle") {
    const calibrationReady =
      snapshot.calibration?.ready === true ||
      snapshot.calibration?.mapping_enabled === true;
    return (
      <div className="status-banner--idle">
        <span className="brand">rebot 录制管理</span>
        <span className="status-banner__center">
          {armsHint || "待校准 — 从臂未跟随"}
        </span>
        <span className="status-banner__action">
          {calibrationReady ? (
            <EstopButton
              paused
              onPause={onPause}
              onResume={onResume}
              disabled={!armsReady}
            />
          ) : (
            <RunToggleButton
              freeMove={false}
              onFreeMove={onFreeMove}
              onResume={onResume}
              disabled
            />
          )}
        </span>
      </div>
    );
  }

  const style = MODE_STYLE[snapshot.mode];
  const elapsed = modeStartTs != null ? snapshot.ts - modeStartTs : 0;

  let detail = "";
  if (snapshot.mode === "record") {
    const frames = snapshot.recording_frames ?? 0;
    detail = `${fmtClock(elapsed)} · ${frames}f`;
  } else if (snapshot.mode === "playback" || snapshot.mode === "transition") {
    const action = actions.find((a) => a.id === snapshot.active_action_id);
    const name = action?.name ?? snapshot.active_action_id ?? "";
    if (snapshot.mode === "playback" && action) {
      const isLoop = snapshot.active_play_mode === "loop";
      const dur = action.duration_s;
      const e = isLoop ? elapsed % Math.max(dur, 0.001) : Math.min(elapsed, dur);
      detail = `${name} · ${e.toFixed(1)}s / ${dur.toFixed(1)}s${isLoop ? " · 循环" : ""}`;
    } else {
      detail = name;
    }
  } else if (snapshot.mode === "return_to_follow") {
    detail = `${elapsed.toFixed(1)}s`;
  } else if (snapshot.mode === "paused") {
    detail = armsHint || "从臂已锁定，主臂活动不会被传递";
  } else if (snapshot.mode === "calibrate") {
    detail = "从臂已失能，请分别拖动主/从臂扫满各轴行程";
  }

  const showRunToggle =
    snapshot.mode === "record" ||
    snapshot.mode === "playback" ||
    snapshot.mode === "transition" ||
    snapshot.mode === "return_to_follow";

  return (
    <div
      className="status-banner"
      style={{ background: style.accent, color: "#000" }}
    >
      <span
        className={`status-banner__dot${style.pulse ? " status-banner__dot--pulse" : ""}`}
        style={{ background: "#000" }}
      />
      <span className="status-banner__label">{style.label}</span>
      {detail ? <span className="status-banner__detail">{detail}</span> : null}
      <span className="status-banner__action status-banner__action--pair">
        {showRunToggle ? (
          <RunToggleButton
            freeMove={freeMove}
            onFreeMove={onFreeMove}
            onResume={onResume}
            disabled={!armsReady}
          />
        ) : null}
        <EstopButton
          paused={paused}
          onPause={onPause}
          onResume={onResume}
          disabled={!armsReady && paused}
        />
      </span>
    </div>
  );
}
