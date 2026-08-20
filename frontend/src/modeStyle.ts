import type { ControllerMode } from "./types";

/** Single source of truth for per-mode visual properties. */
export const MODE_STYLE: Record<
  ControllerMode,
  { label: string; accent: string; pulse: boolean }
> = {
  idle: { label: "待校准", accent: "var(--accent-trans)", pulse: false },
  follow: { label: "跟随中", accent: "var(--accent-idle)", pulse: false },
  record: { label: "录制中", accent: "var(--accent-rec)", pulse: true },
  transition: { label: "准备执行", accent: "var(--accent-trans)", pulse: true },
  playback: { label: "执行中", accent: "var(--accent-play)", pulse: false },
  return_to_follow: {
    label: "回到跟随",
    accent: "var(--accent-return)",
    pulse: true,
  },
  paused: { label: "已锁定", accent: "var(--accent-paused)", pulse: true },
  calibrate: { label: "校准中", accent: "var(--accent-cal)", pulse: true },
  free_move: { label: "已停止", accent: "var(--accent-free)", pulse: true },
};
