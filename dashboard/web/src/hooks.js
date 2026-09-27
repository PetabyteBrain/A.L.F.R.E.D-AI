import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api.js";

// Load data now and optionally every `intervalMs`; pauses while the tab is hidden.
export function usePoll(loader, intervalMs = 0, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const reload = useCallback(async () => {
    try {
      setData(await loaderRef.current());
      setError("");
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError(e.message || "Request failed");
    }
  }, []);

  useEffect(() => {
    reload();
    if (!intervalMs) return undefined;
    const id = setInterval(() => { if (!document.hidden) reload(); }, intervalMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, intervalMs, ...deps]);

  return { data, error, reload };
}

export function useStoredState(key, initial) {
  const [value, setValue] = useState(() => {
    try { return localStorage.getItem(key) ?? initial; } catch { return initial; }
  });
  const set = useCallback((v) => {
    setValue(v);
    try { localStorage.setItem(key, v); } catch { /* storage unavailable */ }
  }, [key]);
  return [value, set];
}
