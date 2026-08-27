/** Stable serial-port colors so swapping leader/follower keeps device identity visible. */
const PORT_PALETTE = ["#38bdf8", "#a78bfa"] as const;

/** Preserve the original Windows lab mapping used during early SO-101 bring-up. */
const LEGACY: Record<string, string> = {
  COM11: PORT_PALETTE[0],
  COM13: PORT_PALETTE[1],
};

export function portColor(
  port?: string | null,
  peers: Array<string | null | undefined> = [],
): string {
  if (!port) return "var(--text-dim)";
  if (LEGACY[port]) return LEGACY[port];

  const unique = Array.from(
    new Set([port, ...peers].filter((p): p is string => Boolean(p))),
  ).sort();
  const idx = unique.indexOf(port);
  if (idx < 0) return "var(--text-dim)";
  return PORT_PALETTE[idx % PORT_PALETTE.length];
}
