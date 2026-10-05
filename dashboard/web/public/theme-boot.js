/* Applies the saved skin before the page paints (no flash of the default look). Plain script,
   loaded from <head> on every page; the skins are listed in src/themes.js. */
(function () {
  var t = "alfred";
  try { t = localStorage.getItem("alfred-theme") || t; } catch (e) { /* storage blocked */ }
  if (["alfred", "batcave", "matrix", "alfred"].indexOf(t) < 0) t = "alfred";
  document.documentElement.setAttribute("data-theme", t);
  var icon = document.querySelector('link[rel="icon"]');      // loaded after the <link>, so it exists
  if (icon) icon.setAttribute("href", "/favicon-" + t + ".svg");
})();
