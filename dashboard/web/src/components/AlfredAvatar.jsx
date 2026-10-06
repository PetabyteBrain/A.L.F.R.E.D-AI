import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiStream } from "../api.js";
import { firstName } from "../format.js";
import { usePoll } from "../hooks.js";
import Markdown from "./Markdown.jsx";
import { Portrait } from "./portraits.jsx";

const TIPS_KEY = "alfred-avatar-tips";
const GREETED_KEY = "alfred-avatar-greeted";
// Idle life: after a quiet spell he does something (tea, a look to the side, his tablet), holds it a
// while, returns to his resting pose, and after another while does something else. Never the same twice.
const IDLE_FIRST_MS = 90000;
const IDLE_GAP_MS = [60000, 150000];
const IDLE_HOLD_MS = [10000, 18000];
const IDLE_MOMENTS = ["tea", "lookout", "reading"];
const RESTING = new Set(["attentive", "idle", "neutral", "pleased", "greeting", "bow"]);
const between = ([a, b]) => a + Math.random() * (b - a);

const SUGGEST = {
  live: ["What's happening right now?", "How do I end a call?"],
  tasks: ["Remind me tomorrow at 9 to call the bank", "How do I add something to the calendar?"],
  notes: ["What's on my shopping list?", "How do I move a file to another folder?"],
  history: ["What did I talk about yesterday?", "How do I find an old call?"],
  activity: ["What happened today?"],
  health: ["Is everything running fine?", "Which systems need attention?"],
  people: ["How do I add a new person?", "How do I reset someone's two-factor sign-in?"],
  usage: ["What have we spent this month?"],
  settings: ["What can cofounders do at the moment?", "How do I preview the dashboard as a cofounder?"],
  profile: ["How do I set my quiet hours?", "How do I link my Google Calendar?"],
};
const greetingWord = (h) => (h < 5 || h >= 18 ? "Good evening" : h < 12 ? "Good morning" : "Good afternoon");

function toolLabel(name) {
  if (name === "web_search") return "Searching the web";
  if (name.startsWith("calendar_")) return "Consulting the calendar";
  if (name.startsWith("nas_") || name.includes("company")) return "Looking through the files";
  if (/call/.test(name)) return "Arranging the call";
  if (name.includes("task") || name.includes("reminder")) return "Checking the task list";
  if (name.startsWith("n8n_")) return "Consulting the automation server";
  return "Looking into it";
}

/* Alfred's avatar: a portrait button on every dashboard tab that opens a small helper with a speech
   bubble. Same conversation as the Chat tab; the portrait follows what he is doing. */
