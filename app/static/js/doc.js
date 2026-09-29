// Collaborative doc surface: a shared markdown body with a live preview and "editing now" presence.
(function () {
  const CS = window.CS;
  if (!CS) return;
  let root = null;
  let activeDocId = null;
  let sendTimer = null;
  const editing = new Map(); // user_id -> timeout handle for the recently-edited pulse

  CS.surfaceInits = CS.surfaceInits || {};
  CS.surfaceInits.doc = function (rootEl, docId) {
    root = rootEl;
    activeDocId = docId;
    const input = root.querySelector("[data-doc-input]");
    renderPreview();
    input.addEventListener("input", onInput);
    const histBtn = root.querySelector("[data-doc-history]");
    if (histBtn) histBtn.addEventListener("click", toggleHistory);
    CS.onPresenceChange = refreshPresence;
    refreshPresence();
  };

  async function toggleHistory() {
    const panel = root && root.querySelector("[data-doc-versions-panel]");
    if (!panel) return;
    if (!panel.classList.contains("hidden")) { panel.classList.add("hidden"); return; }
    try {
      const res = await fetch(`/workspaces/${CS.workspaceId}/doc/${activeDocId}/versions`);
      if (!res.ok) throw new Error(res.status);
      panel.innerHTML = await res.text();
    } catch (e) {
      panel.innerHTML = '<div class="p-3 text-xs text-slate-500">Could not load history.</div>';
    }
    panel.classList.remove("hidden");
    wireVersions(panel);
  }

  function wireVersions(panel) {
    const close = panel.querySelector("[data-versions-close]");
    if (close) close.addEventListener("click", () => panel.classList.add("hidden"));
    panel.querySelectorAll("[data-version-restore]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const version = parseInt(btn.dataset.versionRestore, 10);
        if (Number.isNaN(version)) return;
        CS.send({ channel: "doc", type: "doc.restore", doc_id: activeDocId, version });
        panel.classList.add("hidden");
      }));
  }

  function onInput() {
    renderPreview();
    clearTimeout(sendTimer);
    sendTimer = setTimeout(() => {
      const input = root && root.querySelector("[data-doc-input]");
      if (input) CS.send({ channel: "doc", type: "doc.update", doc_id: activeDocId, content: input.value });
    }, 350);
  }

  function setVersion(v) {
    const el = root && root.querySelector("[data-doc-version]");
    if (el && v != null) el.textContent = "v" + v;
  }

  function renderPreview() {
    if (!root) return;
    const input = root.querySelector("[data-doc-input]");
    const out = root.querySelector("[data-doc-preview]");
    if (input && out) out.innerHTML = mdToHtml(input.value);
  }

  // Minimal, XSS-safe markdown: escape the source first, then introduce only our own tags.
  function mdToHtml(src) {
    const esc = (src || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const inline = (s) =>
      s
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\*([^*]+)\*/g, "<em>$1</em>")
        .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
          '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return esc
      .split(/\n{2,}/)
      .map((block) => {
        const b = block.replace(/\s+$/, "");
        if (!b.trim()) return "";
        let m;
        if ((m = b.match(/^(#{1,3})\s+(.*)$/))) {
          const n = m[1].length;
          return `<h${n}>${inline(m[2])}</h${n}>`;
        }
        if (/^\s*[-*]\s+/.test(b)) {
          const items = b
            .split(/\n/)
            .filter((li) => li.trim())
            .map((li) => `<li>${inline(li.replace(/^\s*[-*]\s+/, ""))}</li>`)
            .join("");
          return `<ul>${items}</ul>`;
        }
        return `<p>${inline(b).replace(/\n/g, "<br>")}</p>`;
      })
      .join("");
  }

  function refreshPresence() {
    if (!root || !root.isConnected) return;
    const box = root.querySelector("[data-doc-presence]");
    if (!box) return;
    const here = [];
    CS.presence.forEach((m) => { if (m.surface === "doc:" + activeDocId) here.push(m); });
    box.innerHTML = "";
    if (!here.length) {
      const hint = document.createElement("span");
      hint.className = "text-xs text-slate-600";
      hint.textContent = "just you";
      box.appendChild(hint);
      return;
    }
    here.forEach((m) => {
      const s = document.createElement("span");
      s.className =
        "grid h-6 w-6 place-items-center rounded-full text-[10px] font-semibold ring-2 ring-slate-900";
      s.style.background = m.color;
      const isEditing = editing.has(m.user_id);
      s.title = m.display_name + (isEditing ? " — editing…" : "");
      if (isEditing) s.classList.add("cs-editing");
      s.textContent = CS.initials(m.display_name);
      box.appendChild(s);
    });
  }

  function markEditing(userId) {
    clearTimeout(editing.get(userId));
    editing.set(userId, setTimeout(() => { editing.delete(userId); refreshPresence(); }, 1600));
    refreshPresence();
  }

  CS.on("doc", (m) => {
    if (!root || !root.isConnected || m.doc_id !== activeDocId) return;
    if (m.type !== "doc.updated") return;
    setVersion(m.version);
    const input = root.querySelector("[data-doc-input]");
    // Last-write-wins: apply the remote body unless this client is mid-edit
    // (our own next debounced save will win and rebroadcast anyway).
    if (input && document.activeElement !== input) {
      input.value = m.content;
      renderPreview();
    }
    if (m.user_id) markEditing(m.user_id);
  });
})();
