import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePageActive, usePoll, useStoredState } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";
import { Icon } from "../components/icons.jsx";
import MailAttachments from "../components/MailAttachments.jsx";
import TemplateManager from "../components/TemplateManager.jsx";

const PAGE = 50;
const KIND_LABEL = {
  request: "to Alfred", reply: "reply", inquiry: "inquiry", unverified: "sender not verified", automated: "automated",
  spam: "spam", sent: "sent", auto_reply: "Alfred's answer", notice: "notice", unsorted: "new",
};
const KIND_TONE = { inquiry: "busy", unverified: "bad", reply: "ok" };
const BOXES = [
  { id: "inbox", label: "Inbox", icon: "inbox", count: "inbox" },
  { id: "unread", label: "Unread", icon: "mail" },
  { id: "starred", label: "Starred", icon: "star", count: "starred", plain: true },
  { id: "inquiries", label: "Inquiries", icon: "alert", count: "inquiries", team: true },
  { id: "sent", label: "Sent", icon: "send" },
  { id: "archived", label: "Archived", icon: "archive" },
  { id: "all", label: "All mail", icon: "folder" },
];

function who(m) {
  if (m.direction === "in") return m.from_name || m.from_addr;
  return `To ${(m.to || []).join(", ")}`;
}

function LabelChip({ l, onRemove }) {
  return (
    <span className={`lab lab-${l.color}`} title={l.kind === "group" ? "Group" : "Tag"}>
      {l.kind === "group" && <Icon name="folder" className="lab-ico" />}{l.name}
      {onRemove && <button type="button" onClick={onRemove} aria-label={`Remove ${l.name}`}>×</button>}
    </span>
  );
}

/* Sidebar: one section of groups or tags, with create / rename / recolour / delete. */
function LabelSection({ kind, scope, title, labels, colors, active, onPick, onChanged }) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState("");
  const list = labels.filter((l) => l.kind === kind && l.scope === scope);

  async function run(fn) {
    setErr("");
    try { await fn(); onChanged(); return true; } catch (e) { setErr(e.message); return false; }
  }
  const create = (e) => {
    e.preventDefault();
    run(() => api("/mail/labels", { method: "POST", body: { kind, scope, name, color: colors[(list.length + 1) % colors.length] } }))
      .then((ok) => { if (ok) { setName(""); setAdding(false); } });
  };
  const save = (e) => {
    e.preventDefault();
    run(() => api(`/mail/labels/${Number(edit.id)}`, { method: "PATCH", body: { name: edit.name, color: edit.color } }))
      .then((ok) => ok && setEdit(null));
  };
  const remove = (l) => {
    if (window.confirm(`Delete the ${kind} “${l.name}”? The emails stay; only the ${kind} goes.`))
      run(() => api(`/mail/labels/${Number(l.id)}`, { method: "DELETE" })).then(() => setEdit(null));
  };

  return (
    <div className="mail-side-sec">
      <div className="mail-side-head">
        <span>{title}</span>
        <button className="icon-btn tiny" type="button" onClick={() => setAdding(!adding)} aria-label={`New ${kind}`}
                title={`New ${kind}`}>+</button>
      </div>
      {adding && (
        <form className="mail-side-form" onSubmit={create}>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} placeholder={`New ${kind}`} autoFocus
                 aria-label={`New ${kind} name`} />
        </form>
      )}
      {list.length === 0 && !adding && <div className="muted tiny mail-side-empty">None yet</div>}
      {list.map((l) => edit?.id === l.id ? (
        <form key={l.id} className="mail-side-form" onSubmit={save}>
          <input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} maxLength={40} autoFocus
                 aria-label="Name" />
          <div className="swatches">
            {colors.map((c) => (
              <button key={c} type="button" className={`swatch lab-${c}${edit.color === c ? " on" : ""}`}
                      onClick={() => setEdit({ ...edit, color: c })} aria-label={c} />
            ))}
          </div>
          <div className="mail-side-actions">
            <button className="btn small" type="submit">Save</button>
            <button className="btn ghost small" type="button" onClick={() => setEdit(null)}>Cancel</button>
            <button className="btn danger small" type="button" onClick={() => remove(l)}>Delete</button>
          </div>
        </form>
      ) : (
        <div key={l.id} className={`mail-side-item${active === l.id ? " active" : ""}`}>
          <button type="button" className="mail-side-pick" onClick={() => onPick(l)}>
            <span className={`dot lab-${l.color}`} />{l.name}
            {l.unread > 0 ? <b className="mail-count">{l.unread}</b> : <span className="mail-count muted">{l.total || ""}</span>}
          </button>
          <button type="button" className="icon-btn tiny mail-side-edit" onClick={() => setEdit({ ...l })}
                  aria-label={`Edit ${l.name}`} title="Rename, colour, delete">✎</button>
        </div>
      ))}
      {err && <div className="fail-note">{err}</div>}
    </div>
  );
}

