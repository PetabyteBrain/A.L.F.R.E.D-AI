/* Applies the saved skin before the page paints (no flash of the default look). Plain script,
   loaded from <head> on every page; the skins are listed in src/themes.js. */
(function () {
  var t = "stark";
  try { t = localStorage.getItem("alfred-theme") || t; } catch (e) { /* storage blocked */ }
  if (["stark", "batcave", "matrix", "alfred"].indexOf(t) < 0) t = "stark";
  document.documentElement.setAttribute("data-theme", t);
  // Tab icon: a fresh <link> with a versioned URL (browsers ignore a changed href or keep an old copy).
  var icon = document.querySelector('link[rel="icon"]');      // loaded after the <link>, so it exists
  var ver = document.querySelector('meta[name="icon-version"]');
  if (icon) {
    var fresh = icon.cloneNode(false);
    fresh.setAttribute("href", "/favicon-" + t + ".svg" + (ver ? "?v=" + ver.getAttribute("content") : ""));
    icon.parentNode.replaceChild(fresh, icon);
  }
})();
