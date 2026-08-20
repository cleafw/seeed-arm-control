import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useWs } from "./useWs";
import type { ActionMeta } from "./types";
import { ToastProvider, useToast } from "./components/Toaster";
import { StatusBanner } from "./components/StatusBanner";
import { JointPanel } from "./components/JointPanel";
import { StageMain } from "./components/StageMain";
import { StatusFoot } from "./components/StatusFoot";
import { CalibratePanel } from "./components/CalibratePanel";
import { ActionPicker } from "./components/ActionPicker";

export default function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  );
}

function AppInner() {
  const toast = useToast();
  const { connected, snapshot, modeStartTs } = useWs();
  const [actions, setActions] = useState<ActionMeta[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastModeRef = useRef<string | null>(null);

  const handlePause = useCallback(async () => {
    try {
      await api.pause();
      toast.push("info", "已紧急停止 — 从臂保持当前位姿");
    } catch (e) {
      toast.push("err", `急停失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }, [toast]);

  const handleFreeMove = useCallback(async () => {
    try {
      await api.freeMove();
      toast.push("info", "已停止运行 — 电机已解锁，可自由拖动");
    } catch (e) {
      toast.push("err", `停止失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }, [toast]);

  const handleResume = useCallback(async () => {
    try {
      await api.resume();
      toast.push("ok", "正在重新使能并缓移到跟随位姿");
    } catch (e) {
      toast.push("err", `恢复失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }, [toast]);

  const refresh = useCallback(async () => {
    try {
      setActions(await api.listActions());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Refresh action list whenever a recording finishes (mode goes record -> follow)
  // or when leaving playback so renames/deletes from elsewhere are picked up.
  useEffect(() => {
    if (!snapshot) return;
    const prev = lastModeRef.current;
    lastModeRef.current = snapshot.mode;
    if (prev && prev !== snapshot.mode && snapshot.mode === "follow") {
      refresh();
    }
  }, [snapshot, refresh]);

  // First-run prompt: no valid calibration saved yet.
  const calPromptedRef = useRef(false);
  useEffect(() => {
    if (!connected || !snapshot || calPromptedRef.current) return;
    const cal = snapshot.calibration;
    if (cal?.mapping_enabled) return;
    if (snapshot.mode === "calibrate") return;
    calPromptedRef.current = true;
    toast.push("warn", "首次运行需要先校准：请在中间栏点击「开始校准」");
  }, [connected, snapshot, toast]);

  const needsCalibration =
    connected &&
    snapshot != null &&
    snapshot.mode !== "calibrate" &&
    !snapshot.calibration?.mapping_enabled;

  const mode = snapshot?.mode ?? "idle";
  // Picker / record only when idle follow (not calibrating).
  const playDisabled = mode !== "follow";

  return (
    <>
      {!connected && (
        <div className="conn-overlay">
          <div className="conn-card">
            <div className="conn-card__spinner" />
            <div className="conn-card__title">正在连接到机器人服务...</div>
            <div className="conn-card__hint">如果一直无法连接，请检查后端是否在运行</div>
          </div>
        </div>
      )}

      <div className="app-shell">
        <StatusBanner
          connected={connected}
          snapshot={snapshot}
          modeStartTs={modeStartTs}
          actions={actions}
          onPause={handlePause}
          onResume={handleResume}
          onFreeMove={handleFreeMove}
        />
        {(error || needsCalibration) && (
          <div className="notice-stack">
            {error && (
              <div className="error-banner">
                <span>{error}</span>
                <button
                  type="button"
                  className="error-banner__close"
                  onClick={() => setError(null)}
                  aria-label="关闭"
                >
                  ✕
                </button>
              </div>
            )}
            {needsCalibration && (
              <div className="cal-needed-banner" role="status">
                首次运行需要先校准 — 请在中间「关节校准」栏点击「开始校准」，分别扫满主臂与从臂行程后再完成。
              </div>
            )}
          </div>
        )}
        <JointPanel
          joints={
            mode === "calibrate" || mode === "free_move"
              ? (snapshot?.master_joint_states ?? snapshot?.joint_states)
              : snapshot?.joint_states
          }
          slaveJoints={snapshot?.slave_joint_states}
          mode={mode}
          masterLabel={mode === "calibrate" || mode === "free_move"}
          motorMap={snapshot?.motor_map}
          motorMapBlending={snapshot?.motor_map_blending}
          onMotorMapChange={refresh}
          onMotorMapSaved={() =>
            toast.push("ok", "电机映射已永久保存（重启后仍有效）")
          }
          onMotorMapError={(msg) => toast.push("err", `电机映射失败：${msg}`)}
        />
        <CalibratePanel
          snapshot={snapshot}
          onToast={(kind, msg) => toast.push(kind, msg)}
        />
        <main className="main-stage">
          {snapshot ? (
            <StageMain
              snapshot={snapshot}
              modeStartTs={modeStartTs}
              actions={actions}
              playDisabled={playDisabled}
              onChange={refresh}
              onOpenPicker={() => setPickerOpen(true)}
            />
          ) : null}
        </main>
        <StatusFoot connected={connected} snapshot={snapshot} />
      </div>

      {pickerOpen && (
        <ActionPicker
          actions={actions}
          playDisabled={playDisabled}
          onClose={() => setPickerOpen(false)}
          onChange={refresh}
        />
      )}
    </>
  );
}
