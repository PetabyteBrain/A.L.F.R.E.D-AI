import { useState } from "react";
import { api } from "../../api.js";
import { fmtAgo } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { Empty, ErrorNote } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* A record's timeline: what people logged, plus emails and Alfred's outside calls matched by address. */

const TYPES = {
  note: { label: "Note", icon: "note" },
  call: { label: "Call", icon: "phone" },
  meeting: { label: "Meeting", icon: "calendar" },
  task: { label: "Task", icon: "check" },
  document: { label: "Document", icon: "doc" },
  email: { label: "Email", icon: "mail" },
  outside_call: { label: "Alfred's call", icon: "phone" },
  deal: { label: "Deal", icon: "crm" },
};
const PATHS = { company: "companies", contact: "contacts", deal: "deals" };
const FILTERS = [["", "All"], ["note", "Notes"], ["call", "Calls"], ["meeting", "Meetings"], ["email", "Emails"], ["deal", "Deals"]];

function localInput(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function Composer({ path, onLogged }) {
  const [kind, setKind] = useState("note");
  const [summary, setSummary] = useState("");
  const [body, setBody] = useState("");
  const [when, setWhen] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const out = await api(`${path}/activities`, { method: "POST",
        body: { kind, summary, body, when: when ? when.replace("T", " ") : null } });
      setSummary(""); setBody(""); setWhen(""); setOpen(false);
      onLogged(out, TYPES[kind].label);
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  return (
    <form className="crm-composer" onSubmit={submit}>
      <div className="seg crm-kinds" role="radiogroup" aria-label="What to log">
        {["note", "call", "meeting", "document"].map((k) => (
          <button key={k} type="button" role="radio" aria-checked={kind === k} className={kind === k ? "active" : ""}
                  onClick={() => setKind(k)}><Icon name={TYPES[k].icon} className="btn-ico" />{TYPES[k].label}</button>
        ))}
      </div>
      <input id="crm-composer-input" value={summary} maxLength={200} required onFocus={() => setOpen(true)}
             placeholder={kind === "call" ? "e.g. Called about the offer, wants a call back Friday"
               : kind === "meeting" ? "e.g. Kick-off meeting at their office" : kind === "document"
                 ? "e.g. Offer v2 sent (Files: Clients/Acme/offer.pdf)" : "Write a note…"}
             onChange={(e) => setSummary(e.target.value)} aria-label="Summary" />
      {open && (
        <>
          <textarea rows={3} value={body} maxLength={5000} placeholder="Details (optional)" onChange={(e) => setBody(e.target.value)}
                    aria-label="Details" />
          <div className="crm-composer-foot">
            <label className="field"><span>When</span>
              <input type="datetime-local" value={when} max={localInput(new Date(Date.now() + 365 * 864e5))}
                     onChange={(e) => setWhen(e.target.value)} />
            </label>
            <span className="muted tiny">{when ? "" : "Empty = now."}</span>
            <button type="button" className="btn ghost small" onClick={() => { setOpen(false); setErr(""); }}>Cancel</button>
            <button type="submit" className="btn primary small" disabled={busy || !summary.trim()}>Add to timeline</button>
          </div>
        </>
      )}
      <ErrorNote error={err} />
    </form>
  );
}

export default function Timeline({ entity, id, canEdit, onChanged }) {
  const [filter, setFilter] = useState("");
  const path = `/crm/${PATHS[entity]}/${id}`;
  const filters = FILTERS.filter(([k]) => !(k === "email" && entity === "deal") && !(k === "deal" && entity === "contact"));
  const { data, error, reload } = usePoll(() => api(`${path}/timeline${filter ? `?type=${filter}` : ""}`), 60000, [path, filter]);
  const [open, setOpen] = useState(() => new Set());
  const [err, setErr] = useState("");
  const items = data || [];
  const toggle = (k) => setOpen((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  async function remove(item) {
    setErr("");
    try { const out = await api(`/crm-actions/activities/${item.id}`, { method: "DELETE" }); reload(); onChanged?.(out, "Entry removed"); }
    catch (e) { setErr(e.message); }
  }
  return (
    <section className="crm-timeline" aria-label="Timeline">
      <h3 className="sub flush">Timeline</h3>
      {canEdit && <Composer path={path} onLogged={(out, what) => { reload(); onChanged?.(out, `${what} added`); }} />}
      <div className="crm-filters" role="tablist" aria-label="Filter the timeline">
        {filters.map(([k, label]) => (
          <button key={k || "all"} type="button" role="tab" aria-selected={filter === k}
                  className={`chip${filter === k ? "" : " off"}`} onClick={() => setFilter(k)}>{label}</button>
        ))}
      </div>
      <ErrorNote error={error || err} />
      {data && items.length === 0 && (
        <Empty>{filter ? "Nothing of that kind yet." : "Nothing yet. Log a call or a note, or email them: mail to and from their addresses shows up here by itself."}</Empty>
      )}
      <ol className="crm-tl">
        {items.map((i) => {
          const t = TYPES[i.type] || TYPES.note;
          const key = `${i.type}:${i.id}`;
          const long = (i.body || "").length > 0;
          return (
            <li key={key} className={`crm-tl-item t-${i.type}${i.direction ? ` ${i.direction}` : ""}`}>
              <span className="crm-tl-ico" aria-hidden="true"><Icon name={t.icon} className="lab-ico" /></span>
              <div className="crm-tl-body">
                <div className="crm-tl-top">
                  <span className="crm-tl-kind">{i.type === "email" ? (i.direction === "in" ? "Email received" : "Email sent") : t.label}</span>
                  <span className="meta" title={i.at_local}>{fmtAgo(i.at)}</span>
                </div>
                <button type="button" className="crm-tl-summary" onClick={() => (long ? toggle(key) : null)}
                        aria-expanded={long ? open.has(key) : undefined} disabled={!long}>
                  {i.summary}
                </button>
                {long && open.has(key) && <p className="crm-tl-text">{i.body}</p>}
                <div className="meta">
                  {[i.by && (i.type === "email" ? i.by : `by ${i.by}`), i.contact && `with ${i.contact}`].filter(Boolean).join(" · ")}
                  {i.mine && canEdit && !["email", "outside_call", "deal"].includes(i.type) && <> · <button type="button" className="linkish" onClick={() => remove(i)}>Remove</button></>}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
