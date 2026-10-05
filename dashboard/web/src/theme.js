import { createContext, useContext, useEffect } from "react";
import { useStoredState } from "./hooks.js";

// Skins for the same dashboard. "alfred" keeps its id so saved preferences still work.
export const THEMES = {
  alfred: { label: "Stark", wordmark: "A.L.F.R.E.D.", subtitle: "Personal butler system · Stark HUD" },
  batcave: { label: "Batcave", wordmark: "ALFRED", subtitle: "Wayne Manor · Batcave terminal" },
  matrix: { label: "Matrix", wordmark: "ALFRED", subtitle: "Green-screen terminal · Construct" },
};
export const DEFAULT_THEME = "alfred";
export const normalizeTheme = (t) => (THEMES[t] ? t : DEFAULT_THEME); // e.g. old "friday" -> alfred

export const ThemeContext = createContext(DEFAULT_THEME);
export const useThemeName = () => useContext(ThemeContext);

export function useTheme() {
  const [stored, setTheme] = useStoredState("alfred-theme", DEFAULT_THEME);
  const theme = normalizeTheme(stored);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  return [theme, setTheme];
}
