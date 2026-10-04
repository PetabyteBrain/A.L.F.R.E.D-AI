import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";
import Markdown from "../components/Markdown.jsx";

const q = (o) => new URLSearchParams(o).toString();

/* ---------------------------------------------------------------- one note */
function NoteView({ scope, path, onChanged, onClose }) {
  const [note, setNote] = useState(null);
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setNote(null); setEdit(null); setErr("");
    api(`/nas/note?${q({ scope, path })}`).then(setNote).catch((e) => setErr(e.message));
  }, [scope, path]);

  async function run(fn) {
    setBusy(true); setErr("");
    try { await fn(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const save = () => run(async () => {
    await api("/nas/note", { method: "POST", body: { scope, title: path, content: edit, mode: "replace" } });
    setNote(await api(`/nas/note?${q({ scope, path })}`)); setEdit(null); onChanged();
  });
  const trash = () => run(async () => {
    await api(`/nas/note?${q({ scope, path })}`, { method: "DELETE" }); onChanged(); onClose();
  });

  return (
    <div className="hud note-view">
      <div className="note-head">
        <h3 className="sub flush">{path.replace(/\.md$/, "")}</h3>
        <div className="row-actions">
          {note && edit === null && <button className="btn small" onClick={() => setEdit(note.text)}>Edit</button>}
          {edit !== null && <button className="btn primary small" disabled={busy} onClick={save}>Save</button>}
          {edit !== null && <button className="btn small" disabled={busy} onClick={() => setEdit(null)}>Cancel</button>}
          {edit === null && <button className="btn danger small" disabled={busy} onClick={trash} title="Moves it to the trash folder on the NAS">Trash</button>}
          <button className="btn small" onClick={onClose}>Close</button>
        </div>
      </div>
      <ErrorNote error={err} />
      {!note && !err && <p className="muted">Loading…</p>}
      {note && edit === null && <div className="note-body"><Markdown text={note.text} /></div>}
      {edit !== null && (
        <textarea className="note-editor" value={edit} onChange={(e) => setEdit(e.target.value)} spellCheck />
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- notes column */
function NotesColumn({ scope, open, setOpen }) {
  const { data, error, reload } = usePoll(() => api(`/nas/notes?${q({ scope })}`), 0, [scope]);
  const [form, setForm] = useState(null);
  const [err, setErr] = useState("");
  const groups = {};
  for (const n of data?.notes || []) {
    const i = n.lastIndexOf("/");
    (groups[i < 0 ? "" : n.slice(0, i)] ||= []).push(n);
  }
  async function create(e) {
    e.preventDefault(); setErr("");
    try {
      const r = await api("/nas/note", { method: "POST", body: { scope, ...form, mode: "create" } });
      setForm(null); await reload(); setOpen(r.saved.replace(/^Notes\//, ""));
    } catch (e2) { setErr(e2.message); }
  }
  return (
    <section>
      <h3 className="sub">Notes {data && <Tag>{data.count}</Tag>}
        {!form && <button className="btn primary small" onClick={() => setForm({ title: "", folder: "", content: "" })}>New note</button>}
      </h3>
      <ErrorNote error={error || err} />
      {form && (
        <form className="stack new-note" onSubmit={create}>
          <div className="field-row">
            <label className="field"><span>Title</span>
              <input value={form.title} required maxLength={100} autoFocus onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </label>
            <label className="field"><span>Folder (optional)</span>
              <input value={form.folder} maxLength={100} placeholder="e.g. Clients" onChange={(e) => setForm({ ...form, folder: e.target.value })} />
            </label>
          </div>
          <textarea className="note-editor small" value={form.content} placeholder="Markdown…" onChange={(e) => setForm({ ...form, content: e.target.value })} />
          <div className="form-actions">
            <button className="btn primary small" type="submit">Create</button>
            <button className="btn small" type="button" onClick={() => setForm(null)}>Cancel</button>
          </div>
        </form>
      )}
      {data && data.count === 0 && !form && <Empty>No notes yet. Create one here or tell Alfred "make a note…".</Empty>}
      <div className="note-tree">
        {Object.keys(groups).sort().map((g) => (
          <div key={g}>
            {g && <div className="note-folder">{g}</div>}
            {groups[g].map((n) => (
              <button key={n} className={`day${open === n ? " sel" : ""}`} onClick={() => setOpen(n)}>
                <span>{n.slice(n.lastIndexOf("/") + 1).replace(/\.md$/, "")}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      {open && <NoteView scope={scope} path={open} onChanged={reload} onClose={() => setOpen(null)} />}
    </section>
  );
}

/* ---------------------------------------------------------------- lists */
function ListCard({ scope, name }) {
  const { data, error, reload } = usePoll(() => api(`/nas/list?${q({ scope, name })}`), 0, [scope, name]);
  const [item, setItem] = useState("");
  const [err, setErr] = useState("");
  async function op(o, items) {
    setErr("");
    try { await api("/nas/list", { method: "POST", body: { scope, name, op: o, items } }); await reload(); }
    catch (e) { setErr(e.message); }
  }
  return (
    <div className="hud list-card">
      <h3 className="sub flush">{name}</h3>
      <ErrorNote error={error || err} />
      {(data?.open || []).map((t) => (
        <label key={t} className="toggle"><input type="checkbox" checked={false} onChange={() => op("check", [t])} /><span>{t}</span></label>
      ))}
      {(data?.done || []).map((t) => (
        <label key={t} className="toggle done"><input type="checkbox" checked onChange={() => op("uncheck", [t])} /><span>{t}</span></label>
      ))}
      <form className="field-row" onSubmit={(e) => { e.preventDefault(); if (item.trim()) op("add", [item.trim()]).then(() => setItem("")); }}>
        <input value={item} maxLength={200} placeholder="Add an item" aria-label={`Add to ${name}`} onChange={(e) => setItem(e.target.value)} />
        <button className="btn small" type="submit">Add</button>
        {data?.done?.length > 0 && <button className="btn small" type="button" onClick={() => op("clear_done", [])}>Clear done</button>}
      </form>
    </div>
  );
}

function ListsColumn({ scope }) {
  const { data, error, reload } = usePoll(() => api(`/nas/lists?${q({ scope })}`), 0, [scope]);
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  async function create(e) {
    e.preventDefault(); setErr("");
    try {
      await api("/nas/list", { method: "POST", body: { scope, name: name.trim(), op: "create" } });
      setName(""); reload();
    } catch (e2) { setErr(e2.message); }
  }
  return (
    <section>
      <h3 className="sub">Lists</h3>
      <ErrorNote error={error || err} />
      {data && data.lists.length === 0 && <Empty>No lists yet.</Empty>}
      <div className="list-grid">{(data?.lists || []).map((n) => <ListCard key={n} scope={scope} name={n} />)}</div>
      <form className="field-row" onSubmit={create}>
        <input value={name} maxLength={80} placeholder="New list name" aria-label="New list name" onChange={(e) => setName(e.target.value)} required />
        <button className="btn small" type="submit">Create list</button>
      </form>
    </section>
  );
}

/* ---------------------------------------------------------------- Company to-do */
const SECTION_LABEL = { current: "Aktuelles", future: "Zukunft", done: "Fertig" };

function CompanyTodo() {
  const { data, error, reload } = usePoll(() => api("/nas/company-todo"), 0);
  const [form, setForm] = useState({ description: "", importance: 3, section: "current" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function post(body) {
    setBusy(true); setErr("");
    try { await api("/nas/company-todo", { method: "POST", body }); await reload(); return true; }
    catch (e) { setErr(e.message); return false; } finally { setBusy(false); }
  }
  return (
    <section className="company-todo">
      <h3 className="sub">Company to-do</h3>
      <p className="muted small">The Excel file on the NAS; a backup is kept before every change.</p>
      <ErrorNote error={error || err} />
      {data && ["current", "future", "done"].map((k) => (
        <div key={k}>
          <div className="note-folder">{SECTION_LABEL[k]} · {data[k].length}</div>
          <table className="data">
            <tbody>
              {data[k].map((t) => (
                <tr key={t.row}>
                  <td className="mono">{t.importance}</td>
                  <td>{t.description}</td>
                  <td>
                    {k !== "done" && (
                      <select value={t.status || "not started"} disabled={busy} aria-label="Status"
                              onChange={(e) => post({ action: "update", todo: t.description, status: e.target.value })}>
                        <option>not started</option><option>in progress</option><option>done</option>
                      </select>
                    )}
                  </td>
                  <td>
                    {k !== "done" && (
                      <button className="btn small" disabled={busy}
                              onClick={() => post({ action: "move", todo: t.description, to: k === "current" ? "future" : "current" })}>
                        → {k === "current" ? "Zukunft" : "Aktuelles"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {data[k].length === 0 && <tr><td colSpan={4} className="muted">—</td></tr>}
            </tbody>
          </table>
        </div>
      ))}
      <form className="field-row" onSubmit={async (e) => {
        e.preventDefault();
        if (await post({ action: "add", ...form, importance: Number(form.importance) })) setForm({ ...form, description: "" });
      }}>
        <input value={form.description} required maxLength={300} placeholder="New to-do" aria-label="New to-do"
               onChange={(e) => setForm({ ...form, description: e.target.value })} />
        <select value={form.importance} aria-label="Importance" onChange={(e) => setForm({ ...form, importance: e.target.value })}>
          {[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>importance {n}</option>)}
        </select>
        <select value={form.section} aria-label="Section" onChange={(e) => setForm({ ...form, section: e.target.value })}>
          <option value="current">Aktuelles</option><option value="future">Zukunft</option>
        </select>
        <button className="btn primary small" type="submit" disabled={busy}>Add</button>
      </form>
    </section>
  );
}

/* ---------------------------------------------------------------- page */
export default function Notes({ assistant }) {
  const { data: st, error } = usePoll(() => api("/nas/status"));
  const [scope, setScope] = useState("personal");
  const [open, setOpen] = useState(null);
  if (!st) return <Panel code="10" title="Notes & lists"><ErrorNote error={error} /></Panel>;
  if (!st.enabled) return <Panel code="10" title="Notes & lists"><Empty>The NAS isn't connected.</Empty></Panel>;
  return (
    <Panel code="10" title="Notes & lists" actions={st.scopes.length > 1 && (
      <div className="seg" role="group" aria-label="Space">
        {st.scopes.map((s) => (
          <button key={s} className={s === scope ? "active" : ""} aria-pressed={s === scope}
                  onClick={() => { setScope(s); setOpen(null); }}>{s === "personal" ? "Mine" : "Company"}</button>
        ))}
      </div>
    )}>
      <p className="muted small">
        {scope === "personal" ? `Your own notes and lists on the NAS; only you can see them. ${assistant} manages the same files when you ask in chat.`
          : "Shared Company notes and lists for admins and cofounders, stored in the Company share."}
      </p>
      <div className="notes-grid">
        <NotesColumn scope={scope} open={open} setOpen={setOpen} />
        <ListsColumn scope={scope} />
      </div>
      {scope === "company" && st.company_todo && <CompanyTodo />}
    </Panel>
  );
}
