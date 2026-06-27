/* ============================================================
   Shared nav + UI wiring for every study page.
   - builds the sidebar (single source of truth for page order)
   - highlights the current page
   - adds copy buttons to <pre>
   - renders Mermaid AFTER DOM is ready, with a visible fallback
   - wires prev/next page footer links
   ============================================================ */
const PAGES = [
  { file: "index.html",       num: "0", icon: "🏠", title: "Overview" },
  { file: "big-picture.html", num: "1", icon: "🗺️", title: "Big Picture" },
  { file: "lifecycle.html",   num: "2", icon: "🔄", title: "Request Lifecycle" },
  { file: "verify.html",      num: "3", icon: "✅", title: "Verify Flow" },
  { file: "states.html",      num: "4", icon: "🔁", title: "OTP State Machine" },
  { file: "data-model.html",  num: "5", icon: "🗄️", title: "Data Model" },
  { file: "concepts.html",    num: "6", icon: "💡", title: "Core Concepts" },
  { file: "testing.html",     num: "7", icon: "🧪", title: "Testing" },
  { file: "observe.html",     num: "8", icon: "🔭", title: "Observe Behaviour" },
  { file: "glossary.html",    num: "9", icon: "📖", title: "Glossary" },
];

function currentFile() {
  const p = location.pathname.split("/").pop();
  return p && p.length ? p : "index.html";
}

function buildSidebar() {
  const cur = currentFile();
  const nav = document.getElementById("sidebar");
  if (!nav) return;
  let html = `<div class="brand">🔐 2FA Platform</div>
    <div class="brandsub">Month 1 · Architecture Study Guide</div>`;
  for (const p of PAGES) {
    const active = p.file === cur ? " active" : "";
    html += `<a class="navlink${active}" href="${p.file}">
      <span class="navnum">${p.num}</span><span>${p.icon}</span><span>${p.title}</span></a>`;
  }
  html += `<div class="foot">Self-contained. Design rationale lives in
    <code>docs/decisions.md</code>.</div>`;
  nav.innerHTML = html;
}

function buildPageNav() {
  const host = document.getElementById("pagenav");
  if (!host) return;
  const cur = currentFile();
  const i = PAGES.findIndex(p => p.file === cur);
  const prev = i > 0 ? PAGES[i - 1] : null;
  const next = i < PAGES.length - 1 ? PAGES[i + 1] : null;
  host.innerHTML =
    (prev ? `<a href="${prev.file}">← ${prev.icon} ${prev.title}</a>` : `<span></span>`) +
    (next ? `<a href="${next.file}">${next.icon} ${next.title} →</a>` : `<span></span>`);
}

function addCopyButtons() {
  document.querySelectorAll("pre").forEach(pre => {
    const code = pre.querySelector("code");
    if (!code) return;
    const btn = document.createElement("button");
    btn.className = "copybtn";
    btn.textContent = "copy";
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(code.innerText);
      btn.textContent = "copied!";
      setTimeout(() => (btn.textContent = "copy"), 1200);
    });
    pre.appendChild(btn);
  });
}

async function renderMermaid() {
  const blocks = document.querySelectorAll(".mermaid");
  if (!blocks.length) return;
  if (typeof mermaid === "undefined") {
    blocks.forEach(b => {
      const fb = b.nextElementSibling;
      if (fb && fb.classList.contains("diagram-fallback")) {
        fb.style.display = "block";
        fb.textContent = "⚠ Diagram library failed to load (offline?). Graph source is in the page HTML.";
      }
    });
    return;
  }
  try {
    mermaid.initialize({
      startOnLoad: false,
      theme: "default",
      securityLevel: "loose",
      flowchart: { useMaxWidth: true, htmlLabels: true, curve: "basis" },
      sequence: { useMaxWidth: true },
      themeVariables: { fontSize: "14px" },
    });
    await mermaid.run({ querySelector: ".mermaid" });
  } catch (e) {
    console.error("Mermaid render error:", e);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  buildSidebar();
  buildPageNav();
  addCopyButtons();
  renderMermaid();
});