import { useEffect, useMemo, useRef, useState } from "react";
import { api, apiUpload } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "./common.jsx";

const COLOR_LABELS = {
  page: "Page", card: "Paper", ink: "Text", muted: "Quiet text", accent: "Accent line & links",
  foot_bg: "Banner & footer", foot_ink: "Footer heading", foot_muted: "Footer text",
};
const FIELD_RE = /\{([a-z][a-z0-9_]{0,30})\}/g;
const STANDARD = ["name", "first_name", "company", "date", "sender", "assistant", "website"];

function useDebounced(value, ms) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

function blank(kind, layouts) {
  return kind === "layout"
    ? { kind, name: "", description: "", active: false, company: false, from_name: "", style: { ...(layouts[0]?.style || {}) },
        signature_md: "Kind regards,\n**{assistant}**", footer_md: "{company}\n{website}" }
    : { kind, name: "", description: "", active: true, subject: "", body_md: "Dear {name},\n\n",
        layout_id: layouts.find((l) => l.active)?.id || null };
}

/* One template: the form on the left, the live preview (exactly what the recipient sees) on the right. */
function Editor({ initial, layouts, onClose, onSaved }) {
  const [t, setT] = useState(initial);
  const [sample, setSample] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [pv, setPv] = useState(null);
  const fileRef = useRef(null);
  const isNew = !t.id;
  const set = (k, v) => setT((x) => ({ ...x, [k]: v }));
  const fields = useMemo(() => {
    const found = new Set();
    for (const m of `${t.subject || ""} ${t.body_md || ""}`.matchAll(FIELD_RE)) found.add(m[1]);
    return [...found];
  }, [t.subject, t.body_md]);
  const editable = (({ kind, name, description, company, style, from_name, signature_md, footer_md, subject, body_md, layout_id }) =>
    (t.kind === "layout" ? { kind, name, description, company, style, from_name, signature_md, footer_md }
                         : { kind, name, description, subject, body_md, layout_id }))(t);
  const preview = useDebounced(JSON.stringify({ ...editable, sample }), 500);

  useEffect(() => {
    let live = true;
    const body = JSON.parse(preview);
    const { sample: s, ...rest } = body;
    api("/mail/templates/preview", { method: "POST", body: { ...rest, template_id: t.id || null, sample: s } })
      .then((r) => live && setPv({ ...r, url: `${r.url}?v=${Date.now()}` }))
      .catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [preview, t.id, t.custom_banner]);

  async function save() {
    setBusy(true); setErr(""); setNote("");
    try {
      const body = { ...editable, active: t.active };
      const saved = isNew ? await api("/mail/templates", { method: "POST", body })
                          : await api(`/mail/templates/${Number(t.id)}`, { method: "PATCH", body });
      setT(saved); setNote("Saved."); onSaved();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  async function test() {
    setBusy(true); setErr(""); setNote("");
    try {
      const r = await api(`/mail/templates/${Number(t.id)}/test`, { method: "POST", body: { sample } });
      setNote(`Test sent to ${r.to}.`); onSaved();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  async function banner(file) {
    if (!file) return;
    setBusy(true); setErr("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      setT(await apiUpload(`/mail/templates/${Number(t.id)}/banner`, fd)); onSaved();
    } catch (e) { setErr(e.message); } finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  async function removeBanner() {
    setBusy(true); setErr("");
    try { setT(await api(`/mail/templates/${Number(t.id)}/banner`, { method: "DELETE" })); onSaved(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="tm-editor">
      <div className="tm-editor-head">
        <button className="btn small ghost" onClick={onClose}>← All templates</button>
        <b>{isNew ? `New ${t.kind}` : t.name}</b>
        <Tag>{t.kind}</Tag>
        {t.builtin && <Tag>built-in</Tag>}
        <span className="tm-editor-actions">
          {!isNew && <button className="btn small ghost" onClick={test} disabled={busy}>Send a test to me</button>}
          <button className="btn small primary" onClick={save} disabled={busy}>{isNew ? "Create" : "Save"}</button>
        </span>
      </div>
      <ErrorNote error={err} />
      {note && <div className="ok-note small">{note}</div>}
      <div className="tm-editor-grid">
        <form className="tm-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <label className="field"><span>Name</span>
            <input value={t.name} onChange={(e) => set("name", e.target.value)} maxLength={60} required /></label>
          <label className="field"><span>What it's for</span>
            <input value={t.description || ""} onChange={(e) => set("description", e.target.value)} maxLength={200} /></label>
          <label className="tm-check"><input type="checkbox" checked={!!t.active} onChange={(e) => set("active", e.target.checked)} />
            Active (offered to Alfred and in the pickers){!isNew && " · saved with the other changes"}</label>
          {t.kind === "layout" ? (
            <>
              <label className="tm-check"><input type="checkbox" checked={!!t.company} onChange={(e) => set("company", e.target.checked)} />
                In the company's name (formal; admins and cofounders; counts as company mail)</label>
              <label className="field"><span>Sender name (empty = Alfred)</span>
                <input value={t.from_name || ""} onChange={(e) => set("from_name", e.target.value)} maxLength={80} /></label>
              <div className="field"><span>Banner (PNG or JPEG, up to 1 MB; 1200 × 300 px works well)</span>
                <div className="tm-banner">
                  {!isNew && t.has_banner && <img src={`/api/mail/templates/${Number(t.id)}/banner?v=${encodeURIComponent(t.updated_at || "")}`} alt="Banner" />}
                  {isNew ? <span className="muted tiny">Create the layout first, then add a banner.</span> : (
                    <span className="tm-banner-actions">
                      <input ref={fileRef} type="file" accept="image/png,image/jpeg" onChange={(e) => banner(e.target.files[0])}
                             aria-label="Upload banner" />
                      {t.custom_banner && <button type="button" className="btn tiny-btn ghost" onClick={removeBanner}>
                        {t.builtin ? "Back to the built-in banner" : "Remove banner"}</button>}
                    </span>
                  )}
                </div>
              </div>
              <div className="field"><span>Colours</span>
                <div className="tm-colors">
                  {Object.entries(COLOR_LABELS).map(([k, label]) => (
                    <label key={k} className="tm-color">
                      <input type="color" value={t.style?.[k] || "#000000"} onChange={(e) => set("style", { ...t.style, [k]: e.target.value })} />
                      <span>{label}</span>
                    </label>
                  ))}
                </div>
              </div>
              <label className="field"><span>Font</span>
                <select value={t.style?.font || "serif"} onChange={(e) => set("style", { ...t.style, font: e.target.value })}>
                  <option value="serif">Serif (classic)</option><option value="sans">Sans-serif (modern)</option>
                </select></label>
              <label className="field"><span>Signature (Markdown; {"{assistant}"}, {"{company}"}; “on behalf of …” is added when Alfred writes for someone)</span>
                <textarea rows={4} value={t.signature_md || ""} onChange={(e) => set("signature_md", e.target.value)} maxLength={1000} /></label>
              <label className="field"><span>Footer, one line each (first line is the heading; {"{company}"}, {"{website}"}, {"{tagline}"}, {"{assistant}"})</span>
                <textarea rows={4} value={t.footer_md || ""} onChange={(e) => set("footer_md", e.target.value)} maxLength={1500} /></label>
            </>
          ) : (
            <>
              <label className="field"><span>Layout</span>
                <select value={t.layout_id || ""} onChange={(e) => set("layout_id", e.target.value ? Number(e.target.value) : null)}>
                  {layouts.map((l) => <option key={l.id} value={l.id}>{l.name}{l.active ? "" : " (inactive)"}</option>)}
                </select></label>
              <label className="field"><span>Subject</span>
                <input value={t.subject || ""} onChange={(e) => set("subject", e.target.value)} maxLength={200} /></label>
              <label className="field"><span>Text (Markdown; start with the greeting, no signature)</span>
                <textarea rows={12} value={t.body_md || ""} onChange={(e) => set("body_md", e.target.value)} maxLength={20000} /></label>
              <div className="field"><span>Fields: write {"{name}"}, {"{first_name}"}, {"{company}"}, {"{date}"}, {"{sender}"} or your own like {"{offer_date}"}; Alfred asks for any it doesn't know</span>
                {fields.length === 0 ? <span className="muted tiny">No fields in this text.</span> : (
                  <div className="tm-fields">
                    {fields.map((f) => (
                      <label key={f} className="tm-field">
                        <code>{`{${f}}`}</code>
                        {STANDARD.includes(f) && f !== "name" && f !== "first_name"
                          ? <span className="muted tiny">filled in automatically</span>
                          : <input value={sample[f] || ""} onChange={(e) => setSample({ ...sample, [f]: e.target.value })}
                                   placeholder="sample for the preview" maxLength={200} />}
                      </label>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </form>
        <div className="tm-preview">
          <div className="muted tiny">Preview{pv?.subject ? ` · Subject: ${pv.subject}` : ""}</div>
          {/* The preview page carries its own CSP (no scripts at all, only inline styles and the data: banner). */}
          {pv ? <iframe className="tm-frame" title="Email preview" src={pv.url} /> : <p className="muted small">Rendering…</p>}
          {pv?.text && <details className="tm-plain"><summary>Plain-text version</summary><pre>{pv.text}</pre></details>}
        </div>
      </div>
    </div>
  );
}

/* Mail tab → Template manager: a quick CRM of layouts and reusable texts. */
export default function TemplateManager({ onBack }) {
  const [kind, setKind] = useState("");
  const [activeOnly, setActiveOnly] = useState(false);
  const [archived, setArchived] = useState(false);
  const [q, setQ] = useState("");
  const query = useDebounced(q, 300);
  const [editing, setEditing] = useState(null);
  const [err, setErr] = useState("");
  const url = `/mail/templates?${new URLSearchParams({ kind, active: String(activeOnly), archived: String(archived), q: query })}`;
  const { data, error, reload } = usePoll(() => api(url), 0, [url]);
  const { data: allLayouts, reload: reloadLayouts } = usePoll(() => api("/mail/templates?kind=layout"), 0);
  const layouts = allLayouts || [];
  const layoutName = (id) => layouts.find((l) => l.id === id)?.name;
  const refresh = () => { reload(); reloadLayouts(); };

  async function act(path, body) {
    setErr("");
    try { await api(path, { method: "POST", body }); refresh(); } catch (e) { setErr(e.message); }
  }
  async function open(id) {
    setErr("");
    try { setEditing(await api(`/mail/templates/${Number(id)}`)); } catch (e) { setErr(e.message); }
  }

  if (editing) {
    return <Editor key={editing.id || `new-${editing.kind}`} initial={editing} layouts={layouts}
                   onClose={() => { setEditing(null); refresh(); }} onSaved={refresh} />;
  }
  return (
    <div className="tm">
      <div className="tm-bar">
        <div className="seg" role="group" aria-label="Kind">
          {[["", "All"], ["layout", "Layouts"], ["text", "Texts"]].map(([k, l]) => (
            <button key={k || "all"} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>{l}</button>
          ))}
        </div>
        <label className="mail-unread-toggle"><input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} /> Active only</label>
        <label className="mail-unread-toggle"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Archived</label>
        <input className="tm-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search templates" maxLength={80} aria-label="Search templates" />
        <span className="tm-new">
          <button className="btn small" onClick={() => setEditing(blank("text", layouts))}>+ New text</button>
          <button className="btn small" onClick={() => setEditing(blank("layout", layouts))}>+ New layout</button>
        </span>
      </div>
      <p className="muted small">Layouts decide how an email looks; texts are reusable emails with fields. Only active ones are
        offered to Alfred (“send Max the offer follow-up”) and in the pickers.</p>
      <ErrorNote error={err || error} />
      {data && data.length === 0 && <Empty>No templates here.</Empty>}
      <div className="tm-grid">
        {(data || []).map((t) => (
          <article key={t.id} className={`tm-card${t.active ? "" : " off"}`}>
            <div className="tm-card-top">
              <Tag kind={t.kind === "layout" ? "ok" : ""}>{t.kind}</Tag>
              {t.company && <Tag>company</Tag>}
              {t.builtin && <Tag>built-in</Tag>}
              {!archived && (
                <label className="tm-switch" title={t.active ? "Active" : "Inactive"}>
                  <input type="checkbox" checked={t.active} onChange={(e) => act(`/mail/templates/${Number(t.id)}/active`, { value: e.target.checked })}
                         aria-label={`${t.name} active`} />
                  <span>{t.active ? "Active" : "Off"}</span>
                </label>
              )}
            </div>
            <button type="button" className="tm-card-body" onClick={() => open(t.id)}>
              <b className="tm-name">{t.name}</b>
              {t.description && <span className="muted small">{t.description}</span>}
              {t.kind === "text" && t.subject && <span className="small">Subject: {t.subject}</span>}
              {t.kind === "text" && <span className="muted tiny">Layout: {layoutName(t.layout_id) || "Alfred"}{t.fields.length ? ` · fields: ${t.fields.join(", ")}` : ""}</span>}
              {t.kind === "layout" && (
                <span className="tm-swatches">{["foot_bg", "accent", "card", "ink"].map((k) => <span key={k} style={{ background: t.style[k] }} />)}</span>
              )}
            </button>
            <div className="tm-card-foot">
              <span className="muted tiny">{t.used ? `Used ${t.used}× · last ${fmtTime(t.last_used)}` : "Not used yet"}</span>
              <span className="tm-card-actions">
                <button className="btn tiny-btn ghost" onClick={() => open(t.id)}>Edit</button>
                <button className="btn tiny-btn ghost" onClick={() => act(`/mail/templates/${Number(t.id)}/duplicate`)}>Duplicate</button>
                <button className="btn tiny-btn ghost" onClick={() => act(`/mail/templates/${Number(t.id)}/archive`, { value: !archived })}>
                  {archived ? "Restore" : "Archive"}</button>
              </span>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
