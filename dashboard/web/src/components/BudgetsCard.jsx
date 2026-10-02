import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Monthly budgets (admin): warnings go to admins on Telegram at 80 % and 100 %. */
export default function BudgetsCard() {
  const { data: b, error, reload } = usePoll(() => api("/budgets"), 300000);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });

  useEffect(() => {
    if (b) setForm(Object.fromEntries(b.rows.map((r) => [r.provider, r.budget ?? ""])));
  }, [b]);
  if (!b || !form) return <section className="budgets"><h3 className="sub">Monthly budgets</h3><ErrorNote error={error} /></section>;

  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setMsg({ ok: "", err: "" });
    try {
      const body = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v === "" ? null : Number(v)]));
      await api("/budgets", { method: "PUT", body });
      await reload();
      setMsg({ ok: "Budgets saved.", err: "" });
    } catch (err) { setMsg({ ok: "", err: err.message }); } finally { setBusy(false); }
  }

  return (
    <section className="budgets">
      <h3 className="sub">Monthly budgets · {b.month}</h3>
      <p className="muted small">Admins get a Telegram message at 80 % and 100 % of a budget. Nothing is switched off. Empty = no budget.</p>
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err || error} />
      <form onSubmit={save}>
        <table className="data">
          <thead><tr><th>Provider</th><th>Spent this month</th><th>Budget (USD)</th><th /></tr></thead>
          <tbody>
            {b.rows.map((r) => (
              <tr key={r.provider}>
                <td>{r.label}</td>
                <td>{r.spent == null ? "—" : `$${r.spent.toFixed(2)}`}</td>
                <td>
                  <input className="budget-input" type="number" min="0.01" step="0.01" inputMode="decimal"
                         aria-label={`${r.label} budget`} value={form[r.provider]}
                         onChange={(e) => setForm({ ...form, [r.provider]: e.target.value })} />
                </td>
                <td>{r.percent == null ? null : <Tag kind={r.percent >= 100 ? "bad" : r.percent >= 80 ? "busy" : "ok"}>{r.percent} %</Tag>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="form-actions"><button className="btn primary small" type="submit" disabled={busy}>Save budgets</button></div>
      </form>
      <p className="muted tiny">Claude and ElevenLabs are estimates from metered usage; Twilio is its real month-to-date bill.</p>
    </section>
  );
}
