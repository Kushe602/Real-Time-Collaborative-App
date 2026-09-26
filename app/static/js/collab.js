// CollabSpace live client: one WebSocket per workspace, multiplexing every channel.
(function () {
  const cfg = window.CS;
  if (!cfg) return;

  const dot = document.getElementById("live-dot");
  const text = document.getElementById("live-text");
  const presenceEl = document.getElementById("presence");
  const chatLog = document.getElementById("chat-log");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");

  const listeners = {};
  const presence = new Map(); // user_id -> {display_name, color, cursor, surface}
  let ws = null;
  let backoff = 500;

  function on(channel, fn) {
    (listeners[channel] || (listeners[channel] = [])).push(fn);
  }
  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }
  function initials(name) {
    const p = (name || "").split(" ").filter(Boolean);
    if (!p.length) return "?";
    return (p.length === 1 ? p[0].slice(0, 2) : p[0][0] + p[p.length - 1][0]).toUpperCase();
  }
  function setLive(v) {
    dot.classList.toggle("cs-offline", !v);
    text.textContent = v ? "live" : "reconnecting…";
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/workspace/${cfg.workspaceId}`);
    ws.onopen = () => { backoff = 500; setLive(true); };
    ws.onclose = () => {
      setLive(false);
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 8000);
    };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.channel === "system") return;
      (listeners[msg.channel] || []).forEach((fn) => fn(msg));
    };
  }

  function renderPresence() {
    presenceEl.innerHTML = "";
    const seen = new Set();
    [cfg.me, ...presence.values()].forEach((m) => {
      if (!m || seen.has(m.user_id)) return;
      seen.add(m.user_id);
      const s = document.createElement("span");
      s.className =
        "grid h-7 w-7 place-items-center rounded-full text-[10px] font-semibold ring-2 ring-slate-900";
      s.style.background = m.color;
      s.title = m.display_name + (m.user_id === cfg.me.user_id ? " (you)" : "");
      s.textContent = initials(m.display_name);
      presenceEl.appendChild(s);
    });
    if (window.CS.onPresenceChange) window.CS.onPresenceChange();
  }

  on("presence", (m) => {
    if (m.type === "roster") {
      presence.clear();
      m.members.forEach((x) => { if (x.user_id !== cfg.me.user_id) presence.set(x.user_id, x); });
    } else if (m.type === "join") {
      presence.set(m.member.user_id, m.member);
    } else if (m.type === "leave") {
      presence.delete(m.user_id);
    } else if (m.type === "cursor") {
      const e = presence.get(m.user_id) || {};
      Object.assign(e, { user_id: m.user_id, display_name: m.display_name, color: m.color, cursor: m.cursor, surface: m.surface });
      presence.set(m.user_id, e);
      if (window.CS.onCursor) window.CS.onCursor(m);
    }
    renderPresence();
  });

  function appendChat(m) {
    const empty = chatLog.querySelector("[data-empty]");
    if (empty) empty.remove();
    const wrap = document.createElement("div");
    wrap.className = "flex gap-2";
    const av = document.createElement("span");
    av.className = "grid h-6 w-6 flex-none place-items-center rounded-full text-[10px] font-semibold";
    av.style.background = m.color;
    av.textContent = initials(m.display_name);
    const body = document.createElement("div");
    body.className = "min-w-0";
    const name = document.createElement("div");
    name.className = "text-xs text-slate-400";
    name.textContent = m.display_name;
    const txt = document.createElement("div");
    txt.className = "break-words text-sm text-slate-200";
    txt.textContent = m.body;
    body.append(name, txt);
    wrap.append(av, body);
    chatLog.appendChild(wrap);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  on("chat", (m) => { if (m.type === "message") appendChat(m); });

  if (chatForm) {
    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = chatInput.value.trim();
      if (!v) return;
      send({ channel: "chat", type: "send", body: v });
      chatInput.value = "";
    });
  }

  window.CS.on = on;
  window.CS.send = send;
  window.CS.presence = presence;
  window.CS.initials = initials;

  // ---- opening surfaces into the center pane ----
  const surfaceRoot = document.getElementById("surface-root");
  const surfaceTitle = document.getElementById("surface-title");
  window.CS.surfaceInits = window.CS.surfaceInits || {};

  async function openSurface(kind, id, title) {
    try {
      const res = await fetch(`/workspaces/${cfg.workspaceId}/${kind}/${id}`);
      if (!res.ok) throw new Error(res.status);
      surfaceRoot.innerHTML = await res.text();
    } catch (err) {
      surfaceRoot.innerHTML =
        '<div class="grid h-full place-items-center text-slate-500">Could not open this surface.</div>';
      return;
    }
    window.CS.currentSurface = `${kind}:${id}`;
    if (title) surfaceTitle.textContent = title;
    send({ channel: "presence", type: "cursor", cursor: null, surface: window.CS.currentSurface });
    const init = window.CS.surfaceInits[kind];
    if (init) init(surfaceRoot.firstElementChild, id);
  }
  window.CS.openSurface = openSurface;

  document.querySelectorAll(".cs-surface-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      const [kind, id] = btn.dataset.surface.split(":");
      openSurface(kind, id, btn.textContent.trim());
    });
  });

  renderPresence();
  connect();
})();
