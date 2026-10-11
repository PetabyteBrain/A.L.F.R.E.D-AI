// The site's skins, shared by the dashboard (theme.js) and the public pages (home/home.js).
// Keep in sync with public/theme-boot.js.
export const THEMES = {
  stark: { label: "Stark", wordmark: "A.L.F.R.E.D.", subtitle: "A Loyal Family Retainer, Exceedingly Discreet" },
  batcave: { label: "Batcave", wordmark: "ALFRED", subtitle: "Wayne Manor · Batcave terminal" },
  matrix: { label: "Matrix", wordmark: "ALFRED", subtitle: "Green-screen terminal · Construct" },
  alfred: { label: "Alfred", wordmark: "Alfred", subtitle: "Your butler · at your service" },
};
export const DEFAULT_THEME = "stark";
export const THEME_KEY = "alfred-theme";
export const normalizeTheme = (t) => (THEMES[t] ? t : DEFAULT_THEME); // unknown or retired ids -> Stark

// Browser-tab icon per skin (public/favicon-<skin>.svg; favicon.svg = Stark for pages without JS).
// A fresh <link> with a versioned URL (meta icon-version, set by the server): browsers often ignore a
// changed href on the existing element, and may hold on to an old copy of the same URL.
export function setFavicon(theme) {
  const link = document.querySelector('link[rel="icon"]');
  if (!link) return;
  const ver = document.querySelector('meta[name="icon-version"]')?.getAttribute("content");
  const href = `/favicon-${normalizeTheme(theme)}.svg${ver ? `?v=${ver}` : ""}`;
  if (link.getAttribute("href") === href) return;
  const fresh = link.cloneNode(false);
  fresh.setAttribute("href", href);
  link.replaceWith(fresh);
}
