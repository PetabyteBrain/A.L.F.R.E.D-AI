import { useState } from "react";
import { api } from "../../api.js";
import { fmtAgo } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { Empty, ErrorNote, Tag } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* Suggested leads for review: businesses from market analyses and senders of inquiries not in the CRM yet.
   Nothing is added by itself: tick and Add (duplicates come back with "Link to it" / "Add anyway"), or Dismiss. */

function LinkPicker({ suggestion, onLinked, onCancel }) {
  const [q, setQ] = useState(suggestion.data.name || suggestion.data.company || "");
  const { data } = usePoll(() => (q.trim() ? api(`/crm/companies?limit=8&q=${encodeURIComponent(q.trim())}`) : Promise.resolve(null)), 0, [q]);
  const [err, setErr] = useState("");
  async function link(c) {
    try { onLinked(await api(`/crm-actions/leads/${suggestion.id}/link`, { method: "POST", body: { company_id: c.id } })); }
    catch (e) { setErr(e.message); }
  }
  return (
    <div className="crm-link-picker">
      <input value={q} maxLength={80} autoFocus placeholder="Find the company" onChange={(e) => setQ(e.target.value)} aria-label="Find the company" />
      <div className="crm-pick-list">
        {(data?.items || []).map((c) => (
          <button key={c.id} type="button" className="crm-pick" onClick={() => link(c)}>
            <span>{c.name}</span><span className="meta">{c.industry}</span>
          </button>
        ))}
      </div>
      <ErrorNote error={err} />
      <button className="btn ghost small" onClick={onCancel}>Cancel</button>
    </div>
  );
}

