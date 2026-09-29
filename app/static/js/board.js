// Kanban board surface: drag-drop, create, rich card detail, and live updates.
(function () {
  const CS = window.CS;
  if (!CS) return;
  let activeBoardId = null;
  let root = null;

  const modal = document.getElementById("card-modal");
  const modalBody = document.getElementById("card-modal-body");
  let openCardId = null;
  let cardState = null;

  CS.surfaceInits = CS.surfaceInits || {};
  CS.surfaceInits.board = function (rootEl, boardId) {
    root = rootEl;
    activeBoardId = boardId;
    root.querySelectorAll("[data-list]").forEach(wireColumn);
    root.querySelectorAll("[data-card]").forEach(wireCard);
    const addList = root.querySelector("[data-add-list]");
    if (addList) addList.addEventListener("submit", onAddList);
  };
  function wireColumn(col) {
    const cards = col.querySelector("[data-cards]");
    new Sortable(cards, {
      group: "cards",
      animation: 150,
      ghostClass: "cs-ghost",
      dragClass: "cs-drag",
      onEnd: (evt) => {
        const node = evt.item;
        const listEl = evt.to.closest("[data-list]");
        const pos = computePosition(evt.to, node);
        node.dataset.position = pos;
        CS.send({
          channel: "board", type: "card.move", board_id: activeBoardId,
          card_id: node.dataset.card, list_id: listEl.dataset.list, position: pos,
        });
        updateCounts();
      },
    });
    const addCard = col.querySelector("[data-add-card]");
    if (addCard) addCard.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = e.currentTarget.querySelector("input");
      const title = input.value.trim();
      if (!title) return;
      CS.send({
        channel: "board", type: "card.create", board_id: activeBoardId,
        list_id: col.dataset.list, title, position: lastPosition(cards) + 1000,
      });
      input.value = "";
    });
  }

  // Open the detail panel on a genuine click (one the drag threshold didn't consume).
  function wireCard(node) {
    let sx = 0, sy = 0, moved = false;
    node.addEventListener("pointerdown", (e) => { sx = e.clientX; sy = e.clientY; moved = false; });
    node.addEventListener("pointermove", (e) => {
      if (Math.abs(e.clientX - sx) > 5 || Math.abs(e.clientY - sy) > 5) moved = true;
    });
    node.addEventListener("click", () => { if (!moved) openCard(node.dataset.card, activeBoardId); });
  }

  function onAddList(e) {
    e.preventDefault();
    const input = e.currentTarget.querySelector("input");
    const title = input.value.trim();
    if (!title) return;
    const cols = root ? root.querySelectorAll("[data-list]").length : 0;
    CS.send({
      channel: "board", type: "list.create", board_id: activeBoardId,
      title, position: (cols + 1) * 1000,
    });
    input.value = "";
  }
  function computePosition(container, node) {
    const sibs = [...container.querySelectorAll("[data-card]")];
    const idx = sibs.indexOf(node);
    const prev = idx > 0 ? parseFloat(sibs[idx - 1].dataset.position) : null;
    const next = idx < sibs.length - 1 ? parseFloat(sibs[idx + 1].dataset.position) : null;
    if (prev == null && next == null) return 1000;
    if (prev == null) return next - 1000;
    if (next == null) return prev + 1000;
    return (prev + next) / 2;
  }

  function lastPosition(container) {
    const sibs = [...container.querySelectorAll("[data-card]")];
    if (!sibs.length) return 0;
    return Math.max(...sibs.map((s) => parseFloat(s.dataset.position) || 0));
  }

  function insertByPosition(container, node) {
    const pos = parseFloat(node.dataset.position) || 0;
    let before = null;
    for (const s of container.querySelectorAll("[data-card]")) {
      if (s !== node && (parseFloat(s.dataset.position) || 0) > pos) { before = s; break; }
    }
    container.insertBefore(node, before);
  }

  function updateCounts() {
    if (!root) return;
    root.querySelectorAll("[data-list]").forEach((col) => {
      const c = col.querySelector("[data-cards]");
      const badge = col.querySelector("[data-count]");
      if (c && badge) badge.textContent = c.querySelectorAll("[data-card]").length;
    });
  }
  // ---- card detail panel ----
  async function openCard(cardId, boardId) {
    if (!cardId || !modal || !modalBody) return;
    try {
      const res = await fetch(`/workspaces/${CS.workspaceId}/card/${cardId}`);
      if (!res.ok) throw new Error(res.status);
      modalBody.innerHTML = await res.text();
    } catch (err) {
      return;
    }
    openCardId = cardId;
    modal.classList.remove("hidden");
    wireDetail(modalBody.firstElementChild, boardId);
  }
  CS.openCard = openCard;

  function closeModal() {
    if (!modal) return;
    modal.classList.add("hidden");
    modalBody.innerHTML = "";
    openCardId = null;
    cardState = null;
  }

  if (modal) {
    modal.querySelectorAll("[data-modal-close]").forEach((el) =>
      el.addEventListener("click", closeModal));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
    });
  }

  function sendPatch(patch) {
    if (openCardId) CS.send({ channel: "board", type: "card.update", card_id: openCardId, patch });
  }
  function wireDetail(el, boardId) {
    if (!el) return;
    try {
      cardState = JSON.parse(el.querySelector("[data-card-state]").textContent);
    } catch (e) {
      cardState = { labels: [], checklist: [] };
    }
    cardState.labels = cardState.labels || [];
    cardState.checklist = cardState.checklist || [];

    const title = el.querySelector("[data-cd-title]");
    if (title) title.addEventListener("change", () => {
      const v = title.value.trim();
      if (v) sendPatch({ title: v });
    });
    const assignee = el.querySelector("[data-cd-assignee]");
    if (assignee) assignee.addEventListener("change", () =>
      sendPatch({ assignee_id: assignee.value || null }));
    const due = el.querySelector("[data-cd-due]");
    if (due) due.addEventListener("change", () => sendPatch({ due_date: due.value || null }));
    const desc = el.querySelector("[data-cd-desc]");
    if (desc) desc.addEventListener("change", () => sendPatch({ description: desc.value }));

    const labelForm = el.querySelector("[data-cd-label-form]");
    if (labelForm) labelForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const t = el.querySelector("[data-cd-label-text]");
      const c = el.querySelector("[data-cd-label-color]");
      const text = t.value.trim();
      if (!text) return;
      cardState.labels.push({ text, color: (c && c.value) || "#6366f1" });
      t.value = "";
      renderLabels(el);
      sendPatch({ labels: cardState.labels });
    });

    const checkForm = el.querySelector("[data-cd-check-form]");
    if (checkForm) checkForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const t = el.querySelector("[data-cd-check-text]");
      const text = t.value.trim();
      if (!text) return;
      cardState.checklist.push({ id: rid(), text, done: false });
      t.value = "";
      renderChecklist(el);
      sendPatch({ checklist: cardState.checklist });
    });

    wireComment(el);
    renderLabels(el);
    renderChecklist(el);
  }
  function renderLabels(el) {
    const box = el.querySelector("[data-cd-labels]");
    if (!box) return;
    box.innerHTML = "";
    cardState.labels.forEach((lab, i) => {
      const chip = document.createElement("span");
      chip.className =
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-white";
      chip.style.background = lab.color || "#6366f1";
      chip.textContent = lab.text;
      const x = document.createElement("button");
      x.type = "button";
      x.textContent = "✕";
      x.className = "leading-none opacity-80 hover:opacity-100";
      x.addEventListener("click", () => {
        cardState.labels.splice(i, 1);
        renderLabels(el);
        sendPatch({ labels: cardState.labels });
      });
      chip.appendChild(x);
      box.appendChild(chip);
    });
  }

  function renderChecklist(el) {
    const box = el.querySelector("[data-cd-checklist]");
    if (!box) return;
    box.innerHTML = "";
    const done = cardState.checklist.filter((i) => i.done).length;
    const prog = el.querySelector("[data-cd-progress]");
    if (prog) prog.textContent = cardState.checklist.length ? `${done}/${cardState.checklist.length}` : "";
    cardState.checklist.forEach((item, i) => {
      const row = document.createElement("label");
      row.className = "flex items-center gap-2 text-sm text-slate-200";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!item.done;
      cb.className = "cs-check";
      cb.addEventListener("change", () => {
        cardState.checklist[i].done = cb.checked;
        renderChecklist(el);
        sendPatch({ checklist: cardState.checklist });
      });
      const span = document.createElement("span");
      span.className = "flex-1" + (item.done ? " text-slate-500 line-through" : "");
      span.textContent = item.text;
      const x = document.createElement("button");
      x.type = "button";
      x.textContent = "✕";
      x.className = "flex-none text-slate-500 hover:text-slate-300";
      x.addEventListener("click", () => {
        cardState.checklist.splice(i, 1);
        renderChecklist(el);
        sendPatch({ checklist: cardState.checklist });
      });
      row.append(cb, span, x);
      box.appendChild(row);
    });
  }
  // Comment box with lightweight @username autocomplete over the workspace roster.
  function wireComment(el) {
    const form = el.querySelector("[data-cd-comment-form]");
    const input = el.querySelector("[data-cd-comment-input]");
    const box = el.querySelector("[data-cd-mention-box]");
    if (!form || !input) return;
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const body = input.value.trim();
      if (!body || !openCardId) return;
      CS.send({ channel: "board", type: "comment.add", card_id: openCardId, body });
      input.value = "";
      hideMentions(box);
    });
    input.addEventListener("input", () => updateMentions(input, box));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
      if (e.key === "Escape") hideMentions(box);
    });
    input.addEventListener("blur", () => setTimeout(() => hideMentions(box), 150));
  }

  function updateMentions(input, box) {
    if (!box) return;
    const upto = input.value.slice(0, input.selectionStart);
    const m = upto.match(/@([a-z0-9_]*)$/i);
    if (!m) { hideMentions(box); return; }
    const q = m[1].toLowerCase();
    const matches = (CS.members || [])
      .filter((u) => u.username && u.username.toLowerCase().startsWith(q))
      .slice(0, 6);
    if (!matches.length) { hideMentions(box); return; }
    box.innerHTML = "";
    matches.forEach((u) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "cs-mention-item";
      b.textContent = "@" + u.username;
      const hint = document.createElement("span");
      hint.className = "cs-mention-name";
      hint.textContent = u.display_name;
      b.appendChild(hint);
      b.addEventListener("mousedown", (e) => {
        e.preventDefault();
        completeMention(input, u.username);
        hideMentions(box);
      });
      box.appendChild(b);
    });
    box.classList.remove("hidden");
  }

  function completeMention(input, username) {
    const start = input.selectionStart;
    const before = input.value.slice(0, start).replace(/@([a-z0-9_]*)$/i, "@" + username + " ");
    const after = input.value.slice(start);
    input.value = before + after;
    input.focus();
    input.setSelectionRange(before.length, before.length);
  }

  function hideMentions(box) {
    if (box) { box.classList.add("hidden"); box.innerHTML = ""; }
  }

  function rid() {
    return Math.random().toString(36).slice(2, 10);
  }
  function appendComment(html) {
    const box = modalBody && modalBody.querySelector("[data-cd-comments]");
    if (!box) return;
    const tmp = document.createElement("div");
    tmp.innerHTML = (html || "").trim();
    const node = tmp.firstElementChild;
    if (!node) return;
    const id = node.dataset.comment;
    if (id && box.querySelector(`[data-comment="${id}"]`)) return;
    box.appendChild(node);
    box.scrollTop = box.scrollHeight;
  }

  // Reconcile the open panel with the server's authoritative card, leaving focused fields alone.
  function syncModal(card) {
    if (!cardState) return;
    cardState.labels = card.labels || [];
    cardState.checklist = card.checklist || [];
    cardState.assignee_id = card.assignee_id;
    cardState.due_date = card.due_date;
    const el = modalBody && modalBody.firstElementChild;
    if (!el) return;
    const ae = document.activeElement;
    const title = el.querySelector("[data-cd-title]");
    if (title && ae !== title) title.value = card.title;
    const assignee = el.querySelector("[data-cd-assignee]");
    if (assignee && ae !== assignee) assignee.value = card.assignee_id || "";
    const due = el.querySelector("[data-cd-due]");
    if (due && ae !== due) due.value = card.due_date || "";
    const desc = el.querySelector("[data-cd-desc]");
    if (desc && ae !== desc && card.description != null) desc.value = card.description;
    renderLabels(el);
    renderChecklist(el);
  }

  CS.on("board", (m) => {
    // The open panel can be updated even when its board isn't the visible surface.
    if (m.type === "comment.added" && openCardId && m.card_id === openCardId) appendComment(m.html);
    if (m.type === "card.updated" && openCardId === m.card_id && m.card) syncModal(m.card);

    if (!root || !root.isConnected || m.board_id !== activeBoardId) return;

    if (m.type === "card.created") {
      const tmp = document.createElement("div");
      tmp.innerHTML = (m.html || "").trim();
      const node = tmp.firstElementChild;
      if (!node) return;
      node.dataset.position = m.position;
      const col = root.querySelector(`[data-list="${m.list_id}"] [data-cards]`);
      if (col && !root.querySelector(`[data-card="${node.dataset.card}"]`)) {
        insertByPosition(col, node);
        wireCard(node);
        updateCounts();
      }
    } else if (m.type === "card.moved") {
      const node = root.querySelector(`[data-card="${m.card_id}"]`);
      const col = root.querySelector(`[data-list="${m.list_id}"] [data-cards]`);
      if (node && col) {
        node.dataset.position = m.position;
        insertByPosition(col, node);
        updateCounts();
      }
    } else if (m.type === "card.updated") {
      const existing = root.querySelector(`[data-card="${m.card_id}"]`);
      if (existing && m.html) {
        const tmp = document.createElement("div");
        tmp.innerHTML = m.html.trim();
        const node = tmp.firstElementChild;
        if (node) {
          node.dataset.position = existing.dataset.position;
          existing.replaceWith(node);
          wireCard(node);
        }
      }
    } else if (m.type === "list.created") {
      const tmp = document.createElement("div");
      tmp.innerHTML = (m.html || "").trim();
      const node = tmp.firstElementChild;
      const addForm = root.querySelector("[data-add-list]");
      if (node && addForm && !root.querySelector(`[data-list="${node.dataset.list}"]`)) {
        root.insertBefore(node, addForm);
        wireColumn(node);
      }
    }
  });
})();
