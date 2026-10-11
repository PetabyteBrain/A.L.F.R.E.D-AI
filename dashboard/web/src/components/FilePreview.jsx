import { useEffect, useState } from "react";
import { api } from "../api.js";

// Read-only previews. PDFs, pictures, audio and video come straight from /api/nas/view. Office documents
// are shown in their real layout as a PDF made on the server (orchestrator/office_pdf.py, LibreOffice);
// "Quick view" draws their text/tables from plain data instead (orchestrator/file_preview.py, never HTML).
const q = (scope, path) => new URLSearchParams({ scope, path }).toString();
const ext = (p) => (p.match(/\.[^./]+$/)?.[0] || "").toLowerCase();
const IMAGE = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"];
const AUDIO = [".mp3", ".m4a", ".wav", ".ogg"];
const VIDEO = [".mp4", ".webm", ".mov"];
const OFFICE = [".docx", ".doc", ".docm", ".dotx", ".odt", ".rtf", ".xlsx", ".xlsm", ".xls", ".ods",
  ".pptx", ".ppt", ".pptm", ".ppsx", ".odp"];
const QUICK = [".docx", ".odt", ".xlsx", ".xlsm", ".pptx"];          // also readable as plain data
export const DATA_PREVIEW = [".csv", ".tsv", ".xml", ".html", ".htm", ".ini", ".conf", ".cfg", ".toml", ".sh", ".ps1", ".bat", ".py", ".js", ".jsx", ".ts", ".tsx",
  ".css", ".sql", ".srt", ".vtt", ".tex", ".rtf"];

export function previewKind(path) {
  const e = ext(path);
  if (e === ".pdf") return "pdf";
  if (IMAGE.includes(e)) return "image";
  if (AUDIO.includes(e)) return "audio";
  if (VIDEO.includes(e)) return "video";
  if (OFFICE.includes(e)) return "office";
  return DATA_PREVIEW.includes(e) ? "data" : null;
}