export default function Leads({ status, onOpen, onToast, onChanged }) {
  const counts = status?.leads || {};
  const sources = Object.keys(counts);
  const [source, setSource] = useState("");
  const [tab, setTab] = useState("open");
  const { data, error, reload } = usePoll(() => api(`/crm-actions/leads?status=${tab}${source ? `&source=${source}` : ""}`), 0, [tab, source]);
  const [sel, setSel] = useState(() => new Set());
  const [dups, setDups] = useState({});              // suggestion id -> matches
  const [linking, setLinking] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const items = data || [];
  const done = () => { setSel(new Set()); reload(); onChanged?.(); };

  async function addSelected(ids = [...sel]) {
    setBusy(true); setErr("");
    try {
      const out = await api("/crm-actions/leads/add", { method: "POST", body: { ids } });
      const d = {};
      out.duplicates.forEach((x) => { d[x.id] = x.matches; });
      setDups((old) => ({ ...old, ...d }));
      if (out.added.length) onToast(`Added ${out.added.length === 1 ? out.added[0] : `${out.added.length} leads`}`);
      if (out.errors.length) setErr(out.errors.map((x) => x.error).join(" "));
      done();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  async function addAnyway(id) {
    setBusy(true); setErr("");
    try { const out = await api(`/crm-actions/leads/${id}/add`, { method: "POST", body: { force: true } }); onToast(`Added ${out.added}`); setDups((d) => ({ ...d, [id]: undefined })); done(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  async function dismiss(ids = [...sel]) {
    setBusy(true); setErr("");
    try { const out = await api("/crm-actions/leads/dismiss", { method: "POST", body: { ids } }); onToast(`Dismissed ${out.dismissed}`); done(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const toggle = (id) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const allSel = items.length > 0 && items.every((i) => sel.has(i.id));
  return (
    <div className="crm-leads">
      <div className="mail-toolbar">
        <label className="mail-check" title="Select all">
          <input type="checkbox" checked={allSel} disabled={tab !== "open" || !items.length} aria-label="Select all"
                 onChange={() => setSel(allSel ? new Set() : new Set(items.map((i) => i.id)))} />
        </label>
        {sel.size > 0 ? (
          <div className="mail-bulk">
            <span className="muted small">{sel.size} selected</span>
            <button className="btn primary small" disabled={busy} onClick={() => addSelected()}><Icon name="plus" className="btn-ico" />Add as leads</button>
            <button className="btn ghost small" disabled={busy} onClick={() => dismiss()}>Dismiss</button>
          </div>
        ) : (
          <div className="mail-filters">
            <b className="mail-title">Suggested leads</b>
            {sources.length > 1 && (
              <div className="seg" role="tablist" aria-label="Source">
                {[["", "All"], ["market", `Market analysis${counts.market ? ` (${counts.market})` : ""}`], ["mail", `Inquiries${counts.mail ? ` (${counts.mail})` : ""}`]].map(([k, l]) => (
                  <button key={k || "all"} role="tab" aria-selected={source === k} className={source === k ? "active" : ""} onClick={() => setSource(k)}>{l}</button>
                ))}
              </div>
            )}
            <select className="crm-sort" value={tab} onChange={(e) => { setTab(e.target.value); setSel(new Set()); }} aria-label="Show">
              <option value="open">To review</option><option value="added">Added</option><option value="linked">Linked</option><option value="dismissed">Dismissed</option>
            </select>
          </div>
        )}
      </div>
      <p className="muted small">Businesses from your market analyses' shortlists and people who sent an inquiry but aren't in the
        CRM yet. Nothing is added until you say so.</p>
      <ErrorNote error={error || err} />
      {data && items.length === 0 && <Empty>{tab === "open" ? "Nothing to review. Run a market analysis, or wait for the next inquiry." : "Nothing here."}</Empty>}
      <div className="mail-list">
        {items.map((s) => (
          <div key={s.id} className={`crm-lead-row${sel.has(s.id) ? " selected" : ""}`}>
            {tab === "open" && <input type="checkbox" checked={sel.has(s.id)} onChange={() => toggle(s.id)} aria-label={`Select ${s.title}`} />}
            <span className={`crm-avatar ${s.source === "market" ? "company" : "contact"}`} aria-hidden="true">
              <Icon name={s.source === "market" ? "building" : "mail"} className="lab-ico" /></span>
            <div className="crm-row-main">
              <div className="crm-row-top"><b className="crm-row-name">{s.title}</b>
                <Tag>{s.source === "market" ? "market analysis" : "inquiry"}</Tag>
                {s.data.score != null && <span className="meta">fit {s.data.score}</span>}</div>
              {s.subtitle && <span className="meta crm-row-sub">{s.subtitle}</span>}
              <span className="meta crm-lead-why">{s.reason}</span>
              {dups[s.id] && (
                <div className="crm-dups">
                  <span className="muted small">Already in the CRM?</span>
                  {dups[s.id].map((m) => <span key={m.id} className="crm-dup">{m.name} <span className="muted tiny">({m.reason})</span>
                    <button className="btn ghost small" onClick={() => onOpen("company", m.id)}>Open</button></span>)}
                  <button className="btn small" disabled={busy} onClick={() => setLinking(s)}>Link to a company</button>
                  <button className="btn ghost small" disabled={busy} onClick={() => addAnyway(s.id)}>Add anyway</button>
                </div>
              )}
              {linking?.id === s.id && <LinkPicker suggestion={s} onCancel={() => setLinking(null)}
                onLinked={(out) => { setLinking(null); setDups((d) => ({ ...d, [s.id]: undefined })); onToast(`Linked to ${out.company.name}`); done(); }} />}
            </div>
            {tab === "open" ? (
              <div className="crm-lead-actions">
                <button className="btn small" disabled={busy} onClick={() => addSelected([s.id])}>Add</button>
                <button className="btn ghost small" disabled={busy} onClick={() => setLinking(s)}>Link…</button>
                <button className="btn ghost small" disabled={busy} onClick={() => dismiss([s.id])} aria-label={`Dismiss ${s.title}`}>✕</button>
              </div>
            ) : (
              <span className="meta">{fmtAgo(s.decided_at)}{s.company_id ? <> · <button className="linkish" onClick={() => onOpen("company", s.company_id)}>open</button></> : null}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
