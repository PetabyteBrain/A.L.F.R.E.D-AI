import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiUpload } from "../api.js";
import { usePageActive, usePoll } from "../hooks.js";
import { Empty, ErrorNote } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";
import FileEditor from "../components/FileEditor.jsx";
import CompanyTodo from "../components/CompanyTodo.jsx";

const SCOPE_LABEL = { personal: "Mine", company: "Company", people: "All people" };   // company: COMPANY_NAME
const q = (o) => new URLSearchParams(o).toString();
const join = (dir, name) => (dir ? `${dir}/${name}` : name);
const fmtSize = (n) => (n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const TEMPLATES = {
  note: { label: "Note (.md)", ext: ".md", body: (t) => `# ${t}\n\n` },
  list: { label: "List (.md checklist)", ext: ".md", body: (t) => `# ${t}\n\n- [ ] \n`, folder: "Lists" },
  text: { label: "Text (.txt)", ext: ".txt", body: () => "" },
};

const MAX_UPLOAD = 50_000_000;                     // nas_files.MAX_UPLOAD_BYTES

/* Files (and whole folders) from a drop, each with the sub-folder it came from. */
async function droppedFiles(dt) {
  const entries = [...(dt.items || [])].map((it) => (it.kind === "file" && it.webkitGetAsEntry ? it.webkitGetAsEntry() : null));
  if (!entries.some(Boolean)) return [...(dt.files || [])].map((file) => ({ file, dir: "" }));
  const out = [];
  const fileOf = (entry) => new Promise((res, rej) => entry.file(res, rej));
  const readAll = (reader) => new Promise((res, rej) => {
    const all = [];
    const next = () => reader.readEntries((batch) => (batch.length ? (all.push(...batch), next()) : res(all)), rej);
    next();
  });
  async function walk(entry, dir) {
    if (out.length > 500) return;                   // a sane cap for one drop
    if (entry.isFile) out.push({ file: await fileOf(entry), dir });
    else if (entry.isDirectory) for (const child of await readAll(entry.createReader())) await walk(child, join(dir, entry.name));
  }
  for (const entry of entries.filter(Boolean)) await walk(entry, "");
  return out;
}
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");

/* ---------------------------------------------------------------- explorer */
function Explorer({ scope, path, setPath, onOpen, refreshKey, onChanged }) {
  const { data, error, reload } = usePoll(() => api(`/nas/browse?${q({ scope, path })}`), 0, [scope, path, refreshKey]);
  const [create, setCreate] = useState(null);       // {kind, name}
  const [rename, setRename] = useState(null);       // {name, value}
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [drop, setDrop] = useState(null);           // null | "" (this folder) | sub-folder name
  const [progress, setProgress] = useState(null);   // {done, total, current}
  const [note, setNote] = useState("");
  const depth = useRef(0);
  const active = usePageActive();
  const upRef = useRef(null);
  const crumbs = path ? path.split("/") : [];

  // A file dropped next to the explorer must not make the browser open it instead (only while this tab is shown).
  useEffect(() => {
    if (!active) return undefined;
    const stop = (ev) => { if (hasFiles(ev)) ev.preventDefault(); };
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => { window.removeEventListener("dragover", stop); window.removeEventListener("drop", stop); };
  }, [active]);

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
  /* Uploads one after the other, carrying on past a failed file; a summary at the end. */
  async function uploadAll(items, base) {
    if (!items.length || busy) return;
    setBusy(true); setErr(""); setNote("");
    const failed = [];
    let done = 0;
    for (const { file, dir } of items) {
      const folder = join(base, dir);
      setProgress({ done, total: items.length, current: join(dir, file.name) });
      if (file.size > MAX_UPLOAD) failed.push(`${file.name}: too big (max 50 MB)`);
      else {
        const fd = new FormData();
        fd.append("scope", scope); fd.append("folder", folder); fd.append("file", file);
        try { await apiUpload("/nas/upload", fd); } catch (x) { failed.push(`${file.name}: ${x.message}`); }
      }
      done += 1;
    }
    setProgress(null); setBusy(false);
    await reload(); onChanged?.();
    const ok = items.length - failed.length;
    if (ok) setNote(`Uploaded ${ok} file${ok === 1 ? "" : "s"}${base !== path ? ` to ${base.split("/").pop()}` : ""}.`);
    if (failed.length) setErr(`Not uploaded: ${failed.slice(0, 5).join("; ")}${failed.length > 5 ? ` and ${failed.length - 5} more` : ""}`);
  }
  async function upload(e) {
    const files = [...(e.target.files || [])].map((file) => ({ file, dir: "" }));
    e.target.value = "";
    await uploadAll(files, path);
  }
  const dragEnter = (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth.current += 1; if (drop === null) setDrop(""); };
  const dragLeave = (e) => { if (!hasFiles(e)) return; depth.current -= 1; if (depth.current <= 0) { depth.current = 0; setDrop(null); } };
  const dragOver = (e, target = "") => {
    if (!hasFiles(e)) return;
    e.preventDefault(); e.stopPropagation();
    e.dataTransfer.dropEffect = busy ? "none" : "copy";
    if (drop !== target) setDrop(target);
  };
  async function onDrop(e, target = "") {
    if (!hasFiles(e)) return;
    e.preventDefault(); e.stopPropagation();
    depth.current = 0; setDrop(null);
    if (busy) return;
    await uploadAll(await droppedFiles(e.dataTransfer), join(path, target));
  }

  return (
    <section className={`explorer${drop !== null ? " dropping" : ""}`} onDragEnter={dragEnter} onDragLeave={dragLeave}
             onDragOver={(e) => dragOver(e)} onDrop={(e) => onDrop(e)}>
      {drop !== null && (
        <div className="drop-hint" aria-hidden="true">
          <div className="drop-card">
            <span>⤒ Drop to upload to <b>{drop ? drop : crumbs.length ? crumbs[crumbs.length - 1] : SCOPE_LABEL[scope]}</b></span>
            <span className="muted tiny">Files and whole folders, up to 50 MB each. Drop on a folder to put them in it.</span>
          </div>
        </div>
      )}
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
        <button className="btn small" disabled={busy} onClick={() => upRef.current?.click()}
                title="Or drag files and folders from your computer onto the list">Upload</button>
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
      {progress && (
        <div className="upload-progress" role="status">
          <span>Uploading {progress.done + 1} of {progress.total}: {progress.current}</span>
          <span className="upload-bar"><span style={{ width: `${(progress.done / progress.total) * 100}%` }} /></span>
        </div>
      )}
      {note && !progress && <div className="notice" role="status" onClick={() => setNote("")}>{note}</div>}
      <ErrorNote error={error || err} />
      {data && data.items.length === 0 && <Empty>Empty folder. Drop files here to upload them.</Empty>}
      <div className="file-list">
        {(data?.items || []).map((it) => (
          <div className={`file-row${it.dir && drop === it.name ? " drop-target" : ""}`} key={it.name}
               onDragOver={it.dir ? (e) => dragOver(e, it.name) : undefined}
               onDrop={it.dir ? (e) => onDrop(e, it.name) : undefined}>
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
                <button className="file-name" title={it.name} onClick={() => (it.dir ? setPath(join(path, it.name)) : onOpen({ scope, path: join(path, it.name) }))}>
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
            <button key={r.scope + r.path} className="file-name" title={r.path} onClick={() => onOpen({ scope: r.scope, path: r.path })}>
              <span className="muted tiny">{SCOPE_LABEL[r.scope]}</span> {r.path.split("/").pop()}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- tidy up (own notes, .md/.txt) */
const TIDY_KINDS = {
  structure: { label: "Folders", hint: "Group notes into clear folders. Contents stay as they are." },
  content: { label: "Contents", hint: "Reformat messy notes, combine or split them. Folders stay as they are." },
};

function TidyUp({ assistant, unsaved, onDone, onClose }) {
  const [kind, setKind] = useState("structure");
  const [wishes, setWishes] = useState("");
  const [plan, setPlan] = useState(null);           // {summary, digest, steps, skipped}
  const [ticked, setTicked] = useState(() => new Set());
  const [openText, setOpenText] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  async function ask(e) {
    e.preventDefault();
    setBusy("plan"); setErr(""); setPlan(null); setResult(null);
    try {
      const p = await api("/nas/tidy/plan", { method: "POST", body: { kind, wishes } });
      setPlan(p);
      setTicked(new Set(p.steps.map((_, i) => i)));
    } catch (e2) { setErr(e2.message); } finally { setBusy(""); }
  }
  async function apply() {
    setBusy("apply"); setErr("");
    try {
      const r = await api("/nas/tidy/apply", { method: "POST", body: { kind, digest: plan.digest, steps: [...ticked].sort((a, b) => a - b) } });
      setResult(r); setPlan(null); onDone();
    } catch (e2) { setErr(e2.message); } finally { setBusy(""); }
  }
  const toggle = (i) => setTicked((t) => { const n = new Set(t); n.has(i) ? n.delete(i) : n.add(i); return n; });

  return (
    <section className="tidy-box">
      <div className="tidy-head">
        <h3 className="sub flush">Tidy up my notes</h3>
        <button className="btn ghost small" onClick={onClose} aria-label="Close tidy up">×</button>
      </div>
      {!plan && !result && (
        <form onSubmit={ask} className="tidy-form">
          <div className="seg" role="group" aria-label="What to tidy">
            {Object.entries(TIDY_KINDS).map(([k, v]) => (
              <button type="button" key={k} className={k === kind ? "active" : ""} aria-pressed={k === kind} onClick={() => setKind(k)}>{v.label}</button>
            ))}
          </div>
          <p className="muted small">{TIDY_KINDS[kind].hint} Only .md and .txt notes in your own folder; {assistant} proposes, you pick the steps, nothing changes before you press Apply.</p>
          <textarea rows={2} maxLength={1000} value={wishes} onChange={(e) => setWishes(e.target.value)}
                    placeholder="Anything in particular? e.g. put all recipes together (optional)" aria-label="Wishes" />
          {unsaved > 0 && <p className="warn-text small">You have unsaved edits in an open note: save them first.</p>}
          <button className="btn primary small" type="submit" disabled={!!busy}>
            {busy === "plan" ? `${assistant} is looking through your notes…` : "Propose a plan"}
          </button>
        </form>
      )}
      <ErrorNote error={err} />
      {plan && (
        <div className="tidy-plan">
          {plan.summary && <p className="small">{plan.summary}</p>}
          {plan.steps.length === 0 ? <Empty>Nothing to do.</Empty> : (
            <ul className="tidy-steps">
              {plan.steps.map((s, i) => (
                <li key={i}>
                  <label><input type="checkbox" checked={ticked.has(i)} onChange={() => toggle(i)} /> {s.line}</label>
                  {s.text != null && (
                    <button className="link tiny" onClick={() => setOpenText(openText === i ? null : i)}>{openText === i ? "hide new text" : "show new text"}</button>
                  )}
                  {openText === i && <pre className="tidy-text">{s.text}</pre>}
                </li>
              ))}
            </ul>
          )}
          {plan.skipped?.length > 0 && (
            <details className="muted tiny"><summary>{plan.skipped.length} proposed step(s) left out (they didn't hold up)</summary>
              <ul>{plan.skipped.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </details>
          )}
          <div className="field-row">
            {plan.steps.length > 0 && (
              <button className="btn primary small" onClick={apply} disabled={!!busy || ticked.size === 0}>
                {busy === "apply" ? "Applying…" : `Apply ${ticked.size} step${ticked.size === 1 ? "" : "s"}`}
              </button>
            )}
            <button className="btn small" onClick={() => setPlan(null)} disabled={!!busy}>Discard</button>
          </div>
          <p className="muted tiny">Nothing is lost: replaced and removed notes go to the NAS trash (.trash).</p>
        </div>
      )}
      {result && (
        <div className="tidy-plan">
          <p className="small">{result.applied === true ? "Done." : `Partly done: stopped at "${result.stopped_at}".`}</p>
          <ul className="tidy-steps muted small">{(result.done || []).map((d, i) => <li key={i}>{d}</li>)}</ul>
          <p className="muted tiny">{result.note}</p>
          <button className="btn small" onClick={() => setResult(null)}>Another plan</button>
        </div>
      )}
    </section>
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
  const [tidy, setTidy] = useState(false);
  const [wide, setWide] = useState(() => { try { return localStorage.getItem("alfred-files-wide") === "1"; } catch { return false; } });
  useEffect(() => {
    try { localStorage.setItem("alfred-files-wide", wide ? "1" : "0"); } catch { /* storage blocked */ }
    if (!wide) return undefined;
    const esc = (e) => { if (e.key === "Escape" && !e.target.closest?.("textarea, input")) setWide(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [wide]);
  const [dirty, setDirty] = useState(() => new Set());     // open files with unsaved edits
  const markDirty = useCallback((k, d) => setDirty((s) => {
    if (d === s.has(k)) return s;
    const n = new Set(s); d ? n.add(k) : n.delete(k); return n;
  }), []);
  useEffect(() => {                                        // closing or reloading the browser tab
    if (!dirty.size) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const { data: recent, reload: reloadRecent } = usePoll(() => api("/nas/recent"), 0, [refresh]);
  const key = (f) => `${f.scope}:${f.path}`;

  const open = useCallback((f) => {
    setTabs((t) => (t.some((x) => key(x) === key(f)) ? t : [...t, f].slice(-8)));
    setActive(key(f));
    setTimeout(() => reloadRecent(), 300);
  }, [reloadRecent]);
  const close = (f) => {
    if (dirty.has(key(f)) && !window.confirm(`"${f.path.split("/").pop()}" has unsaved changes. Close it anyway?`)) return;
    markDirty(key(f), false);
    setTabs((t) => t.filter((x) => key(x) !== key(f)));
    if (active === key(f)) setActive(null);
  };
  useEffect(() => { setPath(""); }, [scope]);

  if (!st) return <Panel code="10" title="Notes & files"><ErrorNote error={error} /></Panel>;
  if (!st.enabled) return <Panel code="10" title="Notes & files"><Empty>The NAS isn't connected.</Empty></Panel>;
  const current = tabs.find((t) => key(t) === active);
  SCOPE_LABEL.company = st.company_name || "Company";
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
        {scope === "company" && `The ${SCOPE_LABEL.company} files, for admins and cofounders. Deleting moves things to the NAS trash.`}
        {scope === "people" && "Every person's personal folder (admins only). Handle with care: these are private."}
        {scope === "personal" && !tidy && <> <button className="link" onClick={() => setTidy(true)}>Tidy up my notes…</button></>}
      </p>
      {scope === "personal" && tidy && (
        <TidyUp assistant={assistant} unsaved={dirty.size} onClose={() => setTidy(false)}
                onDone={() => setRefresh((n) => n + 1)} />
      )}
      <div className={`files-grid${wide && current ? " wide" : ""}`}>
        <div className="files-side">
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
                    {t.path.split("/").pop()}{dirty.has(key(t)) && <span className="dirty-dot" title="Unsaved changes"> •</span>}
                  </button>
                  <button aria-label={`Close ${t.path}`} onClick={() => close(t)}>×</button>
                </span>
              ))}
            </div>
          )}
          {/* every open file stays mounted, so switching between file tabs keeps unsaved edits */}
          {tabs.map((t) => (
            <div key={key(t)} hidden={key(t) !== active}>
              <div className="file-path-row">
                <span className="muted tiny file-path">{SCOPE_LABEL[t.scope]} / {t.path}</span>
                <button className="btn ghost small" onClick={() => setWide(!wide)} aria-pressed={wide}
                        title={wide ? "Back to the explorer beside the file (Esc)" : "Use the whole page width for this file"}>
                  {wide ? "⤡ Show explorer" : "⤢ Page width"}
                </button>
              </div>
              <FileEditor file={t} onSaved={() => setRefresh((n) => n + 1)} onDirty={(d) => markDirty(key(t), d)} />
            </div>
          ))}
          {!current && <Empty>Open a file from the explorer, or create a new note or list.</Empty>}
        </div>
      </div>
      {scope === "company" && st.company_todo && <CompanyTodo name={SCOPE_LABEL.company} />}
    </Panel>
  );
}
