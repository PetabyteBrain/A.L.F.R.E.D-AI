import { useMemo, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote } from "../components/common.jsx";
import { Panel } from "../components/hud.jsx";
import BudgetsCard from "../components/BudgetsCard.jsx";

const PERIODS = [7, 30, 90];
const n = (v) => Number(v) || 0;
const compact = (v) => {
  v = n(v);
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(v >= 1e4 ? 0 : 1)}k`;
  return String(Math.round(v));
};
const usd = (v, digits) => {
  v = n(v);
  const d = digits ?? (v >= 10 ? 2 : v >= 0.1 ? 3 : 4);
  return `$${v.toFixed(d)}`;
};
const minutes = (sec) => `${(n(sec) / 60).toFixed(n(sec) >= 600 ? 0 : 1)} min`;

function Stat({ label, value, sub }) {
  return (
    <div className="stat-tile">
      <div className="st-label">{label}</div>
      <div className="st-value">{value}</div>
      {sub && <div className="st-sub">{sub}</div>}
    </div>
  );
}

/* Daily Claude cost: one series, thin rounded bars, 2px gaps, per-bar hover tooltip. */
function DailyCost({ days, series }) {
  const [hover, setHover] = useState(null);
  const data = useMemo(() => {
    const byDay = Object.fromEntries(series.map((d) => [d.day, d]));
    const out = [];
    const end = new Date();
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(end.getTime() - i * 86400000).toISOString().slice(0, 10);
      out.push(byDay[d] ? { ...byDay[d], day: d } : { day: d, cost: 0, input: 0, output: 0 });
    }
    return out;
  }, [days, series]);

  const W = 720, H = 190, padL = 46, padB = 22, padT = 10;
  const max = Math.max(...data.map((d) => n(d.cost)), 0.0001);
  const niceMax = (() => { const p = 10 ** Math.floor(Math.log10(max)); return Math.ceil(max / p) * p; })();
  const slot = (W - padL) / data.length;
  const bw = Math.max(2, Math.min(18, slot - 2));
  const y = (v) => padT + (H - padT - padB) * (1 - n(v) / niceMax);
  const ticks = [0, niceMax / 2, niceMax];
  const labelEvery = Math.ceil(data.length / 8);

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="bar-chart" role="img"
           aria-label={`Daily Claude cost over the last ${days} days`} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={padL} x2={W} y1={y(t)} y2={y(t)} />
            <text className="axis" x={padL - 6} y={y(t) + 4} textAnchor="end">{usd(t, t < 0.1 ? 3 : 2)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = padL + i * slot + (slot - bw) / 2;
          const top = y(d.cost), base = y(0), h = Math.max(0, base - top), r = Math.min(4, bw / 2, h);
          return (
            <g key={d.day} onMouseEnter={() => setHover(i)}>
              <rect className="hit" x={padL + i * slot} y={padT} width={slot} height={H - padT - padB} />
              {h > 0 && (
                <path className={`bar${hover === i ? " on" : ""}`}
                      d={`M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${base} Z`} />
              )}
              {i % labelEvery === 0 && <text className="axis" x={x + bw / 2} y={H - 6} textAnchor="middle">{d.day.slice(5)}</text>}
            </g>
          );
        })}
        <line className="baseline" x1={padL} x2={W} y1={y(0)} y2={y(0)} />
      </svg>
      {hover !== null && (
        <div className="chart-tip" style={{ left: `${((padL + hover * slot + slot / 2) / W) * 100}%` }}>
          <b>{data[hover].day}</b>
          <span>{usd(data[hover].cost)}</span>
          <span>{compact(data[hover].input)} in · {compact(data[hover].output)} out</span>
        </div>
      )}
    </div>
  );
}

export default function Usage() {
  const [days, setDays] = useState(30);
  const { data: u, error } = usePoll(() => api(`/usage?days=${days}`), 60000, [days]);

  const claude = useMemo(() => {
    const rows = u?.anthropic || [];
    const sum = (k) => rows.reduce((a, r) => a + n(r[k]), 0);
    const input = sum("input"), cacheRead = sum("cache_read"), cacheWrite = sum("cache_write");
    return { rows, input, output: sum("output"), cacheRead, cacheWrite, cost: sum("cost"), requests: sum("requests"),
      cacheShare: input + cacheRead + cacheWrite ? cacheRead / (input + cacheRead + cacheWrite) : 0 };
  }, [u]);
  const tts = u?.elevenlabs?.find((r) => r.kind === "tts");
  const stt = u?.elevenlabs?.find((r) => r.kind === "stt");
  const voiceIn = u?.voice?.find((r) => r.kind === "voice-inbound");
  const voiceOut = u?.voice?.find((r) => r.kind === "voice-outbound");
  const tw = u?.twilio_month;

  return (
    <Panel code="09" title="API usage" actions={
      <div className="seg" role="group" aria-label="Period">
        {PERIODS.map((d) => (
          <button key={d} className={d === days ? "active" : ""} aria-pressed={d === days} onClick={() => setDays(d)}>{d}d</button>
        ))}
      </div>
    }>
      <ErrorNote error={error} />
      {!u && !error && <p className="muted">Loading…</p>}
      {u && (
        <>
          <div className="stat-grid">
            <Stat label={`Est. cost · ${days}d`} value={usd(u.estimated_cost_usd, 2)}
                  sub={u.elevenlabs_priced ? "Claude + ElevenLabs" : "Claude only (ElevenLabs prices not set)"} />
            <Stat label="Claude tokens" value={`${compact(claude.input + claude.cacheRead + claude.cacheWrite)} / ${compact(claude.output)}`}
                  sub={`in / out · ${compact(claude.requests)} requests`} />
            <Stat label="Prompt cache hits" value={`${Math.round(claude.cacheShare * 100)}%`} sub={`${compact(claude.cacheRead)} tokens read from cache`} />
            <Stat label="Twilio · this month" value={tw && tw.total != null ? `${tw.total.toFixed(2)} ${tw.currency}` : "—"}
                  sub={tw?.error ? "couldn't fetch" : tw && tw.calls_minutes != null ? `${tw.calls_minutes.toFixed(0)} call min · ${tw.calls_count} calls (Twilio's figures)` : "not configured"} />
            <Stat label="Call time" value={minutes(n(voiceIn?.seconds) + n(voiceOut?.seconds))}
                  sub={`${n(voiceIn?.calls)} in · ${n(voiceOut?.calls)} out`} />
            <Stat label="Voice (TTS)" value={`${compact(tts?.units)} chars`} sub={tts?.cost ? usd(tts.cost) : "ElevenLabs"} />
            <Stat label="Speech-to-text" value={minutes(stt?.units)} sub={stt?.cost ? usd(stt.cost) : "ElevenLabs"} />
          </div>

          <BudgetsCard />

          <h3 className="sub">Claude cost per day</h3>
          {u.anthropic_daily.length === 0 ? <Empty>No Claude usage in this period.</Empty>
            : <DailyCost days={days} series={u.anthropic_daily} />}

          <div className="usage-tables">
            <section>
              <h3 className="sub">Claude by channel</h3>
              <table className="data">
                <thead><tr><th>Channel</th><th>Requests</th><th>Input</th><th>Cached</th><th>Output</th><th>Cost</th></tr></thead>
                <tbody>
                  {claude.rows.map((r) => (
                    <tr key={r.kind + r.model}><td>{r.kind}</td><td>{compact(r.requests)}</td><td>{compact(r.input)}</td>
                      <td>{compact(r.cache_read)}</td><td>{compact(r.output)}</td><td>{usd(r.cost)}</td></tr>
                  ))}
                  {!claude.rows.length && <tr><td colSpan={6} className="muted">—</td></tr>}
                </tbody>
              </table>
            </section>
            <section>
              <h3 className="sub">By person</h3>
              <table className="data">
                <thead><tr><th>Person</th><th>Tokens in</th><th>Out</th><th>Cost</th></tr></thead>
                <tbody>
                  {u.by_user.map((r) => (
                    <tr key={r.user}><td>{r.user}</td><td>{compact(r.input)}</td><td>{compact(r.output)}</td><td>{usd(r.cost)}</td></tr>
                  ))}
                  {!u.by_user.length && <tr><td colSpan={4} className="muted">—</td></tr>}
                </tbody>
              </table>
              {u.requests.length > 0 && (
                <p className="muted small">{u.requests.map((r) => `${r.provider} ${r.kind}: ${compact(r.requests)} requests`).join(" · ")}</p>
              )}
            </section>
          </div>
          <p className="muted tiny">
            Claude figures are exact (from each API response), priced at ${u.prices.claude_input_per_mtok}/M input,
            {" "}${u.prices.claude_output_per_mtok}/M output, ${u.prices.claude_cache_read_per_mtok}/M cache reads.
            {" "}ElevenLabs is metered by characters and seconds; set PRICE_ELEVENLABS_* in .env for cost estimates.
            {" "}Telegram and Google Calendar have no usage charges.
          </p>
        </>
      )}
    </Panel>
  );
}
