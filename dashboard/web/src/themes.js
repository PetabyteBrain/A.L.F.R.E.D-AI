// The site's skins, shared by the dashboard (theme.js) and the public pages (home/home.js).
// "alfred" keeps its id so saved preferences still work. Keep in sync with public/theme-boot.js.
export const THEMES = {
  alfred: { label: "Stark", wordmark: "A.L.F.R.E.D.", subtitle: "A Loyal Family Retainer, Exceedingly Discreet" },
  batcave: { label: "Batcave", wordmark: "ALFRED", subtitle: "Wayne Manor · Batcave terminal" },
  matrix: { label: "Matrix", wordmark: "ALFRED", subtitle: "Green-screen terminal · Construct" },
  alfred: { label: "Alfred", wordmark: "Alfred", subtitle: "Your butler · at your service" },
};
export const DEFAULT_THEME = "alfred";
export const THEME_KEY = "alfred-theme";
export const normalizeTheme = (t) => (THEMES[t] ? t : DEFAULT_THEME); // e.g. old "friday" -> alfred

// Browser-tab icon per skin (public/favicon-<skin>.svg; favicon.svg = Stark for pages without JS).
export function setFavicon(theme) {
  const link = document.querySelector('link[rel="icon"]');
  if (link) link.setAttribute("href", `/favicon-${normalizeTheme(theme)}.svg`);
}
