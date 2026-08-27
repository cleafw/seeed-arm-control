import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { StateSnapshot, VoiceLastIntent, VoicePolicy } from "../types";

interface Props {
  snapshot: StateSnapshot | null;
  onToast: (kind: "ok" | "err" | "info" | "warn", msg: string) => void;
}

const PRIORITY_OPTIONS: Array<{ value: VoicePolicy; label: string; hint: string }> = [
  {
    value: "follow_first",
    label: "跟随优先",
    hint: "主臂运动可随时打断语音；主臂刚动时拒绝新的语音播放",
  },
  {
    value: "voice_first",
    label: "语音优先",
    hint: "语音播放/去姿态可随时打断跟随；执行中掰主臂不会抢回",
  },
];

const INTENT_LABEL: Record<string, string> = {
  estop: "急停",
  resume: "恢复跟随",
  stop_play: "停止播放",
  play_action: "播放动作",
  goto_pose: "去姿态",
  free_move: "自由拖动",
  set_policy: "切换策略",
};

function normalizePriority(policy: VoicePolicy | undefined): VoicePolicy {
  if (policy === "voice_first") return "voice_first";
  return "follow_first";
}

function summarizeResult(li: VoiceLastIntent | null | undefined): string {
  if (!li) return "—";
  if (li.message) return li.message;
  const r = li.result;
  if (!r) return li.intent ? INTENT_LABEL[li.intent] || li.intent : "—";
  if (r.action === "play") {
    return `播放「${String(r.action_name ?? "?")}」(${String(r.mode ?? "once")})`;
  }
  if (r.action === "goto_pose") {
    return `去姿态「${String(r.pose_name ?? r.pose_id ?? "?")}」`;
  }
  if (typeof r.action === "string") return String(r.action);
  return li.intent ? INTENT_LABEL[li.intent] || li.intent : "—";
}

function parseApiError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  const idx = raw.indexOf("{");
  if (idx >= 0) {
    try {
      const j = JSON.parse(raw.slice(idx)) as {
        detail?: { message?: string; code?: string } | string;
      };
      if (typeof j.detail === "string") return j.detail;
      if (j.detail?.message) return j.detail.message;
    } catch {
      /* keep raw */
    }
  }
  return raw;
}

