import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiUpload } from "../api.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";
import FileEditor from "../components/FileEditor.jsx";
import CompanyTodo from "../components/CompanyTodo.jsx";

const SCOPE_LABEL = { personal: "Mine", company: "Company", people: "All people" };
const q = (o) => new URLSearchParams(o).toString();
const join = (dir, name) => (dir ? `${dir}/${name}` : name);
const fmtSize = (n) => (n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const TEMPLATES = {
  note: { label: "Note (.md)", ext: ".md", body: (t) => `# ${t}\n\n` },
  list: { label: "List (.md checklist)", ext: ".md", body: (t) => `# ${t}\n\n- [ ] \n`, folder: "Lists" },
  text: { label: "Text (.txt)", ext: ".txt", body: () => "" },
};

/* ---------------------------------------------------------------- explorer */
function Explorer({ scope, path, setPath, onOpen, refreshKey, onChanged }) {
  const { data, error, reload } = usePoll(() => api(`/nas/browse?${q({ scope, path })}`), 0, [scope, path, refreshKey]);
  const [create, setCreate] = useState(null);       // {kind, name}
  const [rename, setRename] = useState(null);       // {name, value}
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const upRef = useRef(null);
  const crumbs = path ? path.split("/") : [];

  async function run(fn) {
    setBusy(true); setErr("");
    try { await fn(); await reload(); onChanged?.(); return true; } catch (e) { setErr(e.message); return false; } finally { setBusy(false); }
  }
  async function doCreate(e) {
    e.preventDefault();
    const name = create.name.trim();
    if (!name) return;
    if (create.kind === "folder") {
      if (await run(() => api("/nas/folder", { method: "POST", body: { scope, path: join(path, name) } }))) setCreate(null);
      return;
    }
    const t = TEMPLATES[create.kind];
    const file = name.toLowerCase().endsWith(t.ext) ? name : name + t.ext;
    const dir = t.folder && !path ? t.folder : path;
    const full = join(dir, file);
    if (await run(() => api("/nas/file", { method: "PUT", body: { scope, path: full, text: t.body(name.replace(/\.[^.]+$/, "")), create: true } }))) {
      setCreate(null); onOpen({ scope, path: full });
    }
  }
  async function upload(e) {
    const files = [...(e.target.files || [])];
    for (const f of files) {
      const fd = new FormData();
      fd.append("scope", scope); fd.append("folder", path); fd.append("file", f);
      await run(() => apiUpload("/nas/upload", fd));
    }
    e.target.value = "";
  }

  return (
    <section className="explorer">
      <div className="crumbs">
        <button className="link" onClick={() => setPath("")}>{SCOPE_LABEL[scope]}</button>
        {crumbs.map((c, i) => (
          <span key={i}> / <button className="link" onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}>{c}</button></span>
        ))}
      </div>
      <div className="explorer-tools">
        <select value="" aria-label="New" disabled={busy} onChange={(e) => { if (e.target.value) setCreate({ kind: e.target.value, name: "" }); }}>
          <option value="">+ New…</option>
          {Object.entries(TEMPLATES).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
          <option value="folder">Folder</option>
        </select>
        <button className="btn small" disabled={busy} onClick={() => upRef.current?.click()}>Upload</button>
        <input ref={upRef} type="file" multiple hidden onChange={upload} />
        {path && <button className="btn small" onClick={() => setPath(path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "")}>↑ Up</button>}
      </div>
      {create && (
        <form className="field-row" onSubmit={doCreate}>
          <input autoFocus value={create.name} maxLength={100} required
                 placeholder={create.kind === "folder" ? "Folder name" : `${TEMPLATES[create.kind].label.split(" ")[0]} name`}
                 onChange={(e) => setCreate({ ...create, name: e.target.value })} />
          <button className="btn primary small" type="submit" disabled={busy}>Create</button>
          <button className="btn small" type="button" onClick={() => setCreate(null)}>Cancel</button>
        </form>
      )}
      {create?.kind === "list" && !path && <p className="muted tiny">New lists go into the Lists folder.</p>}
      <ErrorNote error={error || err} />
      {data && data.items.length === 0 && <Empty>Empty folder.</Empty>}
      <div className="file-list">
        {(data?.items || []).map((it) => (
          <div className="file-row" key={it.name}>
            {rename?.name === it.name ? (
              <form className="field-row" onSubmit={async (e) => {
                e.preventDefault();
                if (await run(() => api("/nas/move", { method: "POST", body: { scope, path: join(path, it.name), new_path: rename.value.trim() } }))) setRename(null);
              }}>
                <input autoFocus value={rename.value} maxLength={300} aria-label="New name or path"
                       onChange={(e) => setRename({ ...rename, value: e.target.value })} />
                <button className="btn primary small" type="submit">OK</button>
                <button className="btn small" type="button" onClick={() => setRename(null)}>×</button>
              </form>
            ) : (
              <>
                <button className="file-name" onClick={() => (it.dir ? setPath(join(path, it.name)) : onOpen({ scope, path: join(path, it.name) }))}>
                  <span aria-hidden="true">{it.dir ? "📁" : /\.md$/i.test(it.name) ? "📝" : "📄"}</span> {it.name}
                </button>
                <span className="muted tiny">{it.dir ? "" : fmtSize(it.size)} {it.modified}</span>
                <span className="file-actions">
                  <button title="Rename or move (type a new name, or a path like Archive/name)" onClick={() => setRename({ name: it.name, value: join(path, it.name) })}>✎</button>
                  {!it.dir && <a title="Download" href={`/api/nas/download?${q({ scope, path: join(path, it.name) })}`} download>⤓</a>}
                  <button title="Move to the trash" onClick={() => {
                    if (window.confirm(`Move "${it.name}" to the trash?`)) run(() => api(`/nas/path?${q({ scope, path: join(path, it.name) })}`, { method: "DELETE" }));
                  }}>🗑</button>
                </span>
              </>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- recent */
function Recent({ data, onOpen, scopes }) {
  const show = (rows) => (rows || []).filter((r) => scopes.includes(r.scope)).slice(0, 8);
  return (
    <div className="recent-files">
      {["edited", "viewed"].map((k) => (
        <div key={k}>
          <div className="note-folder">Recently {k}</div>
          {show(data?.[k]).length === 0 && <p className="muted tiny">Nothing yet.</p>}
          {show(data?.[k]).map((r) => (
            <button key={r.scope + r.path} className="file-name" onClick={() => onOpen({ scope: r.scope, path: r.path })}>
              <span className="muted tiny">{SCOPE_LABEL[r.scope]}</span> {r.path.split("/").pop()}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- page */
export default function Notes({ assistant }) {
  const { data: st, error } = usePoll(() => api("/nas/status"));
  const [scope, setScope] = useState("personal");
  const [path, setPath] = useState("");
  const [tabs, setTabs] = useState([]);              // open files [{scope, path}]
  const [active, setActive] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const { data: recent, reload: reloadRecent } = usePoll(() => api("/nas/recent"), 0, [refresh]);
  const key = (f) => `${f.scope}:${f.path}`;

  const open = useCallback((f) => {
    setTabs((t) => (t.some((x) => key(x) === key(f)) ? t : [...t, f].slice(-8)));
    setActive(key(f));
    setTimeout(() => reloadRecent(), 300);
  }, [reloadRecent]);
  const close = (f) => {
    setTabs((t) => t.filter((x) => key(x) !== key(f)));
    if (active === key(f)) setActive(null);
  };
  useEffect(() => { setPath(""); }, [scope]);

  if (!st) return <Panel code="10" title="Notes & files"><ErrorNote error={error} /></Panel>;
  if (!st.enabled) return <Panel code="10" title="Notes & files"><Empty>The NAS isn't connected.</Empty></Panel>;
  const current = tabs.find((t) => key(t) === active);
  return (
    <Panel code="10" title="Notes & files" actions={st.scopes.length > 1 && (
      <div className="seg" role="group" aria-label="Space">
        {st.scopes.map((s) => (
          <button key={s} className={s === scope ? "active" : ""} aria-pressed={s === scope} onClick={() => setScope(s)}>{SCOPE_LABEL[s]}</button>
        ))}
      </div>
    )}>
      <p className="muted small">
        {scope === "personal" && `Your own files, notes and lists on the NAS; only you (and admins) can open them. ${assistant} works on the same files when you ask in chat.`}
        {scope === "company" && "The Company company files, for admins and cofounders. Deleting moves things to the NAS trash."}
        {scope === "people" && "Every person's personal folder (admins only). Handle with care: these are private."}
      </p>
      <div className="files-grid">
        <div>
          <Explorer scope={scope} path={path} setPath={setPath} onOpen={open} refreshKey={refresh}
                    onChanged={() => setRefresh((n) => n + 1)} />
          <Recent data={recent} onOpen={open} scopes={st.scopes} />
        </div>
        <div className="editor-col">
          {tabs.length > 0 && (
            <div className="file-tabs" role="tablist" aria-label="Open files">
              {tabs.map((t) => (
                <span key={key(t)} className={`file-tab${key(t) === active ? " active" : ""}`}>
                  <button role="tab" aria-selected={key(t) === active} onClick={() => setActive(key(t))} title={`${SCOPE_LABEL[t.scope]}: ${t.path}`}>
                    {t.path.split("/").pop()}
                  </button>
                  <button aria-label={`Close ${t.path}`} onClick={() => close(t)}>×</button>
                </span>
              ))}
            </div>
          )}
          {current ? (
            <>
              <div className="muted tiny file-path">{SCOPE_LABEL[current.scope]} / {current.path}</div>
              <FileEditor key={key(current)} file={current} onSaved={() => setRefresh((n) => n + 1)} />
            </>
          ) : <Empty>Open a file from the explorer, or create a new note or list.</Empty>}
        </div>
      </div>
      {scope === "company" && st.company_todo && <CompanyTodo />}
    </Panel>
  );
}