/* Group / tag pickers for one or more emails of one scope. */
function Organize({ ids, scope, labels, current = [], onDone }) {
  const [err, setErr] = useState("");
  const groups = labels.filter((l) => l.kind === "group" && (!scope || l.scope === scope));
  const tags = labels.filter((l) => l.kind === "tag" && (!scope || l.scope === scope) && !current.some((c) => c.id === l.id));
  const group = current.find((c) => c.kind === "group");
  async function send(body) {
    setErr("");
    try { await api("/mail/bulk", { method: "POST", body: { ids, ...body } }); onDone(); } catch (e) { setErr(e.message); }
  }
  return (
    <span className="mail-organize">
      <select value={group?.id || ""} aria-label="Group"
              onChange={(e) => send({ set_group: true, group: e.target.value ? Number(e.target.value) : null })}>
        <option value="">{group ? "No group" : "Move to group…"}</option>
        {groups.map((g) => <option key={g.id} value={g.id}>{g.scope === "company" && !scope ? "Team · " : ""}{g.name}</option>)}
      </select>
      <select value="" aria-label="Add tag" onChange={(e) => e.target.value && send({ add_tags: [Number(e.target.value)] })}>
        <option value="">Add tag…</option>
        {tags.map((t) => <option key={t.id} value={t.id}>{t.scope === "company" && !scope ? "Team · " : ""}{t.name}</option>)}
      </select>
      {err && <span className="fail-note">{err}</span>}
    </span>
  );
}

