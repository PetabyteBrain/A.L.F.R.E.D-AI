import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { fmtAgo, fmtCHF, fmtDate, fmtPhone, fmtTime } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { ErrorNote, Tag } from "../common.jsx";
import { Icon } from "../icons.jsx";
import { STATUS_LABEL } from "./RecordEditor.jsx";
import DocList from "./DocList.jsx";
import TimeLogged from "./TimeLogged.jsx";
import Timeline from "./Timeline.jsx";

/* One company or contact: facts, people, tags, the timeline, change history, and the actions
   (edit, status, archive, ask to merge or delete). */

export const STATUS_TONE = { lead: "", prospect: "busy", client: "ok", partner: "ok", supplier: "", former: "bad", other: "" };
const ACTION_WORD = { create: "added it", update: "changed", archive: "archived it", restore: "restored it", delete: "deleted" };

export function Avatar({ entity, name }) {
  const letters = (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return <span className={`crm-avatar ${entity}`} aria-hidden="true">{entity === "company" ? <Icon name="building" className="lab-ico" /> : letters}</span>;
}

function Fact({ label, children, href }) {
  if (!children) return null;
  return (
    <div className="kv crm-fact"><span>{label}</span>
      {href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <b>{children}</b>}
    </div>
  );
}

function MergeDialog({ entity, record, onDone, onClose }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState([]);
  const [pick, setPick] = useState(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!q.trim()) { setFound([]); return undefined; }
    const t = setTimeout(() => api(`/crm/${entity === "company" ? "companies" : "contacts"}?limit=8&q=${encodeURIComponent(q.trim())}`)
      .then((d) => setFound(d.items.filter((r) => r.id !== record.id))).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q, entity, record.id]);
  async function send() {
    setBusy(true); setErr("");
    try {
      onDone(await api("/crm-actions/requests", { method: "POST",
        body: { action: "merge", entity, id: record.id, merge_into: pick.id, reason } }));
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div className="hud modal" role="dialog" aria-modal="true" aria-label="Merge" onClick={(e) => e.stopPropagation()}>
        <header className="hud-head"><h2>Merge {record.name}</h2></header>
        <p className="muted small">Pick the record to keep. Its timeline{entity === "company" ? ", contacts" : ""} and any details it's
          missing come over from {record.name}, which is then deleted. Someone with approval rights confirms it first.</p>
        <label className="field"><span>Keep</span>
          <input value={q} autoFocus maxLength={80} placeholder={`Search ${entity === "company" ? "companies" : "contacts"}`}
                 onChange={(e) => { setQ(e.target.value); setPick(null); }} />
        </label>
        <div className="crm-pick-list">
          {found.map((r) => (
            <button key={r.id} type="button" className={`crm-pick${pick?.id === r.id ? " active" : ""}`} onClick={() => setPick(r)}>
              <Avatar entity={entity} name={r.name} /><span>{r.name}</span>
              <span className="meta">{entity === "company" ? r.industry : r.company_name}</span>
            </button>
          ))}
        </div>
        <label className="field"><span>Reason (optional)</span>
          <input value={reason} maxLength={200} placeholder="e.g. same company, entered twice" onChange={(e) => setReason(e.target.value)} />
        </label>
        <ErrorNote error={err} />
        <div className="modal-actions">
          <button className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" disabled={!pick || busy} onClick={send}>Ask for approval</button>
        </div>
      </div>
    </div>
  );
}

function DeleteDialog({ entity, record, onDone, onClose }) {
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function send() {
    setBusy(true); setErr("");
    try { onDone(await api("/crm-actions/requests", { method: "POST", body: { action: "delete", entity, id: record.id, reason } })); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div className="hud modal alert" role="dialog" aria-modal="true" aria-label="Delete" onClick={(e) => e.stopPropagation()}>
        <header className="hud-head"><h2><span className="hud-code">!</span>Delete {record.name}?</h2></header>
        <p className="modal-body small">This removes the {entity} and its timeline for good, once someone with approval rights
          confirms it. To just hide it from the lists, <b>Archive</b> it instead (that can be undone).</p>
        <label className="field"><span>Reason</span>
          <input value={reason} maxLength={200} autoFocus placeholder="e.g. created by mistake" onChange={(e) => setReason(e.target.value)} />
        </label>
        <ErrorNote error={err} />
        <div className="modal-actions">
          <button className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn danger small" disabled={busy} onClick={send}>Ask for approval</button>
        </div>
      </div>
    </div>
  );
}

function History({ entity, id, onUndo }) {
  const [open, setOpen] = useState(false);
  const path = `/crm/${entity === "company" ? "companies" : "contacts"}/${id}/history`;
  const { data } = usePoll(() => (open ? api(path) : Promise.resolve(null)), 0, [open, path]);
  return (
    <section className="crm-history">
      <button type="button" className="btn ghost small" onClick={() => setOpen(!open)} aria-expanded={open}>
        History {open ? "▴" : "▾"}
      </button>
      {open && (
        <ul className="crm-history-list">
          {(data || []).map((h) => (
            <li key={h.id} className={h.undone ? "undone" : ""}>
              <span>{h.user_name || "Someone"} {ACTION_WORD[h.action] || h.action}{h.fields.length ? ` ${h.fields.join(", ")}` : ""}</span>
              <span className="meta" title={fmtTime(h.ts)}>{fmtAgo(h.ts)}{h.undone ? " · undone" : ""}</span>
              {h.can_undo && <button type="button" className="linkish" onClick={() => onUndo(h.id)}>Undo</button>}
            </li>
          ))}
          {data && data.length === 0 && <li className="muted">No changes recorded.</li>}
        </ul>
      )}
    </section>
  );
}

export default function RecordView({ entity, id, status, onBack, onOpen, onEdit, onAddContact, onAddDeal, onToast, onChanged,
                                    canBilling, onNewDoc, onOpenDoc, onEmail }) {
  const path = `/crm/${entity === "company" ? "companies" : "contacts"}/${id}`;
  const { data: r, error, reload } = usePoll(() => api(path), 0, [path]);
  const [dialog, setDialog] = useState(null);
  const [menu, setMenu] = useState(false);
  const [err, setErr] = useState("");
  const [rev, setRev] = useState(0);
  const canEdit = !!status?.can?.edit;

  const changed = (out, text) => { reload(); setRev((n) => n + 1); onChanged?.(); if (text) onToast(text, out?.change_id); };
  async function act(fn, text) {
    setErr(""); setMenu(false);
    try { changed(await fn(), text); } catch (e) { setErr(e.message); }
  }
  async function undo(changeId) {
    try { await api("/crm-actions/undo", { method: "POST", body: { change_id: changeId } }); changed(null, "Undone"); }
    catch (e) { setErr(e.message); }
  }

  if (!r) return <div className="crm-detail"><ErrorNote error={error} />{!error && <p className="muted">Loading…</p>}</div>;
  const site = r.website ? r.website.replace(/^https?:\/\//, "").replace(/\/$/, "") : "";
  return (
    <div className="crm-detail">
      <div className="mail-view-head">
        <div className="mail-view-nav">
          <button className="btn ghost small" onClick={onBack}>‹ Back</button>
        </div>
        {canEdit && (
          <div className="mail-view-tools">
            <button className="btn small" onClick={() => onEdit(r)} disabled={r.archived}>Edit</button>
            <button className="btn small" onClick={() => document.getElementById("crm-composer-input")?.focus()} disabled={r.archived}>
              <Icon name="note" className="btn-ico" />Log</button>
            {onEmail && <button className="btn small" onClick={() => onEmail(entity, r)} disabled={r.archived}>
              <Icon name="mail" className="btn-ico" />Email</button>}
            <div className="crm-menu">
              <button className="btn ghost small" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>More ▾</button>
              {menu && (
                <div className="crm-menu-list" role="menu">
                  <button role="menuitem" onClick={() => act(() => api(`${path}/archive`, { method: "POST", body: { archived: !r.archived } }),
                    r.archived ? "Restored" : "Archived")}><Icon name="archive" className="btn-ico" />{r.archived ? "Restore" : "Archive"}</button>
                  <button role="menuitem" onClick={() => { setMenu(false); setDialog("merge"); }}>Merge into…</button>
                  <button role="menuitem" className="danger" onClick={() => { setMenu(false); setDialog("delete"); }}>Delete…</button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <header className="crm-head">
        <Avatar entity={entity} name={r.name} />
        <div className="crm-head-main">
          <h3 className="crm-title">{r.name}</h3>
          <div className="crm-head-meta">
            {entity === "company" ? (
              canEdit && !r.archived ? (
                <select className={`crm-status-select s-${r.status}`} value={r.status} aria-label="Status"
                        onChange={(e) => act(() => api(path, { method: "PATCH", body: { fields: { status: e.target.value } } }),
                          `Status: ${STATUS_LABEL[e.target.value]}`)}>
                  {(status?.statuses || []).map((s) => <option key={s} value={s}>{STATUS_LABEL[s] || s}</option>)}
                </select>
              ) : <Tag kind={STATUS_TONE[r.status]}>{STATUS_LABEL[r.status] || r.status}</Tag>
            ) : (
              <span>{r.title}{r.title && r.company ? " at " : ""}
                {r.company && <button type="button" className="linkish" onClick={() => onOpen("company", r.company.id)}>{r.company.name}</button>}
              </span>
            )}
            {r.archived && <Tag kind="bad">archived</Tag>}
            {r.tags.map((t) => <span key={t} className="chip crm-tag">{t}</span>)}
          </div>
        </div>
      </header>
      <ErrorNote error={err} />

      <div className="crm-detail-grid">
        <div className="crm-facts-col">
          <div className="crm-facts">
            <Fact label="Email" href={r.email ? `mailto:${r.email}` : null}>{r.email}</Fact>
            <Fact label="Phone" href={r.phone ? `tel:${r.phone}` : null}>{fmtPhone(r.phone)}</Fact>
            {entity === "company" && <Fact label="Website" href={r.website || null}>{site}</Fact>}
            {entity === "company" && <Fact label="Industry">{r.industry}</Fact>}
            {entity === "company" && r.address && <div className="kv crm-fact"><span>Address</span><b className="pre">{r.address}</b></div>}
            <Fact label="Owner">{r.owner_name || "—"}</Fact>
            <Fact label="Source">{r.source}</Fact>
            {entity === "company" && status?.timetrack && <Fact label="Time tag">{r.timetrack_tag}</Fact>}
            {Object.entries(r.custom).map(([k, v]) => <Fact key={k} label={k}>{v}</Fact>)}
            <Fact label="Added">{`${fmtAgo(r.created_at)}${r.created_by_name ? ` by ${r.created_by_name}` : ""}`}</Fact>
          </div>
          {r.notes && <div className="crm-notes"><h3 className="sub">Notes</h3><p className="pre">{r.notes}</p></div>}

          {entity === "company" && (
            <div className="crm-people">
              <h3 className="sub">People {r.contacts.length > 0 && <span className="mail-count plain">{r.contacts.length}</span>}</h3>
              {r.contacts.length === 0 && <p className="muted small">No contacts yet.</p>}
              {r.contacts.map((c) => (
                <button key={c.id} type="button" className="crm-person" onClick={() => onOpen("contact", c.id)}>
                  <Avatar entity="contact" name={c.name} />
                  <span className="crm-person-main"><b>{c.name}</b><span className="meta">{[c.title, c.email || fmtPhone(c.phone)].filter(Boolean).join(" · ")}</span></span>
                </button>
              ))}
              {canEdit && !r.archived && (
                <button type="button" className="btn ghost small" onClick={() => onAddContact(r)}><Icon name="plus" className="btn-ico" />Add a person</button>
              )}
            </div>
          )}
          {entity === "company" && (
            <div className="crm-people">
              <h3 className="sub">Deals {r.deals.length > 0 && <span className="mail-count plain">{r.deals.length}</span>}</h3>
              {r.deals.length === 0 && <p className="muted small">No deals yet.</p>}
              {r.deals.map((d) => (
                <button key={d.id} type="button" className={`crm-person crm-deal-mini k-${d.stage_kind}`} onClick={() => onOpen("deal", d.id)}>
                  <span className="crm-person-main"><b>{d.title}</b>
                    <span className="meta">{d.stage_name} · {fmtCHF(d.value)}{d.expected_close && d.open ? ` · ${fmtDate(d.expected_close)}` : ""}</span></span>
                </button>
              ))}
              {canEdit && !r.archived && onAddDeal && (
                <button type="button" className="btn ghost small" onClick={() => onAddDeal(r)}><Icon name="plus" className="btn-ico" />New deal</button>
              )}
            </div>
          )}
          {entity === "company" && status?.timetrack && <TimeLogged companyId={id} />}
          {entity === "company" && canBilling && (
            <DocList filter={`company_id=${id}`} preset={{ company_id: id }} onOpenDoc={onOpenDoc} onNewDoc={onNewDoc} />
          )}
          <History key={rev} entity={entity} id={id} onUndo={undo} />
        </div>
        <Timeline key={`${entity}:${id}`} entity={entity} id={id} canEdit={canEdit && !r.archived} onChanged={(out, text) => changed(out, text)} />
      </div>

      {dialog === "merge" && <MergeDialog entity={entity} record={r} onClose={() => setDialog(null)}
        onDone={(a) => { setDialog(null); onToast(a.already_pending ? "Already waiting for approval" : "Merge request sent for approval"); onChanged?.(); }} />}
      {dialog === "delete" && <DeleteDialog entity={entity} record={r} onClose={() => setDialog(null)}
        onDone={(a) => { setDialog(null); onToast(a.already_pending ? "Already waiting for approval" : "Deletion request sent for approval"); onChanged?.(); }} />}
    </div>
  );
}