function Table({ rows, header = true }) {
  if (!rows.length) return <p className="muted small">(empty)</p>;
  const [head, ...rest] = header ? rows : [null, ...rows];
  return (
    <div className="pv-table-wrap">
      <table className="pv-table">
        {head && <thead><tr>{head.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>}
        <tbody>{rest.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Doc({ blocks }) {
  const out = [];
  let list = null;
  blocks.forEach((b, i) => {
    if (b.type === "li") {
      if (!list) { list = []; out.push(<ul key={`l${i}`}>{list}</ul>); }
      list.push(<li key={i}>{b.text}</li>);
      return;
    }
    list = null;
    if (b.type === "h") {
      const H = `h${Math.min(6, (b.level || 1) + 2)}`;
      out.push(<H key={i}>{b.text}</H>);
    } else if (b.type === "table") out.push(<Table key={i} rows={b.rows} header={false} />);
    else out.push(<p key={i}>{b.text}</p>);
  });
  return <div className="note-body pv-doc">{out.length ? out : <p className="muted">(no text)</p>}</div>;
}

function Sheets({ sheets }) {
  const [at, setAt] = useState(0);
  const s = sheets[Math.min(at, sheets.length - 1)];
  if (!s) return <p className="muted">(empty workbook)</p>;
  return (
    <div>
      {sheets.length > 1 && (
        <div className="seg pv-sheets" role="tablist" aria-label="Sheets">
          {sheets.map((x, i) => (
            <button key={i} role="tab" aria-selected={i === at} className={i === at ? "active" : ""} onClick={() => setAt(i)} title={x.name || `Sheet ${i + 1}`}>{x.name || `Sheet ${i + 1}`}</button>
          ))}
        </div>
      )}
      <Table rows={s.rows} />
      {s.more && <p className="muted tiny">Only the first rows are shown; download the file for the rest.</p>}
    </div>
  );
}

function Slides({ slides }) {
  if (!slides.length) return <p className="muted">(no slides)</p>;
  return (
    <div className="pv-slides">
      {slides.map((s) => (
        <section key={s.n} className="pv-slide">
          <div className="muted tiny">Slide {s.n}</div>
          {s.title && <h4>{s.title}</h4>}
          {s.text.length > 0 && <ul>{s.text.map((t, i) => <li key={i}>{t}</li>)}</ul>}
          {s.notes.length > 0 && <p className="muted small pv-notes">Notes: {s.notes.join(" ")}</p>}
        </section>
      ))}
    </div>
  );
}

// Where a preview comes from: a NAS file by default; mail attachments pass their own URLs (Mail tab).
function nasUrls(scope, path, version) {
  return {
    view: `/api/nas/view?${q(scope, path)}&r=${version}`,
    pdf: `/api/nas/view?${q(scope, path)}&as=pdf&r=${version}`,
    prepare: () => api("/nas/view/prepare", { method: "POST", body: { scope, path } }),
    data: () => api(`/nas/preview?${q(scope, path)}`),
  };
}

function DataView({ urls, path, version }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let live = true;
    setData(null); setErr("");
    urls.data().then((d) => live && setData(d)).catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [urls.view, path, version]);
  if (err) return <p className="muted small">{err}</p>;
  if (!data) return <p className="muted">Loading preview…</p>;
  if (data.kind === "doc") return <Doc blocks={data.blocks} />;
  if (data.kind === "sheets") return <Sheets sheets={data.sheets} />;
  if (data.kind === "slides") return <Slides slides={data.slides} />;
  return (
    <>
      <pre className="pv-text">{data.text}</pre>
      {data.truncated && <p className="muted tiny">Only the beginning is shown; download the file for the rest.</p>}
    </>
  );
}

/* Word / Excel / PowerPoint: the document as a PDF in its real layout (made on the server, cached). */
function OfficeView({ urls, path, version }) {
  const quickOk = QUICK.includes(ext(path));
  const [mode, setMode] = useState("layout");
  const [state, setState] = useState("preparing");          // preparing | ready | error
  const [err, setErr] = useState("");
  useEffect(() => {
    if (mode !== "layout") return undefined;
    let live = true;
    setState("preparing"); setErr("");
    urls.prepare()
      .then(() => live && setState("ready"))
      .catch((e) => { if (live) { setErr(e.message); setState("error"); } });
    return () => { live = false; };
  }, [urls.pdf, path, version, mode]);
  return (
    <div>
      {quickOk && (
        <div className="seg pv-modes" role="group" aria-label="View">
          <button className={mode === "layout" ? "active" : ""} aria-pressed={mode === "layout"} onClick={() => setMode("layout")}>Original layout</button>
          <button className={mode === "quick" ? "active" : ""} aria-pressed={mode === "quick"} onClick={() => setMode("quick")}>Quick view</button>
        </div>
      )}
      {mode === "quick" ? <DataView urls={urls} path={path} version={version} /> : (
        <>
          {state === "preparing" && <p className="muted small">Preparing the document… (the first time can take a few seconds)</p>}
          {state === "error" && <p className="muted small">{err}{quickOk ? " Try Quick view." : ""}</p>}
          {state === "ready" && (
            <iframe className="pv-frame" title={`Preview of ${path}`} src={urls.pdf} />
          )}
        </>
      )}
    </div>
  );
}

function Video({ src, path }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <p className="muted small">This video can't be played in the browser (often an iPhone .mov in HEVC). Download it to watch it.</p>;
  }
  return (
    <div className="pv-media">
      <video controls playsInline preload="metadata" src={src} onError={() => setFailed(true)} aria-label={path.split("/").pop()} />
    </div>
  );
}

export default function FilePreview({ scope, path, version = 0, urls: given }) {
  const kind = previewKind(path);
  const urls = given || nasUrls(scope, path, version);
  const src = urls.view;
  if (!kind) return <p className="muted small">No preview for this file type. Download it to open it.</p>;
  if (kind === "pdf") return <iframe className="pv-frame" src={src} title={`Preview of ${path}`} />;
  if (kind === "image") return <div className="pv-media"><img src={src} alt={path.split("/").pop()} /></div>;
  if (kind === "audio") return <audio className="pv-audio" controls preload="metadata" src={src} />;
  if (kind === "video") return <Video src={src} path={path} />;
  if (kind === "office") return <OfficeView urls={urls} path={path} version={version} />;
  return <DataView urls={urls} path={path} version={version} />;
}
