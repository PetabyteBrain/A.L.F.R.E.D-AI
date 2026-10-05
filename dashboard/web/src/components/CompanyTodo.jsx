import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote } from "./common.jsx";

/* ---------------------------------------------------------------- Company to-do */
const SECTION_LABEL = { current: "Aktuelles", future: "Zukunft", done: "Fertig" };

export default function CompanyTodo() {
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

