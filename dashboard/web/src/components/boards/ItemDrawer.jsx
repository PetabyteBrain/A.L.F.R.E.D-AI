import { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { fmtAgo, fmtCHF, fmtDate, fmtTime } from "../../format.js";
import { navigate, usePoll } from "../../hooks.js";
import { can, ErrorNote, Tag } from "../common.jsx";
import { initials } from "./BoardView.jsx";

/* One work item, opened beside the board: every field edits in place (saved at once, with history), sub-items,
   company files, comments with @mentions, links to the CRM. Deals from the sales pipeline open in the CRM. */

function FilePicker({ onPick, onClose }) {
  const [path, setPath] = useState("");
  const { data, error } = usePoll(() => api(`/nas/browse?${new URLSearchParams({ scope: "company", path })}`), 0, [path]);
  const crumbs = path ? path.split("/") : [];
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="hud modal editor" role="dialog" aria-modal="true" aria-label="Attach a company file" onClick={(e) => e.stopPropagation()}>
        <header className="hud-head"><h2>Attach a company file</h2>
          <button type="button" className="btn ghost small" onClick={onClose}>Close</button></header>
        <div className="crumbs">
          <button className="link" onClick={() => setPath("")}>Company</button>
          {crumbs.map((c, i) => <span key={i}> / <button className="link" onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}>{c}</button></span>)}
        </div>
        <ErrorNote error={error} />
        <div className="file-list bd-picker">
          {path && <button type="button" className="file-name" onClick={() => setPath(path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "")}>↑ Up</button>}
          {(data?.items || []).map((it) => (
            <button key={it.name} type="button" className="file-name" onClick={() => (it.dir ? setPath(path ? `${path}/${it.name}` : it.name) : onPick(path ? `${path}/${it.name}` : it.name))}>
              <span aria-hidden="true">{it.dir ? "📁" : "📄"}</span> {it.name}
            </button>
          ))}
          {data && data.items.length === 0 && <p className="muted small">Empty folder.</p>}
        </div>
      </div>
    </div>
  );
}

