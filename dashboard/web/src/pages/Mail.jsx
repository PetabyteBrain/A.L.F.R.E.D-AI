import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";

const KIND_LABEL = {
  request: "to Alfred", reply: "reply", inquiry: "inquiry", unverified: "sender not verified", automated: "automated",
  spam: "spam", sent: "sent", auto_reply: "Alfred's answer", notice: "notice", unsorted: "new",
};
const KIND_TONE = { inquiry: "busy", unverified: "bad", spam: "", automated: "", reply: "ok" };

function who(m) {
  if (m.direction === "in") return m.from_name ? `${m.from_name} <${m.from_addr}>` : m.from_addr;
  return `to ${(m.to || []).join(", ")}`;
}

/* One email: text only (never the HTML), auth checks, thread, escalation. */
function MailView({ id, team, onBack, onChanged }) {
  const { data: m, error } = usePoll(() => api(`/mail/${Number(id)}`), 0, [id]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [full, setFull] = useState(false);
  useEffect(() => { setFull(false); setNote(""); setErr(""); }, [id]);
  if (!m) return <ErrorNote error={error} />;

  async function close() {
    setBusy(true);
    setErr("");
    try { await api(`/mail/escalations/${Number(m.escalation.id)}/close`, { method: "POST", body: { note } }); onChanged(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const text = full || !m.own_text ? m.text : m.own_text;
  return (
    <div className="mail-view">
      <div className="mail-view-head">
        <button className="btn small ghost" onClick={onBack}>← Back</button>
        <a className="btn small" href={`/api/mail/${Number(m.id)}/raw`} download>Download .eml</a>
      </div>
      <h3 className="mail-subject">{m.subject || "(no subject)"}</h3>
      <div className="meta">
        {who(m)} · {fmtTime(m.created_at)} · <Tag kind={KIND_TONE[m.kind] || ""}>{KIND_LABEL[m.kind] || m.kind}</Tag>
        {m.scope === "company" && <> <Tag>company</Tag></>}
        {m.archived ? <span className="muted tiny"> · archived on the NAS</span> : <span className="muted tiny"> · not archived yet</span>}
      </div>
      {m.cc?.length > 0 && <div className="meta">Cc {m.cc.join(", ")}</div>}
      {m.auth && (
        <div className="meta tiny">
          Sender check: {m.auth.dmarc ? <Tag kind="ok">DMARC aligned</Tag> : <Tag kind="bad">not aligned</Tag>}{" "}
          SPF {m.auth.spf || "none"} · DKIM {m.auth.dkim ? "pass" : "none"}{m.auth.spam && <> · <Tag kind="bad">spam</Tag></>}
        </div>
      )}
      {m.summary && <p className="mail-summary small"><b>{"Alfred: "}</b>{m.summary}</p>}
      {m.attachments?.length > 0 && <div className="meta">Attachments: {m.attachments.join(", ")}</div>}
      <pre className="mail-text">{text || "(no text)"}</pre>
      {m.own_text && m.text && m.text.length > m.own_text.length && (
        <button className="btn ghost small" onClick={() => setFull(!full)}>{full ? "Hide quoted text" : "Show quoted text"}</button>
      )}
      {m.error && <div className="fail-note">{m.error}</div>}
      {m.escalation && (
        <div className="mail-escalation">
          <span>Escalation #{m.escalation.id}: {m.escalation.topic} · <Tag kind={m.escalation.status === "open" ? "busy" : "ok"}>{m.escalation.status}</Tag></span>
          {team && m.escalation.status === "open" && (
            <form className="field-row" onSubmit={(e) => { e.preventDefault(); close(); }}>
              <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Note (optional)" aria-label="Note" />
              <button className="btn small" type="submit" disabled={busy}>Mark handled</button>
            </form>
          )}
          <span className="muted tiny">To answer, ask Alfred in the chat or on Telegram (“reply to email #{m.id}: …”).</span>
        </div>
      )}
      <ErrorNote error={err} />
      {m.thread?.length > 0 && (
        <>
          <h4 className="sub">Same conversation</h4>
          <div className="list">
            {m.thread.map((t) => (
              <div className="row" key={t.id}>
                <div><div>{t.subject || "(no subject)"}</div><div className="meta">{who(t)} · {fmtTime(t.created_at)}</div></div>
              </div>
            ))}
          </div>
        </>
      )}
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
  const { data: st } = usePoll(() => api("/mail/status"), 60000);
  const [box, setBox] = useState("inbox");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(null);
  const listing = box !== "escalations";
  const { data, error, reload } = usePoll(
    () => (listing ? api(`/mail?box=${box}&q=${encodeURIComponent(query)}`) : Promise.resolve(null)), 30000, [box, query]);
  const team = !!st?.team;
  const boxes = [["inbox", "Inbox"], ...(team ? [["inquiries", "Inquiries"]] : []), ["sent", "Sent"], ["all", "All"],
    ...(team ? [["escalations", `For the team${st?.open_escalations ? ` (${st.open_escalations})` : ""}`]] : [])];

  return (
    <Panel code="12" title="Mail">
      {st && !st.enabled && <Empty>{assistant}'s email isn't set up yet (MAIL_ADDRESS and SMTP_* in .env).</Empty>}
      {st?.enabled && (
        <p className="muted small">
          {assistant} writes from <b>{st.address}</b>{st.receiving ? " and reads what arrives there" : " (receiving is off)"}.
          Mail from registered people (from an address on their profile) is answered with what their role allows; mail
          from anyone else gets public information only, and whatever needs a person goes to the team. Everything is
          filed on the NAS ({team ? "company mail under the company share's Mail folder, " : ""}your own mail in your folder's Mail folder).
        </p>
      )}
      <div className="mail-bar">
        <div className="seg">
          {boxes.map(([id, label]) => (
            <button key={id} className={id === box ? "active" : ""} onClick={() => { setBox(id); setOpen(null); }}>{label}</button>
          ))}
        </div>
        {listing && !open && (
          <form className="mail-search" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" maxLength={80} aria-label="Search mail" />
          </form>
        )}
      </div>
      {open ? (
        <MailView id={open} team={team} onBack={() => setOpen(null)} onChanged={() => reload()} />
      ) : listing ? (
        <>
          <ErrorNote error={error} />
          {data && data.emails.length === 0 && <Empty>No mail here{query ? " for that search" : ""}.</Empty>}
          <div className="list mail-list">
            {(data?.emails || []).map((m) => (
              <button className="row mail-row" key={m.id} onClick={() => setOpen(m.id)}>
                <div>
                  <div className="mail-row-top">
                    <span className="mail-who" title={who(m)}>{who(m)}</span>
                    <span className="meta">{fmtTime(m.created_at)}</span>
                  </div>
                  <div className="mail-row-subject" title={m.subject}>
                    {m.subject || "(no subject)"} <Tag kind={KIND_TONE[m.kind] || ""}>{KIND_LABEL[m.kind] || m.kind}</Tag>
                    {m.attachments.length > 0 && <span className="muted tiny"> · {m.attachments.length} attachment{m.attachments.length > 1 ? "s" : ""}</span>}
                  </div>
                  {m.summary && <div className="meta mail-row-summary">{m.summary}</div>}
                </div>
              </button>
            ))}
          </div>
        </>
      ) : (
        <Escalations onOpen={(id) => setOpen(id)} />
      )}
    </Panel>
  );
}
