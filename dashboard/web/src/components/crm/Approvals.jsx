import { useState } from "react";
import { api } from "../../api.js";
import { fmtAgo, fmtTime } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { Empty, ErrorNote, Tag } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* Requests waiting for approval: deleting and merging records, emails, quotes, invoices and reminders to clients,
   employee changes and absences. Approvers also get them on Telegram with buttons. The requester may approve their
   own (decided 2026-10-12). `area` limits the list (the Team tab shows only employee requests). */

export const KIND_LABEL = { "crm:merge": "Merge", "crm:delete": "Delete", "crm:email": "Email to client",
                            "billing:send": "Send to client", "billing:remind": "Payment reminder",
                            "billing:cancel": "Cancel document", "employees:save": "Employee details",
                            "employees:absence": "Absence", "employees:absence_cancel": "Cancel absence" };
const TONE = { pending: "busy", approved: "ok", rejected: "bad", failed: "bad", cancelled: "" };

export default function Approvals({ onChanged, area = "", intro }) {
  const [tab, setTab] = useState("pending");
  const { data, error, reload } = usePoll(() => api(`/approvals?status=${tab}${area ? `&area=${area}` : ""}`), 30000, [tab, area]);
  const [notes, setNotes] = useState({});
  const [busy, setBusy] = useState(0);
  const [err, setErr] = useState("");
  const [done, setDone] = useState("");

  async function decide(a, approve) {
    setBusy(a.id); setErr(""); setDone("");
    try {
      const out = await api(`/approvals/${a.id}`, { method: "POST", body: { approve, note: notes[a.id] || "" } });
      setDone(out.status === "failed" ? `#${a.id} was approved but failed: ${out.result}` : `#${a.id} ${out.status}. ${out.result || ""}`);
      reload(); onChanged?.();
    } catch (e) { setErr(e.message); } finally { setBusy(0); }
  }
  async function withdraw(a) {
    setBusy(a.id); setErr("");
    try { await api(`/approvals/${a.id}`, { method: "DELETE" }); reload(); onChanged?.(); }
    catch (e) { setErr(e.message); } finally { setBusy(0); }
  }
  const items = data || [];
  return (
    <div className="crm-approvals">
      <div className="mail-toolbar">
        <b className="mail-title">Approvals</b>
        <div className="seg" role="tablist">
          {[["pending", "Waiting"], ["decided", "Decided"]].map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{label}</button>
          ))}
        </div>
      </div>
      <p className="muted small">{intro || "Deleting and merging records, emails, quotes, invoices and reminders going to clients, and "
        + "changes to employee data and absences wait here until someone with approval rights decides."} You can also decide with
        the buttons on Telegram, or ask Alfred.</p>
      {done && <div className="notice" role="status" onClick={() => setDone("")}>{done}</div>}
      <ErrorNote error={error || err} />
      {data && items.length === 0 && <Empty>{tab === "pending" ? "Nothing waiting for approval." : "No decided requests yet."}</Empty>}
      <div className="crm-approval-list">
        {items.map((a) => (
          <article key={a.id} className={`crm-approval s-${a.status}`}>
            <header>
              <span className="crm-approval-ico"><Icon name="approve" className="lab-ico" /></span>
              <b>#{a.id} · {KIND_LABEL[`${a.area}:${a.kind}`] || a.kind}</b>
              <Tag kind={TONE[a.status]}>{a.status}</Tag>
              <span className="meta" title={fmtTime(a.created_at)}>{a.requested_by_name || "Someone"} · {fmtAgo(a.created_at)}</span>
            </header>
            <p className="crm-approval-text">{a.summary}</p>
            {a.status !== "pending" && (
              <p className="meta">{a.status === "cancelled" ? "Withdrawn" : `${a.status[0].toUpperCase()}${a.status.slice(1)}`}
                {a.decided_by_name && a.status !== "cancelled" ? ` by ${a.decided_by_name}` : ""} {fmtAgo(a.decided_at)}
                {a.note ? ` · “${a.note}”` : ""}{a.result ? ` · ${a.result}` : ""}</p>
            )}
            {(a.can_decide || a.can_cancel) && (
              <div className="crm-approval-actions">
                {a.can_decide && (
                  <>
                    <input value={notes[a.id] || ""} maxLength={300} placeholder="Note (optional)" aria-label="Note"
                           onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} />
                    <button className="btn primary small" disabled={busy === a.id} onClick={() => decide(a, true)}>✓ Approve</button>
                    <button className="btn danger small" disabled={busy === a.id} onClick={() => decide(a, false)}>✕ Reject</button>
                  </>
                )}
                {a.can_cancel && <button className="btn ghost small" disabled={busy === a.id} onClick={() => withdraw(a)}>Withdraw</button>}
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
