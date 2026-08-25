import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { ArmProfileInfo, StateSnapshot } from "../types";

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

export function ArmSelectPanel({ snapshot, onToast }: Props) {
  const [leaders, setLeaders] = useState<ArmProfileInfo[]>([]);
  const [followers, setFollowers] = useState<ArmProfileInfo[]>([]);
  const [leaderId, setLeaderId] = useState("");
  const [followerId, setFollowerId] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [L, F] = await Promise.all([
          api.listProfiles("leader"),
          api.listProfiles("follower"),
        ]);
        if (cancelled) return;
        setLeaders(L);
        setFollowers(F);
        setLoadErr(null);
      } catch (e) {
        if (!cancelled) {
          setLoadErr(e instanceof Error ? e.message : String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Sync draft selects from live snapshot (WS).
  useEffect(() => {
    if (!snapshot) return;
    if (snapshot.leader_profile) setLeaderId(snapshot.leader_profile);
    if (snapshot.follower_profile) setFollowerId(snapshot.follower_profile);
  }, [snapshot?.leader_profile, snapshot?.follower_profile]);

  const mode = snapshot?.mode ?? "idle";
  const locked = BUSY_MODES.has(mode);
  const dirty = useMemo(() => {
    if (!snapshot) return false;
    return (
      leaderId !== (snapshot.leader_profile ?? "") ||
      followerId !== (snapshot.follower_profile ?? "")
    );
  }, [leaderId, followerId, snapshot]);

  const pairPreview =
    leaderId && followerId ? `${leaderId}__${followerId}` : "—";

  const onSave = async () => {
    if (!leaderId || !followerId) {
      onToast("err", "请选择主臂与从臂型号");
      return;
    }
    setBusy(true);
    try {
      await api.selectProfiles(leaderId, followerId);
      onToast("ok", `臂型已保存：${leaderId} → ${followerId}`);
    } catch (e) {
      onToast("err", `保存失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="arm-select-panel" aria-label="臂型选择">
      <div className="arm-select-panel__header">臂型选择</div>
      {loadErr ? (
        <div className="arm-select-panel__hint arm-select-panel__hint--err">
          无法加载型号列表：{loadErr}
        </div>
      ) : null}
      <label className="arm-select-panel__field">
        <span>主臂</span>
        <select
          value={leaderId}
          disabled={busy || locked || leaders.length === 0}
          onChange={(e) => setLeaderId(e.target.value)}
        >
          {leaders.length === 0 ? (
            <option value="">加载中…</option>
          ) : (
            leaders.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label_zh || p.label}
              </option>
            ))
          )}
        </select>
      </label>
      <label className="arm-select-panel__field">
        <span>从臂</span>
        <select
          value={followerId}
          disabled={busy || locked || followers.length === 0}
          onChange={(e) => setFollowerId(e.target.value)}
        >
          {followers.length === 0 ? (
            <option value="">加载中…</option>
          ) : (
            followers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label_zh || p.label}
              </option>
            ))
          )}
        </select>
      </label>
      <div className="arm-select-panel__meta">
        <span className="arm-select-panel__pair" title="配套键">
          {pairPreview}
        </span>
        {locked ? (
          <span className="arm-select-panel__hint">录制/回放/校准时不可切换</span>
        ) : dirty ? (
          <span className="arm-select-panel__hint">有未保存更改</span>
        ) : (
          <span className="arm-select-panel__hint">已与运行配置同步</span>
        )}
      </div>
      <button
        type="button"
        className="arm-select-panel__save"
        disabled={busy || locked || !dirty || !leaderId || !followerId}
        onClick={onSave}
      >
        {busy ? "保存中…" : "保存臂型"}
      </button>
    </section>
  );
}
