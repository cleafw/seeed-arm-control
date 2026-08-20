export type PlayMode = "loop" | "once";

export type ControllerMode =
  | "idle"
  | "follow"
  | "record"
  | "transition"
  | "playback"
  | "return_to_follow"
  | "paused"
  | "calibrate"
  | "free_move";

export interface JointRange {
  min: number;
  max: number;
}

export interface CalibrationInfo {
  active: boolean;
  saved_at: string | null;
  mapping_enabled?: boolean;
  ready?: boolean;
  master: Record<string, JointRange>;
  slave: Record<string, JointRange>;
}

export interface MotorMapEntry {
  slave: string | null;
  /** +1 forward, -1 reverse (invert within calibrated range). */
  dir: 1 | -1;
}

export type ArmLinkStatus =
  | "ok"
  | "missing"
  | "error"
  | "reconnecting"
  | "initializing";

export interface ArmStatus {
  id: string;
  label: string;
  status: ArmLinkStatus;
  detail: string;
  port: string | null;
}

export interface ArmsInfo {
  master: ArmStatus;
  slave: ArmStatus;
  ready: boolean;
  hint: string | null;
}

export interface StateSnapshot {
  ts: number;
  mode: ControllerMode;
  safety_enabled: boolean;
  recovering: boolean;
  active_action_id: string | null;
  active_play_mode: PlayMode | null;
  frame_count: number;
  recording_frames: number | null;
  joint_states: Record<string, number>;
  /** Live master (leader) encoder read (rad). */
  master_joint_states?: Record<string, number>;
  /** Follower encoder feedback (rad). Empty if slave offline / not yet polled. */
  slave_joint_states?: Record<string, number>;
  calibration?: CalibrationInfo;
  /** Master joint → { slave, dir }. Legacy string|null also accepted by UI. */
  motor_map?: Record<string, MotorMapEntry | string | null>;
  /** True while slave eases into a new motor map. */
  motor_map_blending?: boolean;
  last_error: string | null;
  /** Dual-arm USB/serial link status. */
  arms?: ArmsInfo;
}

export interface ActionMeta {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  default_play_mode: PlayMode;
  duration_s: number;
  frame_count: number;
}