export function VoiceControlPanel({ snapshot, onToast }: Props) {
  const [busy, setBusy] = useState(false);
  const [utterance, setUtterance] = useState("");
  const [localDebug, setLocalDebug] = useState<VoiceLastIntent | null>(null);
  const dispatchingRef = useRef(false);

  const voice = snapshot?.voice;
  const enabled = Boolean(voice?.enabled);
  const reachable = Boolean(voice?.reachable);
  const deviceListening = Boolean(voice?.device_listening);
  const liveText = (voice?.live_text || "").trim();
  const livePartial = Boolean(voice?.live_partial);
  const priority = normalizePriority(voice?.policy);
  const fromWs = voice?.last_intent ?? null;

  const debug: VoiceLastIntent | null = (() => {
    if (!localDebug && !fromWs) return null;
    if (!localDebug) return fromWs;
    if (!fromWs) return localDebug;
    return Number(localDebug.ts ?? 0) >= Number(fromWs.ts ?? 0) ? localDebug : fromWs;
  })();

  useEffect(() => {
    if (!fromWs?.ts || !localDebug?.ts) return;
    if (Number(fromWs.ts) >= Number(localDebug.ts)) setLocalDebug(null);
  }, [fromWs?.ts, localDebug?.ts]);

  const apply = async (next: { enabled?: boolean; policy?: VoicePolicy }, okMsg: string) => {
    if (busy || !snapshot) return;
    setBusy(true);
    try {
      await api.setVoiceSettings(next);
      onToast("ok", `${okMsg}（已永久保存，下次开机仍有效）`);
    } catch (e) {
      onToast("err", `语音设置失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const dispatchHeard = async (text: string) => {
    const t = text.trim();
    if (!t || !enabled || dispatchingRef.current) return;
    dispatchingRef.current = true;
    setBusy(true);
    setLocalDebug({
      ts: Date.now() / 1000,
      ok: undefined,
      utterance: t,
      intent: null,
      message: "识别中…",
      source: "ui",
    });
    try {
      const out = await api.voiceUtterance(t, "ui");
      const result = (out.result ?? null) as Record<string, unknown> | null;
      const li: VoiceLastIntent = {
        ts: Date.now() / 1000,
        ok: true,
        utterance: t,
        intent: out.intent,
        result,
        message: summarizeResult({ intent: out.intent, result, message: undefined }),
        source: "ui",
      };
      setLocalDebug(li);
      onToast("ok", `已执行：${li.message || out.intent}`);
    } catch (e) {
      const msg = parseApiError(e);
      setLocalDebug({
        ts: Date.now() / 1000,
        ok: false,
        utterance: t,
        intent: null,
        error: "failed",
        message: msg,
        source: "ui",
      });
      onToast("err", msg);
    } finally {
      setBusy(false);
      dispatchingRef.current = false;
    }
  };

  const sendUtterance = async () => {
    const text = utterance.trim();
    if (!text || busy || !enabled) return;
    await dispatchHeard(text);
    setUtterance("");
  };

  const statusOk = debug?.ok === true;
  const statusFail = debug?.ok === false;
  const statusPending = debug != null && debug.ok === undefined;

  return (
    <section className="voice-panel" aria-label="语音控制">
      <div className="voice-panel__header">语音控制</div>

      <label className="voice-panel__enable">
        <input
          type="checkbox"
          checked={enabled}
          disabled={busy || !snapshot}
          onChange={(e) =>
            apply(
              { enabled: e.target.checked, policy: e.target.checked ? priority : undefined },
              e.target.checked ? "已启用语音控制" : "已关闭语音控制",
            )
          }
        />
        <span>启用语音控制</span>
      </label>

      {enabled ? (
        <>
          <p className="voice-panel__status">
            {reachable
              ? deviceListening
                ? "设备麦克风听写中（ReSpeaker）"
                : "语音服务已连通"
              : "语音服务未连通"}
          </p>

          <div
            className={
              liveText
                ? "voice-panel__subtitle voice-panel__subtitle--active"
                : "voice-panel__subtitle"
            }
            aria-live="polite"
          >
            <div className="voice-panel__subtitle-label">
              实时字幕{livePartial ? "（识别中）" : liveText ? "" : ""}
            </div>
            <div className="voice-panel__subtitle-text">
              {liveText || (deviceListening ? "正在听…请对着设备麦克风说话" : "等待设备听写…")}
            </div>
          </div>

          <div className="voice-panel__field">
            <span>优先级</span>
            <div className="voice-panel__seg" role="group" aria-label="语音与跟随优先级">
              {PRIORITY_OPTIONS.map((opt) => {
                const active = priority === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    className={
                      active
                        ? "voice-panel__seg-btn voice-panel__seg-btn--active"
                        : "voice-panel__seg-btn"
                    }
                    disabled={busy}
                    aria-pressed={active}
                    title={opt.hint}
                    onClick={() => {
                      if (active) return;
                      apply({ enabled: true, policy: opt.value }, `已切换为${opt.label}`);
                    }}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
            <p className="voice-panel__hint">
              {PRIORITY_OPTIONS.find((o) => o.value === priority)?.hint}
            </p>
          </div>

          <div className="voice-panel__field">
            <span>试说一句（文字备用）</span>
            <div className="voice-panel__say">
              <input
                type="text"
                value={utterance}
                disabled={busy}
                placeholder="例如：急停 / 播放挥手 / 停止播放"
                onChange={(e) => setUtterance(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void sendUtterance();
                  }
                }}
              />
              <button
                type="button"
                className="voice-panel__say-btn"
                disabled={busy || !utterance.trim()}
                onClick={() => void sendUtterance()}
              >
                发送
              </button>
            </div>
          </div>

          <div
            className={
              statusFail
                ? "voice-panel__debug voice-panel__debug--fail"
                : statusOk
                  ? "voice-panel__debug voice-panel__debug--ok"
                  : "voice-panel__debug"
            }
            aria-live="polite"
          >
            <div className="voice-panel__debug-title">执行结果</div>
            {debug ? (
              <dl className="voice-panel__debug-dl">
                <div>
                  <dt>听到</dt>
                  <dd>{debug.utterance?.trim() || liveText || "（无）"}</dd>
                </div>
                <div>
                  <dt>意图</dt>
                  <dd>
                    {debug.intent
                      ? `${INTENT_LABEL[debug.intent] || debug.intent} (${debug.intent})`
                      : statusPending
                        ? "…"
                        : "未识别"}
                  </dd>
                </div>
                <div>
                  <dt>执行</dt>
                  <dd>{summarizeResult(debug)}</dd>
                </div>
                <div>
                  <dt>结果</dt>
                  <dd>
                    {statusPending
                      ? "处理中"
                      : statusOk
                        ? "成功"
                        : statusFail
                          ? `失败${debug.error ? ` · ${debug.error}` : ""}`
                          : "—"}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="voice-panel__hint">说完一句后这里显示意图与执行结果。</p>
            )}
          </div>
        </>
      ) : (
        <p className="voice-panel__hint">勾选后设备麦克风开始听写；高优先级可随时打断低优先级。</p>
      )}
    </section>
  );
}
