import { useEffect, useRef, useState } from "react";
import { api, apiUpload } from "../api.js";
import { ErrorNote, Tag } from "./common.jsx";
import Markdown from "./Markdown.jsx";

const dl = (scope, path) => `/api/nas/download?${new URLSearchParams({ scope, path })}`;
const isMd = (p) => /\.md$/i.test(p);
const CHECK = /^(\s*[-*]\s+\[)( |x|X)(\]\s+)(.*)$/;

/* Insert Markdown around the selection in a textarea. */
function wrap(el, before, after = "", linePrefix = false) {
  const { selectionStart: a, selectionEnd: b, value } = el;
  if (linePrefix) {
    const lineStart = value.lastIndexOf("\n", a - 1) + 1;
    return { text: value.slice(0, lineStart) + before + value.slice(lineStart), cursor: b + before.length };
  }
  const sel = value.slice(a, b) || "text";
  return { text: value.slice(0, a) + before + sel + after + value.slice(b), cursor: a + before.length + sel.length + after.length };
}

/* One open file: Markdown edit / preview / checklist, or download + replace for other types. */
export default function FileEditor({ file, onSaved }) {
  const { scope, path } = file;
  const [doc, setDoc] = useState(null);
  const [text, setText] = useState("");
  const [mode, setMode] = useState("preview");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [newItem, setNewItem] = useState("");
  const ta = useRef(null);
  const editable = /\.(md|txt|csv|json|log|ya?ml)$/i.test(path);
  const dirty = doc && text !== doc.text;
  const hasChecks = text.split("\n").some((l) => CHECK.test(l));
  const isList = /(^|\/)Lists\//i.test(path);

  async function load() {
    setErr("");
    try {
      const d = await api(`/nas/file?${new URLSearchParams({ scope, path })}`);
      setDoc(d); setText(d.text);
      setMode(/(^|\/)Lists\//i.test(path) ? "checklist" : isMd(path) ? "preview" : "edit");
    } catch (e) { setErr(e.message); }
  }
  useEffect(() => { if (editable) load(); }, [scope, path]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save(next = text) {
    setBusy(true); setErr("");
    try {
      const r = await api("/nas/file", { method: "PUT", body: { scope, path, text: next, mtime: doc?.mtime } });
      setDoc({ ...doc, text: next, mtime: r.mtime }); setText(next); onSaved?.();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  function fmt(before, after, linePrefix) {
    if (!ta.current) return;
    const r = wrap(ta.current, before, after, linePrefix);
    setText(r.text);
    requestAnimationFrame(() => { ta.current.focus(); ta.current.setSelectionRange(r.cursor, r.cursor); });
  }
  function toggleLine(i) {
    const lines = text.split("\n");
    lines[i] = lines[i].replace(CHECK, (_, a, x, b, rest) => `${a}${x.trim() ? " " : "x"}${b}${rest}`);
    save(lines.join("\n"));
  }
  function addItem(e) {
    e.preventDefault();
    if (!newItem.trim()) return;
    const lines = text.replace(/\s+$/, "").split("\n");
    let at = lines.length;
    for (let i = lines.length - 1; i >= 0; i -= 1) if (CHECK.test(lines[i])) { at = i + 1; break; }
    lines.splice(at, 0, `- [ ] ${newItem.trim()}`);
    setNewItem("");
    save(lines.join("\n") + "\n");
  }
  async function replace(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    const fd = new FormData();
    const folder = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    fd.append("scope", scope); fd.append("folder", folder); fd.append("replace", "true");
    fd.append("file", new File([f], path.slice(path.lastIndexOf("/") + 1)));
    setBusy(true); setErr("");
    try { await apiUpload("/nas/upload", fd); onSaved?.(); } catch (x) { setErr(x.message); } finally { setBusy(false); e.target.value = ""; }
  }

  if (!editable) {
    return (
      <div className="file-editor">
        <p className="small">This file type isn't edited in the browser. Download it, edit it, then upload it again to replace it.</p>
        <ErrorNote error={err} />
        <div className="form-actions">
          <a className="btn primary small" href={dl(scope, path)} download>Download</a>
          <label className="btn small">Replace with… <input type="file" hidden onChange={replace} /></label>
        </div>
      </div>
    );
  }
  return (
    <div className="file-editor"
         onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); if (dirty) save(); } }}>
      <div className="editor-bar">
        <div className="seg" role="tablist" aria-label="Mode">
          {isMd(path) && <button className={mode === "preview" ? "active" : ""} onClick={() => setMode("preview")}>Preview</button>}
          <button className={mode === "edit" ? "active" : ""} onClick={() => setMode("edit")}>Edit</button>
          {(hasChecks || isList) && <button className={mode === "checklist" ? "active" : ""} onClick={() => setMode("checklist")}>Checklist</button>}
        </div>
        {mode === "edit" && isMd(path) && (
          <div className="md-tools" role="toolbar" aria-label="Formatting">
            <button title="Bold" onClick={() => fmt("**", "**")}><b>B</b></button>
            <button title="Italic" onClick={() => fmt("*", "*")}><i>I</i></button>
            <button title="Heading" onClick={() => fmt("## ", "", true)}>H</button>
            <button title="Bullet list" onClick={() => fmt("- ", "", true)}>•</button>
            <button title="Checkbox" onClick={() => fmt("- [ ] ", "", true)}>☐</button>
            <button title="Link" onClick={() => fmt("[", "](https://)")}>🔗</button>
            <button title="Code" onClick={() => fmt("`", "`")}>{"</>"}</button>
          </div>
        )}
        <div className="row-actions">
          {dirty && <Tag kind="busy">unsaved</Tag>}
          <button className="btn primary small" disabled={!dirty || busy} onClick={() => save()}>Save</button>
          <a className="btn small" href={dl(scope, path)} download>Download</a>
        </div>
      </div>
      <ErrorNote error={err} />
      {!doc && !err && <p className="muted">Loading…</p>}
      {doc && mode === "edit" && (
        <textarea ref={ta} className="note-editor" value={text} spellCheck onChange={(e) => setText(e.target.value)} />
      )}
      {doc && mode === "preview" && <div className="note-body"><Markdown text={text || "*(empty)*"} /></div>}
      {doc && mode === "checklist" && (
        <div className="list-card">
          {text.split("\n").map((l, i) => {
            const m = l.match(CHECK);
            if (!m) return null;
            const done = m[2].trim() !== "";
            return (
              <label key={i} className={`toggle${done ? " done" : ""}`}>
                <input type="checkbox" checked={done} disabled={busy} onChange={() => toggleLine(i)} /><span>{m[4]}</span>
              </label>
            );
          })}
          {!hasChecks && <p className="muted small">No items yet.</p>}
          <form className="field-row" onSubmit={addItem}>
            <input value={newItem} maxLength={200} placeholder="Add an item" aria-label="Add an item" onChange={(e) => setNewItem(e.target.value)} />
            <button className="btn small" type="submit" disabled={busy}>Add</button>
          </form>
        </div>
      )}
    </div>
  );
}
