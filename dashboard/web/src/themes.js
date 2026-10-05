// The site's skins, shared by the dashboard (theme.js) and the public pages (home/home.js).
// "alfred" keeps its id so saved preferences still work. Keep in sync with public/theme-boot.js.
export const THEMES = {
  alfred: { label: "Stark", wordmark: "A.L.F.R.E.D.", subtitle: "Personal butler system · Stark HUD" },
  batcave: { label: "Batcave", wordmark: "ALFRED", subtitle: "Wayne Manor · Batcave terminal" },
  matrix: { label: "Matrix", wordmark: "ALFRED", subtitle: "Green-screen terminal · Construct" },
  alfred: { label: "Alfred", wordmark: "Alfred", subtitle: "Your butler · at your service" },
};
export const DEFAULT_THEME = "alfred";
export const THEME_KEY = "alfred-theme";
export const normalizeTheme = (t) => (THEMES[t] ? t : DEFAULT_THEME); // e.g. old "friday" -> alfred
