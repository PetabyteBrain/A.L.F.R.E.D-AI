// Alfred's portraits, cut from docs/assets/alfred-avatar-sheet.jpg (scripts/crop_avatar.py); hashed by the build.
export const PORTRAITS = Object.fromEntries(
  Object.entries(import.meta.glob("../assets/avatar/*.webp", { eager: true, import: "default" }))
    .map(([path, url]) => [path.split("/").pop().replace(".webp", ""), url]),
);
const ALT = {
  neutral: "Alfred", idle: "Alfred, standing by", greeting: "Alfred greeting you", listening: "Alfred listening",
  thinking: "Alfred thinking", reading: "Alfred checking his tablet", working: "Alfred at the computers",
  explaining: "Alfred explaining", pleased: "Alfred, pleased", concerned: "Alfred, concerned", firm: "Alfred, arms folded",
  pointing: "Alfred raising a finger", bow: "Alfred bowing", tea: "Alfred having tea", attentive: "Alfred, attentive",
  lookout: "Alfred keeping an eye on a call",
};

export function Portrait({ state, className, decorative = false }) {
  const src = PORTRAITS[state] || PORTRAITS.neutral;
  return <img key={state} className={`avatar-img ${className || ""}`} src={src} alt={decorative ? "" : ALT[state] || "Alfred"} draggable="false" />;
}
