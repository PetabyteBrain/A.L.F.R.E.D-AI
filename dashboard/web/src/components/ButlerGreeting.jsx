import { api } from "../api.js";
import { firstName } from "../format.js";
import { usePoll } from "../hooks.js";
import { ButlerMark } from "./hud.jsx";

const ordinal = (n) => n + (n % 10 === 1 && n !== 11 ? "st" : n % 10 === 2 && n !== 12 ? "nd" : n % 10 === 3 && n !== 13 ? "rd" : "th");
const hhmm = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const NOUN = { event: ["appointment", "appointments"], reminder: ["reminder", "reminders"], task: ["task due", "tasks due"], call: ["call", "calls"] };

function greeting(h) {
  if (h < 5) return "Good evening";          // still up
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

/* Alfred skin, Live tab: a calling card with a greeting, today's agenda at a glance and the systems. */
export default function ButlerGreeting({ me, assistant }) {
  const now = new Date();
  const { data: agenda } = usePoll(() => {
    const s = new Date(); s.setHours(0, 0, 0, 0);
    const e = new Date(s); e.setDate(e.getDate() + 1); e.setSeconds(-1);
    return api(`/agenda?${new URLSearchParams({ start: s.toISOString(), end: e.toISOString() })}`);
  }, 300000);
  const { data: health } = usePoll(() => api("/health"), 60000);

  const items = (agenda?.items || []).filter((i) => i.link_state !== "event_deleted");
  const counts = items.reduce((m, i) => ({ ...m, [i.kind]: (m[i.kind] || 0) + 1 }), {});
  const parts = Object.entries(counts).map(([k, n]) => `${n} ${NOUN[k]?.[n === 1 ? 0 : 1] || k}`);
  const next = items.filter((i) => !i.all_day && new Date(i.start) > now).sort((a, b) => a.start.localeCompare(b.start))[0];
  const down = (health || []).filter((h) => !h.ok).length;
  const date = `${now.toLocaleDateString("en-GB", { weekday: "long" })}, the ${ordinal(now.getDate())} of ${now.toLocaleDateString("en-GB", { month: "long" })}`;

  return (
    <section className="hud butler-card" aria-label="Today at a glance">
      <ButlerMark size="md" />
      <div className="butler-text">
        <p className="butler-date">{date}</p>
        <h2 className="butler-hello">{greeting(now.getHours())}, {firstName(me.display_name)}.</h2>
        <p className="butler-line">
          {!agenda ? "Consulting the diary…"
            : parts.length ? `Today you have ${parts.join(", ").replace(/, ([^,]*)$/, " and $1")}.`
            : "Your diary is clear today."}
          {next && <> Next, at <b>{hhmm(next.start)}</b>: {next.title}.</>}
        </p>
        <p className="butler-line">
          {!health ? "" : down ? `${down} of the household's systems ${down === 1 ? "needs" : "need"} your attention; see Systems.` : "All is in order downstairs."}
        </p>
        <p className="butler-sign">— {assistant}</p>
      </div>
    </section>
  );
}
