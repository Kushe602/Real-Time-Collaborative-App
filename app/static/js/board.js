// Kanban board surface: drag-drop + create, synced live over the workspace socket.
(function () {
  const CS = window.CS;
  if (!CS) return;
  let activeBoardId = null;
  let root = null;

  CS.surfaceInits = CS.surfaceInits || {};
  CS.surfaceInits.board = function (rootEl, boardId) {
    root = rootEl;
    activeBoardId = boardId;
    root.querySelectorAll("[data-list]").forEach(wireColumn);
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
    col.querySelector("[data-add-card]").addEventListener("submit", (e) => {
      e.preventDefault();
      const input = e.currentTarget.querySelector("input");
      const title = input.value.trim();
      if (!title) return;
      const pos = lastPosition(cards) + 1000;
      CS.send({
        channel: "board", type: "card.create", board_id: activeBoardId,
        list_id: col.dataset.list, title, position: pos,
      });
      input.value = "";
    });
  }

  function onAddList(e) {
    e.preventDefault();
    const input = e.currentTarget.querySelector("input");
    const title = input.value.trim();
    if (!title) return;
    const cols = root.querySelectorAll("[data-list]");
    const last = cols.length ? parseFloat(cols[cols.length - 1].dataset.position || 0) : 0;
    CS.send({ channel: "board", type: "list.create", board_id: activeBoardId, title, position: last + 1000 });
    input.value = "";
  }

  function siblings(cardsEl, exclude) {
    return [...cardsEl.querySelectorAll(":scope > [data-card]")].filter((n) => n !== exclude);
  }
  function computePosition(cardsEl, node) {
    const sibs = [...cardsEl.querySelectorAll(":scope > [data-card]")];
    const i = sibs.indexOf(node);
    const prev = sibs[i - 1] ? parseFloat(sibs[i - 1].dataset.position) : null;
    const next = sibs[i + 1] ? parseFloat(sibs[i + 1].dataset.position) : null;
    if (prev != null && next != null) return (prev + next) / 2;
    if (prev != null) return prev + 1000;
    if (next != null) return next / 2;
    return 1000;
  }
  function lastPosition(cardsEl) {
    const sibs = siblings(cardsEl, null);
    return sibs.length ? parseFloat(sibs[sibs.length - 1].dataset.position) : 0;
  }
  function insertByPosition(cardsEl, node, position) {
    const before = siblings(cardsEl, node).find((n) => parseFloat(n.dataset.position) > position);
    if (before) cardsEl.insertBefore(node, before);
    else cardsEl.appendChild(node);
  }
  function updateCounts() {
    if (!root) return;
    root.querySelectorAll("[data-list]").forEach((col) => {
      const c = col.querySelector("[data-count]");
      if (c) c.textContent = col.querySelectorAll("[data-card]").length;
    });
  }

  CS.on("board", (m) => {
    if (!root || m.board_id !== activeBoardId) return;
    if (m.type === "card.created") {
      const cards = root.querySelector(`[data-list="${m.list_id}"] [data-cards]`);
      if (cards) {
        const tmp = document.createElement("div");
        tmp.innerHTML = m.html.trim();
        const node = tmp.firstElementChild;
        if (!root.querySelector(`[data-card="${node.dataset.card}"]`)) {
          insertByPosition(cards, node, m.position);
          updateCounts();
        }
      }
    } else if (m.type === "card.moved") {
      const node = root.querySelector(`[data-card="${m.card_id}"]`);
      const cards = root.querySelector(`[data-list="${m.list_id}"] [data-cards]`);
      if (node && cards) {
        node.dataset.position = m.position;
        insertByPosition(cards, node, m.position);
        updateCounts();
      }
    } else if (m.type === "list.created") {
      const addList = root.querySelector("[data-add-list]");
      const tmp = document.createElement("div");
      tmp.innerHTML = m.html.trim();
      const col = tmp.firstElementChild;
      if (!root.querySelector(`[data-list="${col.dataset.list}"]`)) {
        root.insertBefore(col, addList);
        wireColumn(col);
      }
    }
  });
})();
