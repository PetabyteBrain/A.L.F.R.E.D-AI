import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { fmtAgo, fmtDate } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { Empty, ErrorNote, Tag } from "../common.jsx";

/* Task requests: from people who may only ask on a board. The team's manager (or the board's managers) accepts,
   optionally assigning someone right away, or declines with a note. Also on Telegram with buttons. */

const TONE = { pending: "busy", accepted: "ok", declined: "bad", withdrawn: "" };

export function RequestDialog({ board, onDone, onClose }) {
  const [f, setF] = useState({ title: "", description: "", team_id: "", due_date: "", priority: 2 });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  async function send(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      onDone(await api(`/boards/${board.id}/requests`, { method: "POST", body: {
        ...f, team_id: Number(f.team_id) || null, due_date: f.due_date || null, priority: Number(f.priority) } }));
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <form className="hud modal editor" role="dialog" aria-modal="true" aria-label="Ask the team" onClick={(e) => e.stopPropagation()} onSubmit={send}>
        <header className="hud-head"><h2>Ask for something on {board.name}</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button></header>
        <p className="muted small">It goes to the team's manager (or the board's managers). Once accepted it becomes an item on the board and you're told.</p>
        <ErrorNote error={err} />
        <div className="form-grid">
          <label className="field wide"><span>What do you need?</span><input autoFocus required value={f.title} maxLength={200} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
          <label className="field wide"><span>Details (optional)</span><textarea rows={3} value={f.description} maxLength={5000} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
          <label className="field"><span>For the team</span>
            <select value={f.team_id} onChange={(e) => setF({ ...f, team_id: e.target.value })}>
              <option value="">Whoever runs the board</option>
              {(board.teams || []).map((t) => <option key={t.id} value={t.id}>{t.name}{t.manager_name ? ` (${t.manager_name})` : ""}</option>)}
            </select></label>
          <label className="field"><span>Needed by (optional)</span><input type="date" value={f.due_date} onChange={(e) => setF({ ...f, due_date: e.target.value })} /></label>
          <label className="field"><span>Priority</span>
            <select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}><option value={1}>Urgent</option><option value={2}>Normal</option><option value={3}>Low</option></select></label>
        </div>
        <div className="modal-actions">
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" disabled={busy || !f.title.trim()}>Send request</button>
        </div>
      </form>
    </div>
  );
}

export default function Requests({ onOpenItem, onChanged }) {
  const [tab, setTab] = useState("pending");
  const { data, error, reload } = usePoll(() => api(`/boards/requests?status=${tab === "pending" ? "pending" : "all"}`), 30000, [tab]);
  const [notes, setNotes] = useState({});
  const [assign, setAssign] = useState({});
  const [boardsData, setBoardsData] = useState({});
  const [busy, setBusy] = useState(0);
  const [err, setErr] = useState("");
  const rows = (data || []).filter((r) => (tab === "pending" ? r.status === "pending" : r.status !== "pending"));
  useEffect(() => {                                 // who can be assigned on each board with a request to decide
    for (const r of rows) if (r.can_decide && !boardsData[r.board_id]) {
      api(`/boards/${r.board_id}`).then((b) => setBoardsData((x) => ({ ...x, [r.board_id]: b }))).catch(() => {});
    }
  }, [rows.map((r) => r.id).join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  async function decide(r, accept) {
    setBusy(r.id); setErr("");
    try {
      await api(`/boards/requests/${r.id}`, { method: "POST", body: { accept, note: notes[r.id] || "", assignee_id: Number(assign[r.id]) || null } });
      reload(); onChanged?.();
    } catch (e) { setErr(e.message); } finally { setBusy(0); }
  }
  async function withdraw(r) {
    setBusy(r.id); setErr("");
    try { await api(`/boards/requests/${r.id}`, { method: "DELETE" }); reload(); onChanged?.(); }
    catch (e) { setErr(e.message); } finally { setBusy(0); }
  }
  return (
    <div className="crm-approvals">
      <div className="mail-toolbar">
        <b className="mail-title">Requests</b>
        <div className="seg" role="tablist">
          {[["pending", "Open"], ["decided", "Decided"]].map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}
        </div>
      </div>
      <p className="muted small">Work asked for by people who can't add items to a board themselves. The team's manager decides, here or with
        the buttons on Telegram.</p>
      <ErrorNote error={error || err} />
      {data && rows.length === 0 && <Empty>{tab === "pending" ? "No open requests." : "Nothing decided yet."}</Empty>}
      <div className="crm-approval-list">
        {rows.map((r) => (
          <article key={r.id} className={`crm-approval s-${r.status}`}>
            <header>
              <b>#{r.id} · {r.board_name}{r.team_name ? ` · ${r.team_name}` : ""}</b>
              <Tag kind={TONE[r.status]}>{r.status}</Tag>
              <span className="meta">{r.requested_by_name || "Someone"} · {fmtAgo(r.created_at)}</span>
            </header>
            <p className="crm-approval-text"><b>{r.title}</b>{r.due_date ? ` · needed by ${fmtDate(r.due_date)}` : ""}{r.priority === 1 ? " · urgent" : ""}</p>
            {r.description && <p className="pre small">{r.description}</p>}
            {r.status !== "pending" && (
              <p className="meta">{r.status === "accepted" ? "Accepted" : r.status === "declined" ? "Declined" : "Withdrawn"}
                {r.decided_by_name ? ` by ${r.decided_by_name}` : ""}{r.note ? ` · “${r.note}”` : ""}
                {r.item_key && <> · <button type="button" className="linkish" onClick={() => onOpenItem(r.item_id, r.board_id)}>{r.item_key}</button></>}</p>
            )}
            {(r.can_decide || r.can_withdraw) && (
              <div className="crm-approval-actions">
                {r.can_decide && (
                  <>
                    <select className="crm-sort" value={assign[r.id] || ""} aria-label="Assign to" onChange={(e) => setAssign({ ...assign, [r.id]: e.target.value })}>
                      <option value="">Assign later</option>
                      {(boardsData[r.board_id]?.people || []).map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
                    </select>
                    <input value={notes[r.id] || ""} maxLength={300} placeholder="Note (optional)" aria-label="Note" onChange={(e) => setNotes({ ...notes, [r.id]: e.target.value })} />
                    <button className="btn primary small" disabled={busy === r.id} onClick={() => decide(r, true)}>✓ Accept</button>
                    <button className="btn danger small" disabled={busy === r.id} onClick={() => decide(r, false)}>✕ Decline</button>
                  </>
                )}
                {r.can_withdraw && <button className="btn ghost small" disabled={busy === r.id} onClick={() => withdraw(r)}>Withdraw</button>}
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
