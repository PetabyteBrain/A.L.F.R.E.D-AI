export const fmtTime = (iso) =>
  iso ? new Date(iso).toLocaleString([], { dateStyle: "short", timeStyle: "medium" }) : "—";

export const fmtClock = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "");

export function fmtDur(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${String(r).padStart(2, "0")}s`;
  return `${r}s`;
}

export const firstName = (name) => (name || "").split(" ")[0];