function Picker({ kind, value, label, onPick }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState([]);
  useEffect(() => {
    if (!q.trim()) { setFound([]); return undefined; }
    const t = setTimeout(() => api(`/crm/${kind === "company" ? "companies" : "deals"}?limit=8&${kind === "deal" ? "status=all&" : ""}q=${encodeURIComponent(q.trim())}`)
      .then((d) => setFound(d.items)).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q, kind]);
  return (
    <div className="bd-picker-inline">
      {value ? (
        <span className="bd-linked">{label}<button type="button" className="icon-x" aria-label="Remove" onClick={() => onPick(null)}>×</button></span>
      ) : (
        <>
          <input value={q} maxLength={80} placeholder={`Search ${kind === "company" ? "clients" : "deals"}…`} onChange={(e) => setQ(e.target.value)} />
          {found.length > 0 && (
            <div className="bd-picker-list">
              {found.map((r) => <button key={r.id} type="button" onClick={() => { onPick(r); setQ(""); setFound([]); }}>{r.name || r.title}
                {r.company_name ? <span className="meta"> · {r.company_name}</span> : null}</button>)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function ItemDrawer({ id, board, card, me, onClose, onChanged, onOpenItem, onToast }) {
  const isDeal = board?.kind === "pipeline";
  const { data: it, error, reload } = usePoll(() => (isDeal ? Promise.resolve(null) : api(`/boards/items/${id}`)), 0, [id, isDeal]);
  const [err, setErr] = useState("");
  const [draft, setDraft] = useState({});
  const [comment, setComment] = useState("");
  const [child, setChild] = useState("");
  const [tagIn, setTagIn] = useState("");
  const [picker, setPicker] = useState(false);
  const [hist, setHist] = useState(false);
  const [busy, setBusy] = useState(false);
  const titleRef = useRef(null);
  useEffect(() => { if (it) setDraft({ title: it.title, description: it.description, estimate: it.estimate ?? "" }); }, [it?.id, it?.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !picker && !e.target.closest("input, textarea, select")) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, picker]);

  async function save(fields) {
    setErr("");
    try { await api(`/boards/items/${id}`, { method: "PATCH", body: { fields } }); await reload(); onChanged?.(); return true; }
    catch (e) { setErr(e.message); return false; }
  }
  async function post(path, body, method = "POST") {
    setBusy(true); setErr("");
    try { await api(path, { method, body }); await reload(); onChanged?.(); return true; }
    catch (e) { setErr(e.message); return false; } finally { setBusy(false); }
  }

  if (isDeal) {
    const d = card || board.items?.find((x) => x.id === id);
    return (
      <aside className="bd-drawer" aria-label="Deal">
        <header className="bd-drawer-head"><span className="bd-key">DEAL-{id}</span><span className="bd-type">Deal</span>
          <button type="button" className="btn ghost small" onClick={onClose}>Close</button></header>
        {d && <>
          <h3 className="bd-drawer-title">{d.title}</h3>
          <div className="crm-facts">
            <div className="kv crm-fact"><span>Stage</span><b>{d.state_name}</b></div>
            <div className="kv crm-fact"><span>Value</span><b>{fmtCHF(d.value)}</b></div>
            {d.company_name && <div className="kv crm-fact"><span>Client</span><b>{d.company_name}</b></div>}
            {d.assignee_name && <div className="kv crm-fact"><span>Owner</span><b>{d.assignee_name}</b></div>}
            {d.due_date && <div className="kv crm-fact"><span>Expected close</span><b className={d.overdue ? "warn-text" : ""}>{fmtDate(d.due_date)}</b></div>}
            {d.next_step && <div className="kv crm-fact"><span>Next step</span><b>{d.next_step}</b></div>}
          </div>
        </>}
        <p className="muted small">Deals live in the CRM: their stage moves here and everywhere at once.</p>
        <button className="btn primary small" onClick={() => navigate("crm", { deal: id })}>Open in the CRM</button>
      </aside>
    );
  }
  if (!it) return <aside className="bd-drawer"><ErrorNote error={error} />{!error && <p className="muted">Loading…</p>}</aside>;
  const ed = it.can_edit && !it.archived;
  const states = (board.states || []).filter((s) => !s.archived);
  const types = (board.types || []).filter((t) => !t.archived);
  const people = board.people || [];
  const teams = board.teams || [];
  const parents = (board.items || []).filter((c) => c.id !== it.id && c.kind !== "deal");
  return (
    <aside className="bd-drawer" aria-label={`${it.key} ${it.title}`}>
      <header className="bd-drawer-head">
        <span className="bd-type-dot" style={{ "--type": it.type_color }} />
        {ed ? (
          <select className="crm-sort" value={it.type_id || ""} aria-label="Type" onChange={(e) => save({ type_id: Number(e.target.value) })}>
            {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        ) : <span className="bd-type">{it.type_name}</span>}
        <span className="bd-key">{it.key}</span>
        {it.archived && <Tag kind="bad">removed</Tag>}
        <span className="bd-drawer-tools">
          {it.can_edit && <button type="button" className="btn ghost small" disabled={busy}
                                  onClick={async () => { if (await post(`/boards/items/${id}/archive`, { archived: !it.archived })) onToast?.(it.archived ? "Restored" : `Removed ${it.key}`); }}>
            {it.archived ? "Restore" : "Remove"}</button>}
          <button type="button" className="btn ghost small" onClick={onClose}>Close</button>
        </span>
      </header>
      <ErrorNote error={err} />
      {ed ? (
        <textarea ref={titleRef} className="bd-drawer-title edit" rows={1} value={draft.title ?? ""} maxLength={200} aria-label="Title"
                  onChange={(e) => setDraft({ ...draft, title: e.target.value.replace(/\n/g, " ") })}
                  onBlur={() => draft.title?.trim() && draft.title !== it.title && save({ title: draft.title })}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } }} />
      ) : <h3 className="bd-drawer-title">{it.title}</h3>}

      <div className="bd-fields">
        <label className="field"><span>State</span>
          <select value={it.state_id} disabled={!ed} onChange={(e) => save({ state_id: Number(e.target.value) })}>
            {states.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select></label>
        <label className="field"><span>Assigned to</span>
          <select value={it.assignee_id || ""} disabled={!ed} onChange={(e) => save({ assignee_id: Number(e.target.value) || null })}>
            <option value="">Nobody yet</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.display_name}{me && p.id === me.id ? " (me)" : ""}</option>)}
          </select></label>
        <label className="field"><span>Team</span>
          <select value={it.team_id || ""} disabled={!ed} onChange={(e) => save({ team_id: Number(e.target.value) || null })}>
            <option value="">No team</option>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select></label>
        <label className="field"><span>Priority</span>
          <select value={it.priority} disabled={!ed} onChange={(e) => save({ priority: Number(e.target.value) })}>
            <option value={1}>Urgent</option><option value={2}>Normal</option><option value={3}>Low</option>
          </select></label>
        <label className="field"><span>Due</span>
          <input type="date" value={it.due_date || ""} disabled={!ed} onChange={(e) => save({ due_date: e.target.value || null })} /></label>
        <label className="field"><span>Start</span>
          <input type="date" value={it.start_date || ""} disabled={!ed} onChange={(e) => save({ start_date: e.target.value || null })} /></label>
        <label className="field"><span>Estimate (h)</span>
          <input inputMode="decimal" value={draft.estimate ?? ""} disabled={!ed} onChange={(e) => setDraft({ ...draft, estimate: e.target.value })}
                 onBlur={() => String(draft.estimate ?? "") !== String(it.estimate ?? "") && save({ estimate: draft.estimate === "" ? null : draft.estimate })} /></label>
        <label className="field"><span>Parent</span>
          <select value={it.parent_id || ""} disabled={!ed} onChange={(e) => save({ parent_id: Number(e.target.value) || null })}>
            <option value="">None</option>
            {parents.map((c) => <option key={c.id} value={c.id}>{c.key} {c.title.slice(0, 50)}</option>)}
          </select></label>
      </div>
      {it.overdue && <p className="warn-text small">Overdue since {fmtDate(it.due_date)}.</p>}

      <div className="bd-tags-edit">
        {it.tags.map((t) => <button key={t} type="button" className="chip" disabled={!ed} title="Remove"
                                    onClick={() => save({ tags: it.tags.filter((x) => x !== t) })}>{t}{ed ? " ×" : ""}</button>)}
        {ed && <input value={tagIn} maxLength={30} placeholder="+ tag" aria-label="Add a tag" onChange={(e) => setTagIn(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && tagIn.trim()) { e.preventDefault(); save({ tags: [...it.tags, tagIn.trim()] }); setTagIn(""); } }} />}
      </div>

      <label className="field"><span>Description</span>
        <textarea rows={5} value={draft.description ?? ""} disabled={!ed} maxLength={10000} placeholder={ed ? "What exactly, why, what's done when…" : ""}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  onBlur={() => (draft.description ?? "") !== (it.description ?? "") && save({ description: draft.description })} />
      </label>

      {can(me, "crm.use") && (ed || it.company_id || it.deal_id) && (
        <div className="bd-links">
          <div className="field"><span>Client</span>
            {ed ? <Picker kind="company" value={it.company_id} label={it.company_name} onPick={(r) => save({ company_id: r ? r.id : null })} />
              : <b>{it.company_name}</b>}
            {it.company_id && <button type="button" className="linkish small" onClick={() => navigate("crm", { company: it.company_id })}>Open</button>}
          </div>
          <div className="field"><span>Deal</span>
            {ed ? <Picker kind="deal" value={it.deal_id} label={it.deal_title} onPick={(r) => save({ deal_id: r ? r.id : null })} />
              : <b>{it.deal_title}</b>}
            {it.deal_id && <button type="button" className="linkish small" onClick={() => navigate("crm", { deal: it.deal_id })}>Open</button>}
          </div>
        </div>
      )}

      <section className="bd-sec">
        <h4 className="sub">Sub-items {it.children > 0 && <span className="mail-count plain">{it.children_done}/{it.children}</span>}</h4>
        {it.children_items.map((c) => (
          <button key={c.id} type="button" className={`bd-child${c.done ? " done" : ""}`} onClick={() => onOpenItem(c.id)}>
            <span className="bd-type-dot" style={{ "--type": c.type_color }} /><span className="bd-key">{c.key}</span>
            <span className="bd-child-title">{c.title}</span><span className="meta">{c.state_name}</span>
            {c.assignee_name && <span className="bd-avatar sm" title={c.assignee_name}>{initials(c.assignee_name)}</span>}
          </button>
        ))}
        {ed && (
          <form className="bd-quick" onSubmit={async (e) => {
            e.preventDefault();
            if (child.trim() && await post(`/boards/${it.board_id}/items`, { fields: { title: child.trim(), parent_id: it.id, team_id: it.team_id } })) setChild("");
          }}>
            <input value={child} maxLength={200} placeholder="+ Sub-item, then Enter" onChange={(e) => setChild(e.target.value)} />
          </form>
        )}
        {it.parent && <p className="small">Part of <button type="button" className="linkish" onClick={() => onOpenItem(it.parent.id)}>{it.parent.key} {it.parent.title}</button></p>}
      </section>

      <section className="bd-sec">
        <h4 className="sub">Files {it.file_list.length > 0 && <span className="mail-count plain">{it.file_list.length}</span>}</h4>
        {it.file_list.map((f) => (
          <div key={f.id} className="bd-file">
            {f.can_open ? <button type="button" className="linkish" onClick={() => navigate("notes", { scope: f.scope, path: f.path })}>📄 {f.name}</button>
              : <span>📄 {f.name}</span>}
            <span className="meta" title={f.path}>{f.path.split("/").slice(0, -1).join("/")}</span>
            {ed && <button type="button" className="icon-x" aria-label={`Remove ${f.name}`} onClick={() => post(`/boards/files/${f.id}`, null, "DELETE")}>×</button>}
          </div>
        ))}
        {ed && can(me, "files.company") && <button type="button" className="btn ghost small" onClick={() => setPicker(true)}>+ Attach a company file</button>}
      </section>

      <section className="bd-sec">
        <h4 className="sub">Comments {it.comment_list.length > 0 && <span className="mail-count plain">{it.comment_list.length}</span>}</h4>
        {it.comment_list.map((c) => (
          <div key={c.id} className="bd-comment">
            <span className="bd-avatar sm">{initials(c.user_name)}</span>
            <div><b>{c.user_name || "Someone"}</b> <span className="meta" title={fmtTime(c.created_at)}>{fmtAgo(c.created_at)}</span>
              {c.mine && <button type="button" className="icon-x" aria-label="Delete comment" onClick={() => post(`/boards/comments/${c.id}`, null, "DELETE")}>×</button>}
              <p className="pre">{c.body}</p></div>
          </div>
        ))}
        {(it.level !== "view" || it.assignee_id === me?.id) && (
          <form className="bd-comment-form" onSubmit={async (e) => { e.preventDefault(); if (comment.trim() && await post(`/boards/items/${id}/comments`, { body: comment })) setComment(""); }}>
            <textarea rows={2} value={comment} maxLength={5000} placeholder="Comment… @Name lets them know" onChange={(e) => setComment(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) e.currentTarget.form.requestSubmit(); }} />
            <button className="btn small" disabled={busy || !comment.trim()}>Comment</button>
          </form>
        )}
      </section>

      <section className="bd-sec">
        <button type="button" className="btn ghost small" onClick={() => setHist(!hist)} aria-expanded={hist}>History {hist ? "▴" : "▾"}</button>
        {hist && (
          <ul className="crm-history-list">
            {it.history.map((h) => (
              <li key={h.id}><span>{h.by || "Someone"} {h.action === "create" ? "added it" : h.action === "archive" ? "removed it"
                : h.action === "restore" ? "restored it" : h.text}</span><span className="meta" title={fmtTime(h.ts)}>{fmtAgo(h.ts)}</span></li>
            ))}
          </ul>
        )}
        <p className="muted tiny">Added by {it.created_by_name || "someone"} {fmtAgo(it.created_at)}.</p>
      </section>
      {picker && <FilePicker onClose={() => setPicker(false)} onPick={async (path) => { setPicker(false); await post(`/boards/items/${id}/files`, { path }); }} />}
    </aside>
  );
}
