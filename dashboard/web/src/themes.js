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
// Browsers fetch a changed icon in the background and may keep showing the previous one, so after the
// first switch every skin's icon is embedded as a data: URL (nothing left to fetch, the tab updates at
// once). URLs carry the server's icon version (meta icon-version) so no stale copy is ever used.
const iconData = new Map();          // versioned URL -> data: URL
let wantedIcon = "";

function iconUrl(theme) {
  const ver = document.querySelector('meta[name="icon-version"]')?.getAttribute("content");
  return `/favicon-${normalizeTheme(theme)}.svg${ver ? `?v=${ver}` : ""}`;
}

function showIcon(href) {
  const link = document.querySelector('link[rel="icon"]');
  if (!link || link.getAttribute("href") === href) return;
  const fresh = link.cloneNode(false);           // a new element: some browsers ignore a changed href
  fresh.setAttribute("href", href);
  link.replaceWith(fresh);
}

function loadIcon(url) {
  if (iconData.has(url)) return Promise.resolve(iconData.get(url));
  return fetch(url).then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
    .then((svg) => {
      const data = `data:image/svg+xml,${encodeURIComponent(svg)}`;
      iconData.set(url, data);
      return data;
    });
}

export function setFavicon(theme) {
  const url = iconUrl(theme);
  wantedIcon = url;
  if (iconData.has(url)) { showIcon(iconData.get(url)); return; }
  showIcon(url);                                  // right away; replaced by the embedded copy below
  loadIcon(url).then((data) => { if (wantedIcon === url) showIcon(data); }).catch(() => { /* keep the URL */ });
  for (const id of Object.keys(THEMES)) loadIcon(iconUrl(id)).catch(() => {});   // the next switch is instant
}