/* One email with its whole conversation. */
function Reader({ id, team, labels, onBack, onPrev, onNext, onChanged }) {
  const { data: m, error, reload } = usePoll(() => api(`/mail/${Number(id)}`), 0, [id]);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(() => new Set());
  useEffect(() => { setNote(""); setErr(""); setOpen(new Set([Number(id)])); }, [id]);
  const refresh = useCallback(() => { reload(); onChanged(); }, [reload, onChanged]);
  if (!m) return <ErrorNote error={error} />;

  async function bulk(body) {
    setErr("");
    try { await api("/mail/bulk", { method: "POST", body: { ids: [m.id], ...body } }); refresh(); } catch (e) { setErr(e.message); }
  }
  async function close() {
    try { await api(`/mail/escalations/${Number(m.escalation.id)}/close`, { method: "POST", body: { note } }); refresh(); }
    catch (e) { setErr(e.message); }
  }
  const toggle = (tid) => setOpen((s) => { const n = new Set(s); if (n.has(tid)) n.delete(tid); else n.add(tid); return n; });

  return (
    <div className="mail-view">
      <div className="mail-view-head">
        <div className="mail-view-nav">
          <button className="btn small ghost" onClick={onBack} title="Back to the list (Esc)">← Back</button>
          <button className="btn small ghost" onClick={onPrev} disabled={!onPrev} title="Newer (k)" aria-label="Newer">↑</button>
          <button className="btn small ghost" onClick={onNext} disabled={!onNext} title="Older (j)" aria-label="Older">↓</button>
        </div>
        <div className="mail-view-tools">
          <button className={`btn small ghost${m.starred ? " on" : ""}`} onClick={() => bulk({ starred: !m.starred })}
                  title="Star (s)"><Icon name="star" className="btn-ico" />{m.starred ? "Starred" : "Star"}</button>
          <button className="btn small ghost" onClick={() => bulk({ archived: !m.archived })} title="Archive (e)">
            <Icon name="archive" className="btn-ico" />{m.archived ? "Move to inbox" : "Archive"}</button>
          {m.direction === "in" && <button className="btn small ghost" onClick={() => bulk({ read: false }).then(onBack)}>Mark unread</button>}
          <a className="btn small ghost" href={`/api/mail/${Number(m.id)}/raw`} download>.eml</a>
        </div>
      </div>
      <h3 className="mail-subject">{m.subject || "(no subject)"}</h3>
      <div className="mail-labels-row">
        <Tag kind={KIND_TONE[m.kind] || ""}>{KIND_LABEL[m.kind] || m.kind}</Tag>
        {m.scope === "company" && <Tag>company</Tag>}
        {m.labels.map((l) => (
          <LabelChip key={l.id} l={l} onRemove={() => bulk(l.kind === "group" ? { set_group: true, group: null } : { remove_tags: [l.id] })} />
        ))}
        <Organize ids={[m.id]} scope={m.scope} labels={labels} current={m.labels} onDone={refresh} />
      </div>
      {m.auth && (
        <div className="meta tiny">
          Sender check: {m.auth.dmarc ? <Tag kind="ok">DMARC aligned</Tag> : <Tag kind="bad">not aligned</Tag>}{" "}
          SPF {m.auth.spf || "none"} · DKIM {m.auth.dkim ? "pass" : "none"}{m.auth.spam && <> · <Tag kind="bad">spam</Tag></>}
          {m.archived_nas ? " · filed on the NAS" : " · not filed yet"}
        </div>
      )}
      {m.summary && <p className="mail-summary small"><b>{"Alfred: "}</b>{m.summary}</p>}
      {m.escalation && (
        <div className="mail-escalation">
          <span>Escalation #{m.escalation.id}: {m.escalation.topic} · <Tag kind={m.escalation.status === "open" ? "busy" : "ok"}>{m.escalation.status}</Tag></span>
          {team && m.escalation.status === "open" && (
            <form className="field-row" onSubmit={(e) => { e.preventDefault(); close(); }}>
              <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Note (optional)" aria-label="Note" />
              <button className="btn small" type="submit">Mark handled</button>
            </form>
          )}
          <span className="muted tiny">To answer, ask Alfred in the chat or on Telegram (“reply to email #{m.id}: …”).</span>
        </div>
      )}
      <ErrorNote error={err} />
      <div className="mail-thread">
        {m.thread.map((t) => {
          const isOpen = open.has(t.id) || m.thread.length === 1;
          return (
            <div key={t.id} className={`mail-msg${t.direction === "out" ? " out" : ""}${t.id === m.id ? " current" : ""}`}>
              <button type="button" className="mail-msg-head" onClick={() => toggle(t.id)} aria-expanded={isOpen}>
                <span className="mail-who">{t.direction === "in" ? (t.from_name ? `${t.from_name} <${t.from_addr}>` : t.from_addr)
                                                                : `${t.from_name || "Alfred"} → ${(t.to || []).join(", ")}`}</span>
                <span className="meta">{fmtTime(t.created_at)}</span>
              </button>
              {isOpen ? (
                <>
                  {t.cc?.length > 0 && <div className="meta">Cc {t.cc.join(", ")}</div>}
                  <pre className="mail-text">{t.text || "(no text)"}</pre>
                  <MailAttachments emailId={t.id} items={t.attachments} team={team} onChanged={reload} />
                </>
              ) : <div className="meta mail-snippet">{(t.text || "").slice(0, 140)}</div>}
            </div>
          );
        })}
      </div>
      {m.error && <div className="fail-note">{m.error}</div>}
    </div>
  );
}

function Escalations({ onOpen }) {
  const [status, setStatus] = useState("open");
  const { data, error } = usePoll(() => api(`/mail/escalations?status=${status}`), 30000, [status]);
  return (
    <div>
      <div className="seg">
        {["open", "handled", "all"].map((s) => (
          <button key={s} className={s === status ? "active" : ""} onClick={() => setStatus(s)}>{s}</button>
        ))}
      </div>
      <ErrorNote error={error} />
      {data && data.length === 0 && <Empty>Nothing {status === "open" ? "waiting for the team" : "here"}.</Empty>}
      <div className="list">
        {(data || []).map((e) => (
          <div className="row" key={e.id}>
            <div>
              <div>{e.topic} {e.urgency !== "normal" && <Tag kind={e.urgency === "low" ? "" : "bad"}>{e.urgency}</Tag>}
                {" "}<Tag kind={e.status === "open" ? "busy" : "ok"}>{e.status}</Tag></div>
              <div className="meta">#{e.id} · {e.requester || "someone"} by {e.source} · {fmtTime(e.created_at)}
                {e.notified?.length > 0 && <> · told {e.notified.map((n) => `${n.name} (${n.via})`).join(", ")}</>}</div>
              <div className="small">{e.summary}</div>
              {e.note && <div className="meta">Note: {e.note}{e.handled_by_name ? ` (${e.handled_by_name})` : ""}</div>}
            </div>
            {e.email_id && <div className="row-actions"><button className="btn small" onClick={() => onOpen(e.email_id)}>Open email</button></div>}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Mail({ assistant }) {
  const active = usePageActive();
  const { data: st, reload: reloadStatus } = usePoll(() => api("/mail/status"), 60000);
  const { data: labels, reload: reloadLabels } = usePoll(() => api("/mail/labels"), 60000);
  const [savedBox, setSavedBox] = useStoredState("alfred-mail-box", "inbox");
  const [view, setViewState] = useState(() => ({ box: savedBox }));          // {box} | {box, label} | {box: "team"}
  const setView = (v) => { setViewState(v); if (!v.label) setSavedBox(v.box); };
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState(null);
  const [sel, setSel] = useState(() => new Set());
  const [err, setErr] = useState("");
  const [showLabels, setShowLabels] = useState(false);       // phones: groups & tags folded away
  const [templates, setTemplates] = useState(false);         // the Template manager instead of the mailbox
  const team = !!st?.team;
  const colors = st?.colors || ["slate"];
  const allLabels = labels || [];
  const label = view.label ? allLabels.find((l) => l.id === view.label) : null;
  const listing = view.box !== "team";

  const url = useMemo(() => {
    const p = new URLSearchParams({ box: label ? "all" : view.box, offset: String(offset), limit: String(PAGE) });
    if (label) p.set(label.kind, String(label.id));
    if (query) p.set("q", query);
    if (unreadOnly) p.set("unread", "true");
    return `/mail?${p}`;
  }, [view, label, offset, query, unreadOnly]);
  const { data, error, reload } = usePoll(() => (listing ? api(url) : Promise.resolve(null)), 30000, [url, listing]);
  const emails = data?.emails || [];

  useEffect(() => { setSel(new Set()); }, [url]);
  const changed = useCallback(() => { reload(); reloadStatus(); reloadLabels(); }, [reload, reloadStatus, reloadLabels]);
  const pick = (v) => { setView(v); setOffset(0); setOpenId(null); setSel(new Set()); };

  const idx = emails.findIndex((e) => e.id === openId);
  const prev = idx > 0 ? () => setOpenId(emails[idx - 1].id) : null;
  const next = idx >= 0 && idx < emails.length - 1 ? () => setOpenId(emails[idx + 1].id) : null;

  async function bulk(body, ids = [...sel]) {
    setErr("");
    if (!ids.length) return;
    try { await api("/mail/bulk", { method: "POST", body: { ids, ...body } }); changed(); }
    catch (e) { setErr(e.message); }
  }

  // Keyboard: j/k move, Enter opens, Esc back, s star, e archive (not while typing).
  useEffect(() => {
    if (!active || !listing) return undefined;
    const onKey = (e) => {
      if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (openId) {
        if (e.key === "Escape") setOpenId(null);
        else if (e.key === "j" && next) next();
        else if (e.key === "k" && prev) prev();
        else if (e.key === "s") bulk({ starred: !emails[idx]?.starred }, [openId]);
        else if (e.key === "e") { bulk({ archived: !emails[idx]?.archived }, [openId]); if (next) next(); else setOpenId(null); }
      } else if (e.key === "j" || e.key === "k") {
        if (emails.length) setOpenId(emails[e.key === "j" ? 0 : emails.length - 1].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const selected = emails.filter((e) => sel.has(e.id));
  const selScope = selected.length && selected.every((e) => e.scope === selected[0].scope) ? selected[0].scope : null;
  const allSel = emails.length > 0 && selected.length === emails.length;
  const title = label ? `${label.kind === "group" ? "Group" : "Tag"} · ${label.name}`
    : view.box === "team" ? "For the team" : BOXES.find((b) => b.id === view.box)?.label || "Mail";

  return (
    <Panel code="12" title={templates ? "Template manager" : "Mail"}
           actions={st?.enabled && (
             <button className="btn small" onClick={() => setTemplates(!templates)}>
               <Icon name={templates ? "inbox" : "folder"} className="btn-ico" />{templates ? "Back to mail" : "Template manager"}
             </button>
           )}>
      {templates ? <TemplateManager onBack={() => setTemplates(false)} /> : <>
      {st && !st.enabled && <Empty>{assistant}'s email isn't set up yet (MAIL_ADDRESS and SMTP_* in .env).</Empty>}
      {st?.enabled && (
        <p className="muted small mail-intro">
          {assistant} writes from <b>{st.address}</b>{st.receiving ? " and reads what arrives there" : " (receiving is off)"}.
          Registered people get what their role allows; anyone else gets public information only, and whatever needs a
          person goes to the team. Everything is filed on the NAS.
        </p>
      )}
      <div className="mail-layout">
        <nav className="mail-side" aria-label="Mailboxes">
          {BOXES.filter((b) => !b.team || team).map((b) => {
            const n = b.count ? st?.counts?.[b.count] : 0;
            return (
              <button key={b.id} type="button" className={`mail-side-box${!label && view.box === b.id ? " active" : ""}`}
                      onClick={() => pick({ box: b.id })}>
                <Icon name={b.icon} className="lab-ico" />{b.label}
                {n > 0 && <b className={`mail-count${b.plain ? " plain" : ""}`}>{n}</b>}
              </button>
            );
          })}
          {team && (
            <button type="button" className={`mail-side-box${view.box === "team" ? " active" : ""}`} onClick={() => pick({ box: "team" })}>
              <Icon name="people" className="lab-ico" />For the team
              {st?.open_escalations > 0 && <b className="mail-count">{st.open_escalations}</b>}
            </button>
          )}
          <button type="button" className="mail-side-toggle btn ghost small" onClick={() => setShowLabels(!showLabels)}
                  aria-expanded={showLabels}>Groups &amp; tags {showLabels ? "▴" : "▾"}</button>
          <div className={`mail-side-labels${showLabels ? " open" : ""}`}>
          <LabelSection kind="group" scope="personal" title={team ? "My groups" : "Groups"} labels={allLabels} colors={colors}
                        active={view.label} onPick={(l) => pick({ box: "all", label: l.id })} onChanged={changed} />
          <LabelSection kind="tag" scope="personal" title={team ? "My tags" : "Tags"} labels={allLabels} colors={colors}
                        active={view.label} onPick={(l) => pick({ box: "all", label: l.id })} onChanged={changed} />
          {team && <>
            <LabelSection kind="group" scope="company" title="Team groups" labels={allLabels} colors={colors}
                          active={view.label} onPick={(l) => pick({ box: "all", label: l.id })} onChanged={changed} />
            <LabelSection kind="tag" scope="company" title="Team tags" labels={allLabels} colors={colors}
                          active={view.label} onPick={(l) => pick({ box: "all", label: l.id })} onChanged={changed} />
          </>}
          </div>
        </nav>

        <section className="mail-main">
          {!listing ? <Escalations onOpen={(id) => { setOpenId(id); setView({ box: "all" }); }} /> : openId ? (
            <Reader id={openId} team={team} labels={allLabels} onBack={() => setOpenId(null)} onPrev={prev} onNext={next}
                    onChanged={changed} />
          ) : (
            <>
              <div className="mail-toolbar">
                <label className="mail-check" title="Select all on this page">
                  <input type="checkbox" checked={allSel} aria-label="Select all"
                         onChange={() => setSel(allSel ? new Set() : new Set(emails.map((e) => e.id)))} />
                </label>
                {selected.length > 0 ? (
                  <div className="mail-bulk">
                    <span className="muted small">{selected.length} selected</span>
                    <button className="btn small ghost" onClick={() => bulk({ read: true })}>Read</button>
                    <button className="btn small ghost" onClick={() => bulk({ read: false })}>Unread</button>
                    <button className="btn small ghost" onClick={() => bulk({ starred: !selected.every((e) => e.starred) })}>
                      <Icon name="star" className="btn-ico" />{selected.every((e) => e.starred) ? "Unstar" : "Star"}</button>
                    <button className="btn small ghost" onClick={() => bulk({ archived: view.box !== "archived" })}>
                      <Icon name="archive" className="btn-ico" />{view.box === "archived" ? "Move to inbox" : "Archive"}</button>
                    {selScope ? <Organize ids={[...sel]} scope={selScope} labels={allLabels} onDone={changed} />
                      : <span className="muted tiny">Groups and tags: choose personal or company mail only</span>}
                  </div>
                ) : (
                  <div className="mail-filters">
                    <b className="mail-title">{title}</b>
                    <label className="mail-unread-toggle"><input type="checkbox" checked={unreadOnly}
                           onChange={(e) => { setUnreadOnly(e.target.checked); setOffset(0); }} /> Unread only</label>
                    <form className="mail-search" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); setOffset(0); }}>
                      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search mail" maxLength={80} aria-label="Search mail" />
                      {query && <button type="button" className="btn ghost small" onClick={() => { setQ(""); setQuery(""); }}>Clear</button>}
                    </form>
                  </div>
                )}
                {data && data.total > PAGE && (
                  <div className="mail-pager">
                    <span className="muted tiny">{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</span>
                    <button className="btn small ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} aria-label="Newer page">‹</button>
                    <button className="btn small ghost" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)} aria-label="Older page">›</button>
                  </div>
                )}
              </div>
              <ErrorNote error={err || error} />
              {data && emails.length === 0 && <Empty>No mail here{query ? " for that search" : ""}.</Empty>}
              <div className="mail-list">
                {emails.map((m) => (
                  <div key={m.id} className={`mail-row${m.read ? "" : " unread"}${sel.has(m.id) ? " selected" : ""}`}>
                    <input type="checkbox" checked={sel.has(m.id)} aria-label="Select"
                           onChange={() => setSel((s) => { const n = new Set(s); if (n.has(m.id)) n.delete(m.id); else n.add(m.id); return n; })} />
                    <button type="button" className={`mail-star${m.starred ? " on" : ""}`} aria-label={m.starred ? "Unstar" : "Star"}
                            onClick={() => bulk({ starred: !m.starred }, [m.id])}><Icon name="star" className="lab-ico" /></button>
                    <button type="button" className="mail-row-body" onClick={() => setOpenId(m.id)}>
                      <span className="mail-row-top">
                        <span className="mail-who" title={who(m)}>{who(m)}</span>
                        <span className="meta">{fmtTime(m.created_at)}</span>
                      </span>
                      <span className="mail-row-subject">
                        <span className="mail-subj-text">{m.subject || "(no subject)"}</span>
                        {m.kind !== "request" && m.kind !== "sent" && <Tag kind={KIND_TONE[m.kind] || ""}>{KIND_LABEL[m.kind] || m.kind}</Tag>}
                        {m.labels.map((l) => <LabelChip key={l.id} l={l} />)}
                        {m.attachments.length > 0 && <span className="muted tiny">📎 {m.attachments.length}</span>}
                      </span>
                      {m.summary && <span className="meta mail-row-summary">{m.summary}</span>}
                    </button>
                  </div>
                ))}
              </div>
              {emails.length > 0 && <p className="muted tiny mail-keys">Keys: j / k next and previous · s star · e archive · Esc back</p>}
            </>
          )}
        </section>
      </div>
      </>}
    </Panel>
  );
}
