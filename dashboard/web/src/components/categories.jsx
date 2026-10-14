import { useState } from "react";
import { api } from "../api.js";

// Agenda categories (orchestrator/agenda.py). Colours are set per category in styles.css (.cat-*).
export const CATEGORIES = ["meeting", "reminder", "task", "call", "work", "personal", "other"];
export const KIND_ICON = { event: "📅", reminder: "⏰", task: "📋", call: "☎", work: "🗂" };

export function CatDot({ category }) {
  return <span className={`cat-dot cat-${category || "other"}`} aria-hidden="true" />;
}

/* A small select that changes an item's category right away (list or calendar). */
export function CategorySelect({ kind, id, value, onChanged, disabled }) {
  const [err, setErr] = useState("");
  const [cur, setCur] = useState(value || "other");
  async function change(e) {
    const v = e.target.value;
    setErr("");
    setCur(v);
    try {
      await api("/agenda", { method: "PATCH", body: { kind, id: String(id), category: v } });
      onChanged?.(v);
    } catch (x) { setErr(x.message); setCur(value); }
  }
  return (
    <span className="cat-select">
      <CatDot category={cur} />
      <select value={cur} onChange={change} disabled={disabled} aria-label="Category" title={err || "Category"}>
        {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    </span>
  );
}
