import { useState } from "react";
import { api } from "../api";
import type { ActionMeta, StateSnapshot } from "../types";
import { RecordingRow } from "./RecordingRow";
import { useToast } from "./Toaster";

interface Props {
  snapshot: StateSnapshot;
  modeStartTs: number | null;
  actions: ActionMeta[];
  playDisabled: boolean;
  onChange: () => void;
  onOpenPicker: () => void;
}

const RECENT_LIMIT = 5;

export function StageMain({
  snapshot,
  modeStartTs,
  actions,
  playDisabled,
  onChange,
  onOpenPicker,
}: Props) {
  const toast = useToast();
  const elapsed = modeStartTs != null ? Math.max(0, snapshot.ts - modeStartTs) : 0;

  if (snapshot.mode === "follow") {
    return <FollowStage actions={actions} playDisabled={playDisabled} onChange={onChange} onOpenPicker={onOpenPicker} />;
  }

  if (snapshot.mode === "idle") {
    return (
      <div className="stage-panel stage-panel--center">
        <div className="mode-indicator">
          <div className="mode-indicator__title" style={{ color: "var(--accent-trans)" }}>
            待校准
          </div>
          <div className="mode-indicator__subtitle">
            从臂不会跟随主臂。请先完成中间栏「关节校准」，完成后再开始遥操。
          </div>
        </div>
      </div>
    );
  }

  if (snapshot.mode === "free_move") {
    return (
      <div className="stage-panel stage-panel--center">
        <div className="paused-stage">
          <div className="paused-stage__title" style={{ color: "var(--accent-free)" }}>
            已停止运行
          </div>
          <div className="paused-stage__hint">
            所有电机已解锁，可自由拖动（无阻尼）。主臂活动不会被传递。
            <br />
            准备好后点击 <b>恢复跟随</b>，从臂会缓慢移动到映射位姿后继续遥操。
          </div>
          <div className="paused-stage__actions">
            <button
              type="button"
              className="paused-stage__resume"
              onClick={async () => {
                try {
                  await api.resume();
                  toast.push("ok", "正在缓移到跟随位姿");
                } catch (e) {
                  toast.push("err", `${e instanceof Error ? e.message : String(e)}`);
                }
              }}
            >
              ▶ 恢复跟随
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (snapshot.mode === "record") {
    return (
      <div className="stage-panel stage-panel--center">
        <div className="mode-indicator">
          <div className="mode-indicator__title" style={{ color: "var(--accent-rec)" }}>
            录制中
          </div>
          <div className="mode-indicator__subtitle">主臂 → 从臂 同步存档</div>
        </div>
        <div className="big-stat">
          <div className="big-stat__main" style={{ color: "var(--accent-rec)" }}>
            {elapsed.toFixed(1)}
            <span className="big-stat__unit">s</span>
          </div>
          <div className="big-stat__sub">{snapshot.recording_frames ?? 0} 帧</div>
        </div>
        <button
          type="button"
          className="stop-btn"
          aria-label="停止录制"
          onClick={async () => {
            try {
              const a = await api.stopRecord();
              toast.push("ok", `已保存「${a.name}」(${a.frame_count} 帧 / ${a.duration_s.toFixed(1)}s)`);
              onChange();
            } catch (e) {
              toast.push("err", `停止失败：${e instanceof Error ? e.message : String(e)}`);
            }
          }}
        >
          <div className="stop-btn__square" />
        </button>
      </div>
    );
  }

  if (snapshot.mode === "transition") {
    const target = actions.find((a) => a.id === snapshot.active_action_id);
    return (
      <div className="stage-panel stage-panel--center">
        <div className="mode-indicator">
          <div className="mode-indicator__title" style={{ color: "var(--accent-trans)" }}>
            准备执行
          </div>
          <div className="mode-indicator__subtitle">{target?.name ?? "—"}</div>
        </div>
        <button
          type="button"
          className="stop-btn"
          aria-label="取消"
          onClick={async () => {
            try {
              await api.stopPlay();
              toast.push("info", "已取消");
            } catch (e) {
              toast.push("err", `${e instanceof Error ? e.message : String(e)}`);
            }
          }}
        >
          <div className="stop-btn__square" />
        </button>
      </div>
    );
  }

  if (snapshot.mode === "playback") {
    const action = actions.find((a) => a.id === snapshot.active_action_id);
    const dur = action?.duration_s ?? 0;
    const isLoop = snapshot.active_play_mode === "loop";
    const e = dur > 0 ? (isLoop ? elapsed % dur : Math.min(elapsed, dur)) : elapsed;
    const progress = dur > 0 ? Math.min(1, e / dur) : 0;
    return (
      <div className="stage-panel stage-panel--center">
        <div className="mode-indicator">
          <div className="mode-indicator__title" style={{ color: "var(--accent-play)" }}>
            执行中
          </div>
          <div className="mode-indicator__subtitle">
            {action?.name ?? snapshot.active_action_id ?? "—"}
            {isLoop ? " · 循环" : ""}
          </div>
        </div>
        <div className="big-stat">
          <div className="big-stat__main" style={{ color: "var(--accent-play)" }}>
            {e.toFixed(1)}
            <span className="big-stat__unit">s</span>
          </div>
          <div className="big-stat__sub">/ {dur.toFixed(1)}s</div>
        </div>
        <div className="progress-bar">
          <div className="progress-bar__fill" style={{ width: `${progress * 100}%` }} />
        </div>
        <button
          type="button"
          className="stop-btn"
          aria-label="停止执行"
          onClick={async () => {
            try {
              await api.stopPlay();
              toast.push("info", "已停止");
            } catch (e) {
              toast.push("err", `${e instanceof Error ? e.message : String(e)}`);
            }
          }}
        >
          <div className="stop-btn__square" />
        </button>
      </div>
    );
  }

  if (snapshot.mode === "paused") {
    const recovering = snapshot.recovering;
    return (
      <div className="stage-panel stage-panel--center">
        <div className="paused-stage">
          <div className="paused-stage__title">
            {recovering ? "复位中..." : "从臂已锁定"}
          </div>
          <div className="paused-stage__hint">
            {recovering ? (
              <>
                正在慢慢回到零位。完成后会自动重新握手所有电机。
                <br />
                如果你是误按，可现在点 <b>解除锁定</b> 中止复位。
              </>
            ) : (
              <>
                从臂正保持在当前位姿，主臂的活动不会被传递。
                <br />
                电机保护 / 重新上电后，直接点 <b>解除锁定</b>：会重新使能电机，并缓慢移动到跟随位姿。
                <br />
                若单根电机线刚重插仍无响应，可先点 <b>复位</b> 再解除锁定。
              </>
            )}
          </div>
          <div className="paused-stage__actions">
            <button
              type="button"
              className="paused-stage__resume"
              onClick={async () => {
                try {
                  await api.resume();
                  toast.push("ok", recovering ? "复位已中止，正在缓移回跟随" : "正在缓移到跟随位姿");
                } catch (e) {
                  toast.push("err", `${e instanceof Error ? e.message : String(e)}`);
                }
              }}
            >
              ▶ 解除锁定
            </button>
            <button
              type="button"
              className="paused-stage__recover"
              disabled={recovering}
              onClick={async () => {
                try {
                  await api.recover();
                  toast.push("ok", "已复位：所有电机重新握手");
                } catch (e) {
                  toast.push("err", `复位失败：${e instanceof Error ? e.message : String(e)}`);
                }
              }}
              title="先慢混到零位，再 disable→enable 重新握手所有电机"
            >
              🔧 复位
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (snapshot.mode === "return_to_follow") {
    return (
      <div className="stage-panel stage-panel--center">
        <div className="mode-indicator">
          <div className="mode-indicator__title" style={{ color: "var(--accent-return)" }}>
            回到跟随
          </div>
          <div className="mode-indicator__subtitle">慢速过渡至主臂当前位姿</div>
        </div>
        <div className="big-stat">
          <div className="big-stat__main" style={{ color: "var(--accent-return)" }}>
            {elapsed.toFixed(1)}
            <span className="big-stat__unit">s</span>
          </div>
        </div>
      </div>
    );
  }

  return null;
}

function FollowStage({
  actions,
  playDisabled,
  onChange,
  onOpenPicker,
}: {
  actions: ActionMeta[];
  playDisabled: boolean;
  onChange: () => void;
  onOpenPicker: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const recent = actions.slice(0, RECENT_LIMIT);

  async function startRecord() {
    try {
      await api.startRecord(name.trim() || undefined);
      setName("");
      toast.push("ok", "开始录制");
    } catch (e) {
      toast.push("err", `录制失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div className="stage-panel stage-panel--follow">
      <div className="follow-hero">
        <div className="mode-indicator">
          <div className="mode-indicator__title">遥操模式</div>
          <div className="mode-indicator__subtitle">主臂 → 从臂跟随</div>
        </div>
        <div className="entry-group">
          <input
            className="entry-btn__name-input"
            placeholder="动作名称（可留空）"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void startRecord();
              }
            }}
          />
          <button
            type="button"
            className="entry-btn"
            onClick={() => void startRecord()}
            aria-label="开始录制"
          >
            <div className="entry-btn__icon--rec" />
            <div className="entry-btn__label">开始录制</div>
          </button>
        </div>
        <div className="follow-hero__hint">
          点击开始录制后，从臂会持续跟随主臂，并把整段动作存进下方动作库。
          录完按红色停止键即可。
        </div>
      </div>

      <div className="quick-library">
        <div className="quick-library__header">
          <div>
            <div className="quick-library__eyebrow">最近动作</div>
            <div className="quick-library__title">动作库 · {actions.length}</div>
          </div>
          <button type="button" className="quick-library__more" onClick={onOpenPicker}>
            搜索全部
          </button>
        </div>
        {recent.length === 0 ? (
          <div className="quick-library__empty">
            还没有录制动作。先录一段，再从这里直接执行或循环。
          </div>
        ) : (
          <div className="quick-library__list">
            {recent.map((a) => (
              <RecordingRow
                key={a.id}
                action={a}
                variant="quick"
                playDisabled={playDisabled}
                showDelete
                onChange={onChange}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
