import { createContext, useContext, useEffect } from "react";
import { useStoredState } from "./hooks.js";

import { DEFAULT_THEME, THEME_KEY, THEMES, normalizeTheme, setFavicon } from "./themes.js";

export { DEFAULT_THEME, THEMES, normalizeTheme };

export const ThemeContext = createContext(DEFAULT_THEME);
export const useThemeName = () => useContext(ThemeContext);

export function useTheme() {
  const [stored, setTheme] = useStoredState(THEME_KEY, DEFAULT_THEME);
  const theme = normalizeTheme(stored);
  useEffect(() => { document.documentElement.dataset.theme = theme; setFavicon(theme); }, [theme]);
  return [theme, setTheme];
}
