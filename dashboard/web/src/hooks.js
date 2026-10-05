import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ApiError } from "./api.js";

// False while a page is kept in the background (App keeps Tasks and Files mounted when you switch tabs).
export const PageActive = createContext(true);
export const usePageActive = () => useContext(PageActive);

// Load data now and optionally every `intervalMs`; pauses while the browser tab or the page is hidden
// and refreshes when the page comes back.
export function usePoll(loader, intervalMs = 0, deps = []) {
  const active = useContext(PageActive);
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
    if (!active) return undefined;
    reload();
    if (!intervalMs) return undefined;
    const id = setInterval(() => { if (!document.hidden) reload(); }, intervalMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, intervalMs, active, ...deps]);

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
