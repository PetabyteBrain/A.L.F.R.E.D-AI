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

// "3 min ago", "yesterday", "12 Oct" — for timelines and lists.
export function fmtAgo(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  const s = (Date.now() - t.getTime()) / 1000;
  if (s < 0) return t.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 172800) return "yesterday";
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} days ago`;
  return t.toLocaleDateString([], { day: "numeric", month: "short", ...(s > 300 * 86400 ? { year: "numeric" } : {}) });
}

// +41790000101 -> "+41 79 000 01 01" (Swiss grouping; other numbers in blocks of 3).
export function fmtPhone(p) {
  if (!p || !/^\+\d{8,15}$/.test(p)) return p || "";
  const m = p.match(/^\+41(\d{2})(\d{3})(\d{2})(\d{2})$/);
  if (m) return `+41 ${m[1]} ${m[2]} ${m[3]} ${m[4]}`;
  return `${p.slice(0, 3)} ${p.slice(3).replace(/(\d{3})(?=\d)/g, "$1 ")}`;
}
