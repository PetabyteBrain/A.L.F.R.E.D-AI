import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote } from "../components/common.jsx";

export default function Activity() {
  const { data, error } = usePoll(() => api("/activity?limit=150"), 5000);
  return (
    <section className="panel">
      <h2>Activity</h2>
      <ErrorNote error={error} />
      {data && data.length === 0 && <Empty>No activity yet.</Empty>}
      <div className="feed">
        {(data || []).map((a) => (
          <div className="e" key={a.id}>
            <span className="ts">{fmtTime(a.ts)}</span>
            <span className={`kind ${a.kind === "access" ? "access" : ""}`}>{a.kind}</span>
            <span>{a.user && <><b>{a.user}</b> · </>}{a.summary}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
