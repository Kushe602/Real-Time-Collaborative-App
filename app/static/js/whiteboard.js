// Shared whiteboard surface: sticky notes + shapes with live drag, note text, and cursors.
(function () {
  const CS = window.CS;
  if (!CS) return;
  let root = null;
  let surface = null;
  let canvas = null;
  let wbId = null;
  const COLORS = ["#fde68a", "#fca5a5", "#a7f3d0", "#bfdbfe", "#ddd6fe", "#fbcfe8"];
  const cursors = new Map(); // user_id -> cursor element
  let cursorTimer = null;
  let lastCursor = null;

  CS.surfaceInits = CS.surfaceInits || {};
  CS.surfaceInits.whiteboard = function (rootEl, id) {
    root = rootEl;
    wbId = id;
    surface = root.querySelector("[data-wb-surface]");
    canvas = root.querySelector("[data-wb-canvas]");
    cursors.clear();
    let seed = [];
    try { seed = JSON.parse(root.querySelector("[data-wb-elements]").textContent || "[]"); } catch (e) { seed = []; }
    seed.forEach(renderElement);
    root.querySelectorAll("[data-wb-add]").forEach((btn) =>
      btn.addEventListener("click", () => addElement(btn.dataset.wbAdd)));
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerleave", () =>
      CS.send({ channel: "presence", type: "cursor", surface: "whiteboard:" + wbId, cursor: null }));
    CS.onCursor = onRemoteCursor;
  };

  function addElement(kind) {
    const x = canvas.scrollLeft + 80;
    const y = canvas.scrollTop + 80;
    const color = COLORS[Math.floor(Math.random() * COLORS.length)];
    const data = kind === "note"
      ? { x, y, w: 168, h: 128, text: "New note", color }
      : { x, y, w: 150, h: 110, color };
    CS.send({ channel: "whiteboard", type: "element.create", whiteboard_id: wbId, kind, data });
  }

  function renderElement(el) {
    if (!surface || !el || surface.querySelector(`[data-el="${el.id}"]`)) return;
    const d = el.data || {};
    const node = document.createElement("div");
    node.dataset.el = el.id;
    node.className = "cs-wb-el " + (el.kind === "note" ? "cs-wb-note" : "cs-wb-shape");
    node.style.left = (d.x || 0) + "px";
    node.style.top = (d.y || 0) + "px";
    node.style.width = (d.w || 150) + "px";
    node.style.height = (d.h || 110) + "px";
    if (el.kind === "ellipse") node.style.borderRadius = "9999px";
    if (d.color) {
      if (el.kind === "note") node.style.background = d.color;
      else { node.style.borderColor = d.color; node.style.background = d.color + "22"; }
    }
    if (el.kind === "note") {
      const t = document.createElement("div");
      t.className = "cs-wb-note-text";
      t.textContent = d.text || "";
      node.appendChild(t);
      node.addEventListener("dblclick", () => editNote(node, t));
    }
    bindDrag(node);
    surface.appendChild(node);
  }

  function bindDrag(node) {
    node.addEventListener("pointerdown", (e) => {
      if (node.dataset.editing === "1" || e.target.isContentEditable) return;
      e.preventDefault();
      const sx = e.clientX, sy = e.clientY;
      const ox = parseFloat(node.style.left) || 0;
      const oy = parseFloat(node.style.top) || 0;
      let nx = ox, ny = oy, raf = null;
      node.setPointerCapture(e.pointerId);
      const move = (ev) => {
        nx = Math.max(0, ox + (ev.clientX - sx));
        ny = Math.max(0, oy + (ev.clientY - sy));
        node.style.left = nx + "px";
        node.style.top = ny + "px";
        if (!raf) raf = requestAnimationFrame(() => { raf = null; sendMove(node, nx, ny); });
      };
      const up = () => {
        node.removeEventListener("pointermove", move);
        node.removeEventListener("pointerup", up);
        sendMove(node, nx, ny);
      };
      node.addEventListener("pointermove", move);
      node.addEventListener("pointerup", up);
    });
  }

  function sendMove(node, x, y) {
    CS.send({ channel: "whiteboard", type: "element.move", whiteboard_id: wbId, element_id: node.dataset.el, x, y });
  }

  function editNote(node, textEl) {
    node.dataset.editing = "1";
    textEl.contentEditable = "true";
    textEl.focus();
    const finish = () => {
      textEl.contentEditable = "false";
      node.dataset.editing = "0";
      textEl.removeEventListener("blur", finish);
      CS.send({
        channel: "whiteboard", type: "element.update", whiteboard_id: wbId,
        element_id: node.dataset.el, data: { text: textEl.textContent },
      });
    };
    textEl.addEventListener("blur", finish);
  }

  function onPointerMove(e) {
    const r = canvas.getBoundingClientRect();
    lastCursor = { x: canvas.scrollLeft + (e.clientX - r.left), y: canvas.scrollTop + (e.clientY - r.top) };
    if (cursorTimer) return;
    cursorTimer = setTimeout(() => {
      cursorTimer = null;
      CS.send({ channel: "presence", type: "cursor", surface: "whiteboard:" + wbId, cursor: lastCursor });
    }, 55);
  }

  function onRemoteCursor(m) {
    if (!root || !root.isConnected || !surface) return;
    if (m.surface !== "whiteboard:" + wbId || !m.cursor) { removeCursor(m.user_id); return; }
    let c = cursors.get(m.user_id);
    if (!c) {
      c = document.createElement("div");
      c.className = "cs-cursor";
      const dot = document.createElement("div");
      dot.className = "cs-cursor-dot";
      dot.style.background = m.color;
      const label = document.createElement("div");
      label.className = "cs-cursor-label";
      label.style.background = m.color;
      label.textContent = m.display_name;
      c.append(dot, label);
      surface.appendChild(c);
      cursors.set(m.user_id, c);
    }
    c.style.transform = `translate(${m.cursor.x}px, ${m.cursor.y}px)`;
  }

  function removeCursor(userId) {
    const c = cursors.get(userId);
    if (c) { c.remove(); cursors.delete(userId); }
  }

  CS.on("whiteboard", (m) => {
    if (!root || !root.isConnected || m.whiteboard_id !== wbId) return;
    if (m.type === "element.created") {
      renderElement({ id: m.id, kind: m.kind, data: m.data });
    } else if (m.type === "element.moved") {
      const n = surface.querySelector(`[data-el="${m.element_id}"]`);
      if (n) { n.style.left = m.x + "px"; n.style.top = m.y + "px"; }
    } else if (m.type === "element.updated") {
      const n = surface.querySelector(`[data-el="${m.element_id}"]`);
      if (n && m.data && m.data.text != null) {
        const t = n.querySelector(".cs-wb-note-text");
        if (t) t.textContent = m.data.text;
      }
    }
  });
})();
