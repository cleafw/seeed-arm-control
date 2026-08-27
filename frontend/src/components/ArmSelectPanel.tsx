import { useEffect, useState } from "react";
import { api } from "../api";
import type { ArmProfileInfo, ProfileDetectInfo, StateSnapshot } from "../types";

interface Props {
  snapshot: StateSnapshot | null;
  onToast: (kind: "ok" | "err" | "info" | "warn", msg: string) => void;
}

const BUSY_MODES = new Set([
  "calibrate",
  "record",
  "transition",
  "playback",
  "return_to_follow",
]);

function portColor(port: string) {
  if (port === "COM11") return "#38bdf8";
  if (port === "COM13") return "#a78bfa";
  return "var(--text-dim)";
}

function detectHint(d: ProfileDetectInfo | null | undefined): string {
  if (!d) return "启动后将自动检测串口机型";
  if (d.message) return d.message;
  switch (d.status) {
    case "ok":
      return "已自动识别并选择主臂 / 从臂";
    case "partial":
      return "仅识别到一侧，请检查另一侧接线后重试";
    case "ambiguous":
      return "多个候选，无法唯一判定";
    case "none":
      return "未检测到已登记机型";
    default:
      return "点击「自动检测」读取当前主臂 / 从臂机型";
  }
}

export function ArmSelectPanel({ snapshot, onToast }: Props) {
  const [leaders, setLeaders] = useState<ArmProfileInfo[]>([]);
  const [followers, setFollowers] = useState<ArmProfileInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // The Vite page can become available a moment before a just-restarted
      // backend has finished importing profile drivers. Retry that short
      // startup window instead of permanently showing a raw 500 error.
      let lastError: unknown;
      for (let attempt = 0; attempt < 3 && !cancelled; attempt += 1) {
        try {
          const [L, F] = await Promise.all([
            api.listProfiles("leader"),
            api.listProfiles("follower"),
          ]);
          if (cancelled) return;
          setLeaders(L);
          setFollowers(F);
          setLoadErr(null);
          return;
        } catch (e) {
          lastError = e;
          if (attempt < 2) {
            await new Promise((resolve) => window.setTimeout(resolve, 800));
          }
        }
      }
      if (!cancelled) {
        const detail = lastError instanceof Error ? lastError.message : String(lastError);
        setLoadErr(`型号列表暂时不可用，正在等待服务恢复：${detail}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const mode = snapshot?.mode ?? "idle";
  const locked = BUSY_MODES.has(mode);
  const leaderId = snapshot?.leader_profile ?? "";
  const followerId = snapshot?.follower_profile ?? "";
  const detect = snapshot?.profile_detect ?? null;
  const pairPreview =
    leaderId && followerId ? `${leaderId}__${followerId}` : "—";
  const leaderPort = snapshot?.arms?.master.port ?? "";
  const followerPort = snapshot?.arms?.slave.port ?? "";
  const portOptions = Array.from(
    new Set([leaderPort, followerPort].filter((port): port is string => Boolean(port))),
  );
  const isSo101Pair = leaderId === "so101_leader" && followerId === "so101_follower";

  const onDetect = async () => {
    if (locked) {
      onToast("warn", "录制/回放/校准时不能检测切换臂型");
      return;
    }
    setBusy(true);
    try {
      const snap = await api.detectProfiles();
      const d = snap.profile_detect;
      const msg = d?.message || "检测完成";
      if (d?.status === "ok" && d.applied) {
        onToast("ok", msg);
      } else if (d?.status === "ambiguous" || d?.status === "partial" || d?.status === "none") {
        onToast("warn", msg);
      } else {
        onToast("info", msg);
      }
    } catch (e) {
      onToast("err", `自动检测失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const onSelect = async (
    nextLeader: string,
    nextFollower: string,
  ) => {
    if (locked) {
      onToast("warn", "录制/回放/校准时不能切换臂型");
      return;
    }
    setBusy(true);
    try {
      await api.selectProfiles(nextLeader, nextFollower);
      onToast("ok", "已更新主臂 / 从臂选择");
    } catch (e) {
      onToast("err", `切换臂型失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const onSelectPort = async (role: "leader" | "follower", port: string) => {
    const other = portOptions.find((candidate) => candidate !== port);
    if (!other) {
      onToast("warn", "需要同时检测到两个不同端口后才能重新配对");
      return;
    }
    setBusy(true);
    try {
      await api.selectPorts(role === "leader" ? port : other, role === "follower" ? port : other);
      onToast("ok", "已按所选端口重新配对主臂和从臂");
    } catch (e) {
      onToast("err", `端口配对失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="arm-select-panel" aria-label="臂型选择">
      <div className="arm-select-panel__header">臂型（可手动选择）</div>
      {loadErr ? (
        <div className="arm-select-panel__hint arm-select-panel__hint--err">
          无法加载型号列表：{loadErr}
        </div>
      ) : null}

      <div className="arm-select-panel__field">
        <span>主臂</span>
        <select
          value={leaderId}
          disabled={busy || locked || leaders.length === 0}
          onChange={(e) => onSelect(e.target.value, followerId)}
          aria-label="主臂型号"
        >
          {leaders.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.label_zh || profile.label}
            </option>
          ))}
        </select>
      </div>
      {isSo101Pair ? (
        <>
          <div className="arm-select-panel__field">
            <span><i className="arm-select-panel__port-dot" style={{ background: portColor(leaderPort) }} />主臂端口</span>
            <select style={{ color: portColor(leaderPort) }} value={leaderPort} disabled={busy || locked || portOptions.length < 2} onChange={(e) => onSelectPort("leader", e.target.value)} aria-label="主臂端口">
              {portOptions.map((port) => <option key={port} value={port}>{port}</option>)}
            </select>
          </div>
        </>
      ) : null}
      <div className="arm-select-panel__field">
        <span>从臂</span>
        <select
          value={followerId}
          disabled={busy || locked || followers.length === 0}
          onChange={(e) => onSelect(leaderId, e.target.value)}
          aria-label="从臂型号"
        >
          {followers.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.label_zh || profile.label}
            </option>
          ))}
        </select>
      </div>
      {isSo101Pair ? (
        <div className="arm-select-panel__field">
          <span><i className="arm-select-panel__port-dot" style={{ background: portColor(followerPort) }} />从臂端口</span>
          <select style={{ color: portColor(followerPort) }} value={followerPort} disabled={busy || locked || portOptions.length < 2} onChange={(e) => onSelectPort("follower", e.target.value)} aria-label="从臂端口">
            {portOptions.map((port) => <option key={port} value={port}>{port}</option>)}
          </select>
        </div>
      ) : null}

      <div className="arm-select-panel__meta">
        <span className="arm-select-panel__pair" title="配套键">
          {pairPreview}
        </span>
        <span
          className={
            detect?.status === "none" || detect?.status === "ambiguous"
              ? "arm-select-panel__hint arm-select-panel__hint--err"
              : "arm-select-panel__hint"
          }
        >
          {detectHint(detect)}
        </span>
        <span className="arm-select-panel__hint">
          自动检测只会更新下拉选项；你仍可手动选择。只有读到关节位置才会显示“已接入”。
        </span>
      </div>

      <button
        type="button"
        className="arm-select-panel__detect"
        disabled={busy || locked}
        onClick={onDetect}
      >
        {busy ? "检测中…" : "自动检测"}
      </button>
    </section>
  );
}
