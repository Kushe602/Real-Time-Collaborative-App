// Shared whiteboard: notes, shapes, freehand pen, text, colour palette, live drag + delete.
(function () {
  const CS = window.CS;
  if (!CS) return;
  const SVGNS = "http://www.w3.org/2000/svg";
  let root = null;
  let surface = null;
  let canvas = null;
  let wbId = null;
  let tool = "select";
  let color = "#e2e8f0";
  const cursors = new Map(); // user_id -> cursor element
  let cursorTimer = null;
  let lastCursor = null;
  let drawing = false;
  let points = null;
  let preview = null;
  CS.surfaceInits = CS.surfaceInits || {};
  CS.surfaceInits.whiteboard = function (rootEl, id) {
    root = rootEl;
    wbId = id;
    tool = "select";
    surface = root.querySelector("[data-wb-surface]");
    canvas = root.querySelector("[data-wb-canvas]");
    cursors.clear();
    let seed = [];
    try { seed = JSON.parse(root.querySelector("[data-wb-elements]").textContent || "[]"); } catch (e) { seed = []; }
    seed.forEach(renderElement);

    root.querySelectorAll("[data-wb-add]").forEach((btn) =>
      btn.addEventListener("click", () => addElement(btn.dataset.wbAdd)));
    root.querySelectorAll("[data-wb-tool]").forEach((btn) =>
      btn.addEventListener("click", () => setTool(btn.dataset.wbTool)));
    root.querySelectorAll("[data-wb-color]").forEach((btn) =>
      btn.addEventListener("click", () => setColor(btn.dataset.wbColor)));
    const firstColor = root.querySelector("[data-wb-color]");
    if (firstColor) color = firstColor.dataset.wbColor;

    surface.addEventListener("pointerdown", onSurfaceDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerleave", () =>
      CS.send({ channel: "presence", type: "cursor", surface: "whiteboard:" + wbId, cursor: null }));
    CS.onCursor = onRemoteCursor;
    updateToolUI();
  };

  function setTool(t) { tool = t; updateToolUI(); }
  function setColor(c) {
    color = c;
    root.querySelectorAll("[data-wb-color]").forEach((b) =>
      b.classList.toggle("cs-swatch-active", b.dataset.wbColor === c));
  }
  function updateToolUI() {
    root.querySelectorAll("[data-wb-tool]").forEach((b) =>
      b.classList.toggle("cs-tool-active", b.dataset.wbTool === tool));
    if (canvas) canvas.classList.toggle("cs-wb-drawing", tool !== "select");
  }

  function surfacePoint(e) {
    const r = surface.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  function addElement(kind) {
    const x = canvas.scrollLeft + 80;
    const y = canvas.scrollTop + 80;
    const data = kind === "note"
      ? { x, y, w: 168, h: 128, text: "New note", color }
      : { x, y, w: 150, h: 110, color };
    CS.send({ channel: "whiteboard", type: "element.create", whiteboard_id: wbId, kind, data });
  }

  // Pen and text place new elements where you press on the empty canvas.
  function onSurfaceDown(e) {
    if (tool === "select") return;
    if (e.target.closest(".cs-wb-el") || e.target.closest(".cs-el-del")) return;
    const pt = surfacePoint(e);
    if (tool === "text") {
      e.preventDefault();
      const text = window.prompt("Text");
      if (text && text.trim()) {
        CS.send({
          channel: "whiteboard", type: "element.create", whiteboard_id: wbId, kind: "text",
          data: { x: pt.x, y: pt.y, text: text.trim().slice(0, 2000), color, size: 18 },
        });
      }
      return;
    }
    if (tool === "pen") {
      e.preventDefault();
      drawing = true;
      points = [[pt.x, pt.y]];
      surface.setPointerCapture(e.pointerId);
      preview = document.createElementNS(SVGNS, "svg");
      preview.setAttribute("class", "cs-wb-draw-preview");
      preview.setAttribute("width", surface.clientWidth);
      preview.setAttribute("height", surface.clientHeight);
      const poly = document.createElementNS(SVGNS, "polyline");
      poly.setAttribute("fill", "none");
      poly.setAttribute("stroke", color);
      poly.setAttribute("stroke-width", 3);
      poly.setAttribute("stroke-linecap", "round");
      poly.setAttribute("stroke-linejoin", "round");
      preview.appendChild(poly);
      surface.appendChild(preview);
      surface.addEventListener("pointermove", onDraw);
      surface.addEventListener("pointerup", onDrawEnd);
    }
  }

  function onDraw(e) {
    if (!drawing) return;
    const pt = surfacePoint(e);
    points.push([pt.x, pt.y]);
    preview.firstChild.setAttribute("points", points.map((p) => p[0] + "," + p[1]).join(" "));
  }

  function onDrawEnd() {
    surface.removeEventListener("pointermove", onDraw);
    surface.removeEventListener("pointerup", onDrawEnd);
    drawing = false;
    if (preview) { preview.remove(); preview = null; }
    if (points && points.length > 1) {
      CS.send({
        channel: "whiteboard", type: "element.create", whiteboard_id: wbId, kind: "path",
        data: { points, color, size: 3 },
      });
    }
    points = null;
  }
  function renderElement(el) {
    if (!surface || !el || surface.querySelector(`[data-el="${el.id}"]`)) return;
    let node = null;
    if (el.kind === "path") node = renderPath(el);
    else if (el.kind === "text") node = renderText(el);
    else node = renderBox(el);
    if (node) surface.appendChild(node);
  }

  function renderBox(el) {
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
      node.addEventListener("dblclick", () => editText(node, t));
    }
    bindDrag(node);
    addDelete(node, el.id);
    return node;
  }

  function renderText(el) {
    const d = el.data || {};
    const node = document.createElement("div");
    node.dataset.el = el.id;
    node.className = "cs-wb-el cs-wb-text";
    node.style.left = (d.x || 0) + "px";
    node.style.top = (d.y || 0) + "px";
    node.style.color = d.color || "#e2e8f0";
    if (d.size) node.style.fontSize = d.size + "px";
    const t = document.createElement("div");
    t.className = "cs-wb-text-body";
    t.textContent = d.text || "";
    node.appendChild(t);
    node.addEventListener("dblclick", () => editText(node, t));
    bindDrag(node);
    addDelete(node, el.id);
    return node;
  }

  function renderPath(el) {
    const d = el.data || {};
    const pts = d.points || [];
    if (!pts.length) return null;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    pts.forEach((p) => {
      minX = Math.min(minX, p[0]); minY = Math.min(minY, p[1]);
      maxX = Math.max(maxX, p[0]); maxY = Math.max(maxY, p[1]);
    });
    const pad = (d.size || 3) + 4;
    minX -= pad; minY -= pad; maxX += pad; maxY += pad;
    const w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY);
    const node = document.createElement("div");
    node.dataset.el = el.id;
    node.className = "cs-wb-el cs-wb-path";
    node.style.left = minX + "px";
    node.style.top = minY + "px";
    node.style.width = w + "px";
    node.style.height = h + "px";
    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("class", "cs-wb-path-svg");
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    const poly = document.createElementNS(SVGNS, "polyline");
    poly.setAttribute("points", pts.map((p) => (p[0] - minX) + "," + (p[1] - minY)).join(" "));
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", d.color || "#e2e8f0");
    poly.setAttribute("stroke-width", d.size || 3);
    poly.setAttribute("stroke-linecap", "round");
    poly.setAttribute("stroke-linejoin", "round");
    svg.appendChild(poly);
    node.appendChild(svg);
    addDelete(node, el.id);
    return node;
  }

  function addDelete(node, elId) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cs-el-del";
    b.textContent = "✕";
    b.title = "Delete";
    b.addEventListener("pointerdown", (e) => e.stopPropagation());
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      CS.send({ channel: "whiteboard", type: "element.delete", whiteboard_id: wbId, element_id: elId });
    });
    node.appendChild(b);
  }
  function bindDrag(node) {
    node.addEventListener("pointerdown", (e) => {
      if (tool !== "select") return;
      if (node.dataset.editing === "1" || e.target.isContentEditable) return;
      if (e.target.closest(".cs-el-del")) return;
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

  function editText(node, textEl) {
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
      if (!n || !m.data) return;
      if (m.data.text != null) {
        const t = n.querySelector(".cs-wb-note-text") || n.querySelector(".cs-wb-text-body");
        if (t) t.textContent = m.data.text;
      }
      if (m.data.x != null) n.style.left = m.data.x + "px";
      if (m.data.y != null) n.style.top = m.data.y + "px";
    } else if (m.type === "element.deleted") {
      const n = surface.querySelector(`[data-el="${m.element_id}"]`);
      if (n) n.remove();
    }
  });
})();
