// In-app notifications: live bell, unread badge, dropdown feed, and mark-as-read.
(function () {
  const CS = window.CS;
  if (!CS) return;

  const bell = document.getElementById("notif-bell");
  const panel = document.getElementById("notif-panel");
  const badge = document.getElementById("notif-badge");
  const list = document.getElementById("notif-list");
  const empty = document.getElementById("notif-empty");
  const readAll = document.getElementById("notif-readall");
  if (!bell || !panel || !list) return;

  let unread = CS.unreadCount || 0;
  renderBadge();

  function renderBadge() {
    if (!badge) return;
    badge.textContent = unread > 99 ? "99+" : unread;
    badge.classList.toggle("hidden", unread <= 0);
  }
  function setUnread(n) {
    unread = Math.max(0, n);
    renderBadge();
  }
  function updateEmpty() {
    if (empty) empty.classList.toggle("hidden", list.children.length > 0);
  }
  function togglePanel(show) {
    const willShow = show === undefined ? panel.classList.contains("hidden") : show;
    panel.classList.toggle("hidden", !willShow);
  }

  bell.addEventListener("click", (e) => { e.stopPropagation(); togglePanel(); });
  document.addEventListener("click", (e) => {
    if (panel.classList.contains("hidden")) return;
    if (!panel.contains(e.target) && !bell.contains(e.target)) togglePanel(false);
  });

  function markRead(li) {
    if (!li || !li.classList.contains("cs-note-unread")) return;
    li.classList.remove("cs-note-unread");
    setUnread(unread - 1);
    fetch(`/workspaces/${CS.workspaceId}/notifications/${li.dataset.note}/read`, {
      method: "POST",
    }).catch(() => {});
  }

  if (readAll) readAll.addEventListener("click", (e) => {
    e.stopPropagation();
    list.querySelectorAll(".cs-note-unread").forEach((li) => li.classList.remove("cs-note-unread"));
    setUnread(0);
    fetch(`/workspaces/${CS.workspaceId}/notifications/read-all`, { method: "POST" }).catch(() => {});
  });

  function openTarget(li) {
    const board = li.dataset.board;
    const card = li.dataset.card;
    if (!board || !card) return;
    togglePanel(false);
    if (CS.currentSurface === "board:" + board && CS.openCard) {
      CS.openCard(card, board);
    } else if (CS.openSurface && CS.openCard) {
      CS.openSurface("board", board);
      setTimeout(() => CS.openCard(card, board), 400);
    }
  }

  function wireItem(li) {
    const dot = li.querySelector("[data-note-read]");
    if (dot) dot.addEventListener("click", (e) => { e.stopPropagation(); markRead(li); });
    li.addEventListener("click", () => { markRead(li); openTarget(li); });
  }

  list.querySelectorAll(".cs-note").forEach(wireItem);
  updateEmpty();

  CS.on("notifications", (m) => {
    if (m.type !== "notification.new") return;
    const tmp = document.createElement("div");
    tmp.innerHTML = (m.html || "").trim();
    const li = tmp.firstElementChild;
    if (!li) return;
    list.prepend(li);
    wireItem(li);
    setUnread(unread + 1);
    updateEmpty();
  });
})();