export default function AlfredAvatar({ me, assistant, page, pageLabel, onNavigate }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState("idle");
  const [said, setSaid] = useState("");              // what's in the speech bubble
  const [asked, setAsked] = useState("");            // the question it answers
  const [tool, setTool] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [toast, setToast] = useState(null);          // {state, text, action?}
  const [tips, setTipsState] = useState(() => { try { return localStorage.getItem(TIPS_KEY) !== "off"; } catch { return true; } });
  const input = useRef(null);
  const idleTimer = useRef(0);
  const settle = useRef(0);
  const name = firstName(me.display_name);

  const setTips = (on) => { setTipsState(on); try { localStorage.setItem(TIPS_KEY, on ? "on" : "off"); } catch { /* ignore */ } };
  const restIn = useCallback((ms, to = "attentive") => {
    clearTimeout(settle.current);
    settle.current = setTimeout(() => setState(to), ms);
  }, []);
  const openRef = useRef(false);
  const busyRef = useRef(false);
  const lastMoment = useRef("");
  openRef.current = open;
  busyRef.current = busy;
  const scheduleIdle = useCallback(function schedule(delay) {
    clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => {
      if (busyRef.current) { schedule(IDLE_FIRST_MS); return; }
      const choices = IDLE_MOMENTS.filter((m) => m !== lastMoment.current);
      const moment = choices[Math.floor(Math.random() * choices.length)];
      lastMoment.current = moment;
      setState((s) => (RESTING.has(s) ? moment : s));
      idleTimer.current = setTimeout(() => {
        setState((s) => (s === moment ? (openRef.current ? "attentive" : "idle") : s));
        schedule(between(IDLE_GAP_MS));
      }, between(IDLE_HOLD_MS));
    }, delay);
  }, []);
  // Any activity restarts the quiet-spell countdown.
  const nudgeIdle = useCallback(() => scheduleIdle(IDLE_FIRST_MS), [scheduleIdle]);
  useEffect(() => { nudgeIdle(); return () => { clearTimeout(idleTimer.current); clearTimeout(settle.current); }; }, [nudgeIdle]);

  // Greeting once per browser session (a toast that fades on its own).
  useEffect(() => {
    if (!tips) return undefined;
    let seen = false;
    try { seen = sessionStorage.getItem(GREETED_KEY) === "1"; sessionStorage.setItem(GREETED_KEY, "1"); } catch { /* ignore */ }
    if (seen) return undefined;
    const t = setTimeout(() => setToast({ state: "greeting", text: `${greetingWord(new Date().getHours())}, ${name}. I'm here should you need anything.` }), 1200);
    return () => clearTimeout(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // A phone call in progress (scoped like the Live tab): he keeps an eye on it from the corner.
  const { data: live } = usePoll(() => api("/sessions/active"), 20000);
  const onCall = (live || []).some((s) => s.channel === "call");

  // Systems that fall over: he raises a finger (once per change, not on every poll).
  const { data: health } = usePoll(() => api("/health"), 60000);
  const lastDown = useRef(null);
  useEffect(() => {
    if (!health) return;
    const down = health.filter((h) => !h.ok).map((h) => h.name).sort().join(",");
    if (lastDown.current !== null && down && down !== lastDown.current && tips) {
      const n = down.split(",").length;
      setToast({ state: "pointing", text: `If I may: ${n} of the household's systems ${n === 1 ? "needs" : "need"} attention.`,
                 action: { label: "Show me", go: "health" } });
    }
    lastDown.current = down;
  }, [health, tips]);
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), toast.action ? 15000 : 8000);
    return () => clearTimeout(t);
  }, [toast]);

  function openPanel() {
    setToast(null);
    setOpen(true);
    if (!said) { setSaid(`${greetingWord(new Date().getHours())}, ${name}. How may I help${pageLabel ? ` with ${pageLabel}` : ""}?`); setState("greeting"); restIn(3500); }
    nudgeIdle();
    setTimeout(() => input.current?.focus(), 50);
  }
  function closePanel() {
    setOpen(false);
    setState("bow");
    restIn(1600, "idle");
    nudgeIdle();
  }
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") closePanel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  async function ask(text) {
    const message = text.trim();
    if (!message || busy) return;
    clearTimeout(settle.current);
    setDraft(""); setAsked(message); setSaid(""); setTool(""); setBusy(true); setState("thinking");
    let finished = false, slow = 0;
    try {
      await apiStream("/chat/stream", { message, page }, (ev) => {
        if (ev.type === "delta") { clearTimeout(slow); setTool(""); setState("explaining"); setSaid((s) => s + ev.text); }
        else if (ev.type === "tool") {
          setSaid(""); setTool(ev.name); setState("reading");
          clearTimeout(slow); slow = setTimeout(() => setState("working"), 6000);   // a long job: back to the computers
        } else if (ev.type === "done") { finished = true; clearTimeout(slow); setSaid(ev.reply); setTool(""); setState("pleased"); restIn(4000); }
        else if (ev.type === "error") throw new Error(ev.detail || "Reply failed");
      });
      if (!finished) throw new Error("The connection was lost.");
    } catch (e) {
      clearTimeout(slow);
      const limited = /a lot of messages/i.test(e.message);
      setState(limited ? "firm" : "concerned");
      setSaid(limited ? "Perhaps we might pause for a moment; that was rather a lot at once." : `My apologies, that didn't go through: ${e.message}`);
      setDraft(message);
      restIn(6000);
    } finally {
      setBusy(false);
      nudgeIdle();
    }
  }

  const chips = SUGGEST[page] || ["What can you do for me?"];
  return (
    <>
      {toast && !open && (
        <div className="avatar-toast hud" role="status">
          <Portrait state={toast.state} className="toast-img" />
          <div className="bubble-text">
            <p>{toast.text}</p>
            <div className="avatar-toast-actions">
              {toast.action && <button className="btn primary small" onClick={() => { onNavigate?.(toast.action.go); setToast(null); }}>{toast.action.label}</button>}
              <button className="btn small" onClick={openPanel}>Ask {assistant}</button>
              <button className="btn ghost small" onClick={() => setToast(null)} aria-label="Dismiss">×</button>
            </div>
          </div>
        </div>
      )}

      {open && (
        <section className="avatar-panel hud" role="dialog" aria-label={`Ask ${assistant}`}>
          <div className="avatar-stage">
            <Portrait state={state} className="stage-img" />
            <div className="speech" aria-live="polite" aria-busy={busy}>
              {asked && <p className="asked">“{asked}”</p>}
              {tool && <p className="tool-note"><span className="led" />{toolLabel(tool)}…</p>}
              {busy && !said && !tool && <div className="dots"><i /><i /><i /></div>}
              {said && <div className="speech-text"><Markdown text={said} /></div>}
            </div>
          </div>
          {!busy && (
            <div className="chips avatar-chips">
              {chips.map((c) => <button key={c} className="chip" onClick={() => ask(c)}>{c}</button>)}
            </div>
          )}
          <form className="avatar-compose" onSubmit={(e) => { e.preventDefault(); ask(draft); }}>
            <input ref={input} value={draft} maxLength={4000} placeholder={`Ask ${assistant}…`} aria-label={`Ask ${assistant}`}
                   onChange={(e) => { setDraft(e.target.value); if (!busy) { setState(e.target.value ? "listening" : "attentive"); nudgeIdle(); } }} />
            <button className="btn primary small" type="submit" disabled={busy || !draft.trim()}>Ask</button>
          </form>
          <div className="avatar-foot">
            <button className="link" onClick={() => { onNavigate?.("chat"); closePanel(); }}>Open the full chat</button>
            <label className="toggle small-toggle"><input type="checkbox" checked={tips} onChange={(e) => setTips(e.target.checked)} /><span>Pop-ups</span></label>
            <button className="btn ghost small" onClick={closePanel} aria-label="Close">Close</button>
          </div>
        </section>
      )}

      <button className={`avatar-launcher${open ? " is-open" : ""}${onCall && !open ? " on-call" : ""}`} onClick={open ? closePanel : openPanel}
              aria-label={open ? `Close ${assistant}` : `Ask ${assistant}`} aria-expanded={open}
              title={onCall && !open ? `${assistant} is keeping an eye on a call` : `Ask ${assistant}`}>
        <Portrait className="launcher-img" state={open ? state : toast ? toast.state
          : ["bow", "concerned"].includes(state) ? state : onCall ? "lookout"
          : IDLE_MOMENTS.includes(state) ? state : "idle"} />
      </button>
    </>
  );
}
