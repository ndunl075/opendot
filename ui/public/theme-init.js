// Same-origin, parser-blocking script prevents a wrong-theme flash under a self-only CSP.
(() => {
  let preference;
  try { preference = localStorage.getItem("opendot-theme"); } catch { /* Use OS preference. */ }
  document.documentElement.dataset.theme = preference === "light" || preference === "dark"
    ? preference : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
})();
