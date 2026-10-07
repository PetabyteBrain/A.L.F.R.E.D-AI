import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

const EFFORT_HINT = { low: "quickest, cheapest", medium: "balanced", high: "most thorough, slowest" };

/* Admin (Settings tab): which API key pays (from .env) and which Claude model answers. */
export default function AiModelCard({ assistant }) {
  const { data, error, reload } = usePoll(() => api("/settings/ai"));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  if (!data) return <ErrorNote error={error} />;
  const cur = data.current;
  const model = data.models.find((m) => m.id === cur.model);

  async function set(change) {
    setBusy(true); setErr(""); setOk("");
    try { await api("/settings/ai", { method: "PUT", body: change }); await reload(); setOk("Saved. It applies from the next message."); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const price = (m) => `$${m.price_in} / $${m.price_out} per million tokens in / out`;
  return (
    <section className="settings-block">
      <h3 className="sub">AI model &amp; billing <Tag>{model?.label}</Tag></h3>
      <p className="muted small">
        Which Anthropic API key {assistant}'s requests are billed to, and how capable a model answers. The keys
        themselves live in the .env on the server (ANTHROPIC_API_KEY and ANTHROPIC_API_KEY_2); only their names show here.
      </p>
      {ok && <div className="notice" role="status">{ok}</div>}
      <ErrorNote error={err} />
      <div className="form-grid">
        <label className="field wide"><span>Billed to</span>
          <select value={cur.key} disabled={busy} onChange={(e) => set({ key: e.target.value })}>
            {data.keys.map((k) => (
              <option key={k.id} value={k.id} disabled={!k.configured}>{k.label} ({k.hint})</option>
            ))}
          </select>
        </label>
        <label className="field wide"><span>Model for chats, Telegram and the helper</span>
          <select value={cur.model} disabled={busy} onChange={(e) => set({ model: e.target.value })}>
            {data.models.map((m) => <option key={m.id} value={m.id}>{m.label}: {m.tier}</option>)}
          </select>
        </label>
        {model?.thinking && (
          <label className="field wide"><span>How hard it thinks</span>
            <select value={cur.effort} disabled={busy} onChange={(e) => set({ effort: e.target.value })}>
              {data.efforts.map((x) => <option key={x} value={x}>{x}: {EFFORT_HINT[x]}</option>)}
            </select>
          </label>
        )}
        <label className="field wide"><span>Model for phone calls</span>
          <select value={cur.call_model} disabled={busy} onChange={(e) => set({ call_model: e.target.value })}>
            {data.models.map((m) => <option key={m.id} value={m.id}>{m.label}{m.thinking ? " (pauses longer before answering)" : ""}</option>)}
          </select>
        </label>
      </div>
      <p className="muted tiny">
        {model ? `${model.label}: ${price(model)}.` : ""} Haiku answers fastest; the others think before replying,
        which is smarter but slower and costs more. Calls stay on Haiku unless you change it, because callers notice
        the pause. If a bigger model declines a request for safety reasons, Haiku answers that one instead. Small
        background jobs (call summaries, briefing wording, screening) always use Haiku, billed to the key above.
      </p>
    </section>
  );
}
