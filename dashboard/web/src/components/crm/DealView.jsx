import { useState } from "react";
import { api } from "../../api.js";
import { fmtAgo, fmtCHF, fmtDate, fmtTime } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { ErrorNote, Tag } from "../common.jsx";
import { Icon } from "../icons.jsx";
import { OutcomeDialog } from "./Pipeline.jsx";
import Timeline from "./Timeline.jsx";

/* One deal: stage (switch in place), money, dates, next step, the people involved, its timeline and history. */

const ACTION_WORD = { create: "added it", update: "changed", archive: "archived it", restore: "restored it", delete: "deleted" };
const FIELD_WORD = { stage_id: "stage", value: "value", probability: "probability", expected_close: "close date",
                     next_step: "next step", next_step_at: "next step date", outcome_note: "outcome", company_id: "company",
                     contact_id: "contact", owner_id: "owner", group_id: "group" };

export default function DealView({ id, status, onBack, onOpen, onEdit, onToast, onChanged }) {
  const path = `/crm/deals/${id}`;
  const { data: d, error, reload } = usePoll(() => api(path), 0, [path]);
  const [hist, setHist] = useState(null);
  const [menu, setMenu] = useState(false);
  const [outcome, setOutcome] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [err, setErr] = useState("");
  const [rev, setRev] = useState(0);                       // stage moves show up on the timeline
  const canEdit = !!status?.can?.edit;
  const stages = status?.stages || [];

  const changed = (out, text) => { reload(); setHist(null); setRev((n) => n + 1); onChanged?.(); if (text) onToast(text, out?.change_id); };
  async function act(fn, text) {
    setErr(""); setMenu(false);
    try { changed(await fn(), text); } catch (e) { setErr(e.message); }
  }
  async function moveTo(stageId) {
    const s = stages.find((x) => x.id === Number(stageId));
    if (!s) return;
    if (s.kind !== "open") { setOutcome(s); return; }
    act(() => api(`/crm-actions/deals/${id}/move`, { method: "POST", body: { stage_id: s.id } }), `→ ${s.name}`);
  }
  async function history() {
    if (hist) { setHist(null); return; }
    try { setHist(await api(`${path}/history`)); } catch (e) { setErr(e.message); }
  }
  async function undo(changeId) {
    try { await api("/crm-actions/undo", { method: "POST", body: { change_id: changeId } }); changed(null, "Undone"); }
    catch (e) { setErr(e.message); }
  }

  if (!d) return <div className="crm-detail"><ErrorNote error={error} />{!error && <p className="muted">Loading…</p>}</div>;
  const kind = d.stage_kind;
  return (
    <div className="crm-detail">
      <div className="mail-view-head">
        <div className="mail-view-nav"><button className="btn ghost small" onClick={onBack}>‹ Back</button></div>
        {canEdit && (
          <div className="mail-view-tools">
            <button className="btn small" onClick={() => onEdit(d)} disabled={d.archived}>Edit</button>
            <button className="btn small" onClick={() => document.getElementById("crm-composer-input")?.focus()} disabled={d.archived}>
              <Icon name="note" className="btn-ico" />Log</button>
            <div className="crm-menu">
              <button className="btn ghost small" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>More ▾</button>
              {menu && (
                <div className="crm-menu-list" role="menu">
                  <button role="menuitem" onClick={() => act(() => api(`${path}/archive`, { method: "POST", body: { archived: !d.archived } }),
                    d.archived ? "Restored" : "Archived")}><Icon name="archive" className="btn-ico" />{d.archived ? "Restore" : "Archive"}</button>
                  <button role="menuitem" className="danger" onClick={() => { setMenu(false); setConfirmDelete(true); }}>Delete…</button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <header className="crm-head">
        <span className={`crm-avatar deal k-${kind}`} aria-hidden="true"><Icon name="crm" className="lab-ico" /></span>
        <div className="crm-head-main">
          <h3 className="crm-title">{d.title}</h3>
          <div className="crm-head-meta">
            {canEdit && !d.archived ? (
              <select className={`crm-status-select k-${kind}`} value={d.stage_id} aria-label="Stage" onChange={(e) => moveTo(e.target.value)}>
                {stages.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            ) : <Tag kind={kind === "won" ? "ok" : kind === "lost" ? "bad" : ""}>{d.stage_name}</Tag>}
            {d.company_name && <button type="button" className="linkish" onClick={() => onOpen("company", d.company_id)}>{d.company_name}</button>}
            {d.archived && <Tag kind="bad">archived</Tag>}
          </div>
        </div>
        <div className="crm-head-money">
          <b>{fmtCHF(d.value)}</b>
          <span className="meta">{d.probability_effective} % · weighted {fmtCHF(d.weighted)}</span>
        </div>
      </header>
      {kind === "open" && stages.length > 0 && (
        <ol className="crm-stage-track" aria-label="Pipeline progress">
          {stages.filter((s) => s.kind === "open").map((s) => {
            const pos = stages.findIndex((x) => x.id === s.id);
            const cur = stages.findIndex((x) => x.id === d.stage_id);
            return (
              <li key={s.id} className={pos < cur ? "done" : pos === cur ? "current" : ""}>
                {canEdit && !d.archived ? <button type="button" onClick={() => s.id !== d.stage_id && moveTo(s.id)}>{s.name}</button> : <span>{s.name}</span>}
              </li>
            );
          })}
        </ol>
      )}
      <ErrorNote error={err} />

      <div className="crm-detail-grid">
        <div className="crm-facts-col">
          {(d.next_step || kind !== "open") && (
            <div className={`crm-callout${d.next_step_due ? " warn" : ""}${kind === "won" ? " ok" : kind === "lost" ? " bad" : ""}`}>
              {kind === "open" ? <><span className="crm-callout-label">Next step{d.next_step_at ? ` · ${fmtDate(d.next_step_at)}` : ""}</span><b>{d.next_step}</b></>
                : <><span className="crm-callout-label">{kind === "won" ? "Won" : "Lost"} {fmtAgo(d.closed_at)}</span><b>{d.outcome_note || "No reason noted."}</b></>}
            </div>
          )}
          <div className="crm-facts">
            {d.company_name && <div className="kv crm-fact"><span>Company</span><button type="button" className="linkish" onClick={() => onOpen("company", d.company_id)}>{d.company_name}</button></div>}
            {d.contact_name && <div className="kv crm-fact"><span>Contact</span><button type="button" className="linkish" onClick={() => onOpen("contact", d.contact_id)}>{d.contact_name}</button></div>}
            <div className="kv crm-fact"><span>Expected close</span><b className={d.overdue_close ? "warn-text" : ""}>{d.expected_close ? fmtDate(d.expected_close) : "—"}{d.overdue_close ? " (passed)" : ""}</b></div>
            <div className="kv crm-fact"><span>Probability</span><b>{d.probability_effective} %{d.probability == null ? " (stage)" : ""}</b></div>
            {d.source && <div className="kv crm-fact"><span>Source</span><b>{d.source}</b></div>}
            <div className="kv crm-fact"><span>Owner</span><b>{d.owner_name || "—"}</b></div>
            <div className="kv crm-fact"><span>Added</span><b>{fmtAgo(d.created_at)}{d.created_by_name ? ` by ${d.created_by_name}` : ""}</b></div>
          </div>
          {d.notes && <div className="crm-notes"><h3 className="sub">Notes</h3><p className="pre">{d.notes}</p></div>}
          <section className="crm-history">
            <button type="button" className="btn ghost small" onClick={history} aria-expanded={!!hist}>History {hist ? "▴" : "▾"}</button>
            {hist && (
              <ul className="crm-history-list">
                {hist.map((h) => (
                  <li key={h.id} className={h.undone ? "undone" : ""}>
                    <span>{h.user_name || "Someone"} {ACTION_WORD[h.action] || h.action}{h.fields.length ? ` ${h.fields.map((f) => FIELD_WORD[f] || f).join(", ")}` : ""}</span>
                    <span className="meta" title={fmtTime(h.ts)}>{fmtAgo(h.ts)}{h.undone ? " · undone" : ""}</span>
                    {h.can_undo && <button type="button" className="linkish" onClick={() => undo(h.id)}>Undo</button>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
        <Timeline key={`deal:${id}:${rev}`} entity="deal" id={id} canEdit={canEdit && !d.archived} onChanged={(out, text) => changed(out, text)} />
      </div>

      {outcome && <OutcomeDialog deal={d} stage={outcome} onClose={() => setOutcome(null)}
        onDone={(out) => { const s = outcome; setOutcome(null); changed(out, `${d.title}: ${s.name.toLowerCase()}`); }} />}
      {confirmDelete && (
        <div className="modal-backdrop" role="presentation" onClick={() => setConfirmDelete(false)}>
          <div className="hud modal alert" role="dialog" aria-modal="true" aria-label="Delete deal" onClick={(e) => e.stopPropagation()}>
            <header className="hud-head"><h2><span className="hud-code">!</span>Delete {d.title}?</h2></header>
            <p className="modal-body small">This removes the deal and its timeline for good, once someone with approval rights confirms it.
              To keep it out of the pipeline, mark it <b>Lost</b> or <b>Archive</b> it instead.</p>
            <div className="modal-actions">
              <button className="btn ghost small" onClick={() => setConfirmDelete(false)}>Cancel</button>
              <button className="btn danger small" onClick={async () => {
                setConfirmDelete(false);
                try {
                  const a = await api("/crm-actions/requests", { method: "POST", body: { action: "delete", entity: "deal", id } });
                  onToast(a.already_pending ? "Already waiting for approval" : "Deletion request sent for approval"); onChanged?.();
                } catch (e) { setErr(e.message); }
              }}>Ask for approval</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
