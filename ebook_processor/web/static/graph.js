/* The graph of the notes folder.

   A part of the library page (index.html), in a file of its own. It is an ordinary script that shares the page's
   names: h, api, put, $, fmt, kv and the rest are defined in index.html, and the page calls showGraph(params). */
"use strict";
/* ---------- notes: the graph ---------- */
// Every note and every book is a point; two are joined when one links to or cites the other. The layout is a
// small force simulation written for this page: joined points pull together, all points push apart, and the
// whole settles in a second or two. It is drawn on a canvas; the list under it holds the same notes as links.
async function showGraph(params) {
  const app = $("app"), canvas = h("canvas", { id: "gz", role: "img", tabindex: "0" }), info = h("span", { class: "m mute", id: "gz-info" });
  const find = h("input", { type: "search", id: "gz-q", "aria-label": "Find a note in the graph", placeholder: "Find a note", autocomplete: "off", style: "flex:1 1 200px;min-width:0" });
  const bar = h("div", { class: "pz-bar" }), listing = h("details", { class: "adv" });
  // The picture gets the window: no tall title here, and what the page is for stands under the graph.
  put(app, h("div", { class: "row", style: "justify-content:space-between;margin-top:22px" }, h("a", { class: "m", href: "#/notes" }, "← Notes"), h("b", { style: "font:500 21px/1.2 var(--grot);letter-spacing:-0.03em" }, "Graph")),
    bar, h("div", { class: "gz-wrap" }, canvas, h("div", { class: "legend m" }, h("span", { class: "dot" }, "Idea note"), h("span", { class: "dot index" }, "Index note"),
      h("span", { class: "dot project" }, "Project"), h("span", { class: "book" }, "Book or article"))),
    h("p", { class: "note" }, "Each point is a note; a square is a book or an article. Two are joined when one links to the other, or cites it. Point at a note to see what it is joined to, click to open it, drag to move it or the picture, scroll to zoom."), listing);
  let data;
  try { data = await api("/vault/graph"); } catch (e) { return put(bar, h("p", { class: "msg err" }, e.message)); }
  const state = { kind: params.get("kind") || "", tag: params.get("tag") || "", book: params.get("book") || "", q: "" };
  // The points start spread over a circle that grows with their number: packed into one small circle, every point
  // is at first compared with almost every other, and thousands of notes take many seconds to come apart.
  const spread = Math.max(260, 34 * Math.sqrt(data.nodes.length));
  const all = data.nodes.map((n, i) => ({ ...n, x: spread * Math.cos(i * 2.4) * Math.sqrt((i + 1) / data.nodes.length), y: spread * Math.sin(i * 2.4) * Math.sqrt((i + 1) / data.nodes.length), vx: 0, vy: 0, to: [] }));
  const byId = new Map(all.map((n) => [n.id, n]));
  for (const [a, b] of data.edges) { byId.get(a).to.push(byId.get(b)); byId.get(b).to.push(byId.get(a)); }
  let nodes = [], edges = [], busy = [], heat = 1, hover = null, drag = null, moved = false, view = { s: 1, x: 0, y: 0 }, frame = 0, stopped = false, own = false;
  const name = (n) => (n.untitled ? "A note without a title" : n.title);
  const radius = (n) => 3.5 + 1.7 * Math.sqrt(n.to.length);
  function choose() {                   // the points the filters leave, and the joins among them
    const book = state.book ? byId.get(state.book) : null;
    nodes = all.filter((n) => (state.kind ? n.kind === state.kind : !(n.untitled && !n.to.length))
      && (!state.tag || n.tags.some((t) => t.toLowerCase() === state.tag.toLowerCase())) && (!book || n === book || n.to.includes(book)));
    const shown = new Set(nodes);
    edges = data.edges.map(([a, b]) => [byId.get(a), byId.get(b)]).filter(([a, b]) => shown.has(a) && shown.has(b));
    for (const n of nodes) n.near = n.to.filter((o) => shown.has(o));
    busy = nodes.slice().sort((a, b) => b.near.length - a.near.length).slice(0, 14);   // the most joined: their names always show
    heat = 1; own = false; wake();
    info.textContent = `${fmt(nodes.length)} point${nodes.length === 1 ? "" : "s"} / ${fmt(edges.length)} join${edges.length === 1 ? "" : "s"}`;
    canvas.setAttribute("aria-label", `A graph of ${nodes.length} notes and books with ${edges.length} joins between them. The same notes are listed under it.`);
    const ranked = nodes.slice().sort((a, b) => b.near.length - a.near.length || name(a).localeCompare(name(b)));
    put(listing, h("summary", {}, "The same as a list, most joined first"), h("div", { class: "nz-list", id: "gz-list", style: "margin-top:12px" },
      ranked.map((n) => h("a", { class: "nz-row", href: "#/notes/" + encodeURIComponent(n.id) }, h("b", {}, name(n)),
        h("span", { class: "when m mute" }, `${n.near.length} join${n.near.length === 1 ? "" : "s"}`), h("span", { class: "sub" }, Notes.KINDS[n.kind] || n.kind)))));
  }
  function step() {                     // one turn of the layout
    const cell = 190, grid = new Map();
    for (const n of nodes) { const k = Math.floor(n.x / cell) + ":" + Math.floor(n.y / cell); (grid.get(k) || grid.set(k, []).get(k)).push(n); }
    for (const n of nodes) {
      const gx = Math.floor(n.x / cell), gy = Math.floor(n.y / cell);
      for (let i = gx - 1; i <= gx + 1; i++) for (let j = gy - 1; j <= gy + 1; j++) for (const o of grid.get(i + ":" + j) || []) {
        if (o === n) continue;
        let dx = n.x - o.x, dy = n.y - o.y, d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.5; }
        if (d2 < cell * cell) { const f = 2600 / d2; n.vx += dx * f * 0.02; n.vy += dy * f * 0.02; }
      }
      n.vx -= n.x * 0.004; n.vy -= n.y * 0.004;
    }
    // A join pulls its two ends together. A note with many joins is pulled by all of them at once, so each pull on
    // it is divided by the root of their number; and no point moves more than 40 pixels in one turn. Without these
    // a note with about a hundred joins jumps past its place, further each turn, until the numbers are too large
    // for the loop above to end: the page then hung for good.
    for (const [a, b] of edges) {
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = (d - 120) * 0.012;
      const fa = f / Math.sqrt(a.near.length || 1), fb = f / Math.sqrt(b.near.length || 1);
      a.vx += dx / d * fa; a.vy += dy / d * fa; b.vx -= dx / d * fb; b.vy -= dy / d * fb;
    }
    for (const n of nodes) {
      if (n === drag) { n.vx = n.vy = 0; continue; }
      n.vx *= 0.82; n.vy *= 0.82;
      let mx = n.vx * heat * 6, my = n.vy * heat * 6;
      const far = Math.hypot(mx, my);
      if (!(far < 1e9)) { n.vx = n.vy = mx = my = 0; }
      else if (far > 40) { const k = 40 / far; mx *= k; my *= k; n.vx *= k; n.vy *= k; }
      n.x += mx; n.y += my;
    }
    heat *= 0.985;
  }
  const colour = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  function draw() {
    const ratio = window.devicePixelRatio || 1, w = canvas.clientWidth, hgt = canvas.clientHeight;
    if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(hgt * ratio)) { canvas.width = Math.round(w * ratio); canvas.height = Math.round(hgt * ratio); }
    const c = canvas.getContext("2d"), ink = colour("--ink"), ghost = colour("--ghost"), blue = colour("--blue"), mute = colour("--mute"), paper = colour("--sheet");
    c.setTransform(ratio, 0, 0, ratio, 0, 0); c.clearRect(0, 0, w, hgt);
    c.translate(w / 2 + view.x, hgt / 2 + view.y); c.scale(view.s, view.s);
    const lit = hover ? new Set([hover, ...hover.near]) : null, q = state.q.trim().toLowerCase();
    c.lineWidth = 1 / view.s;
    for (const [a, b] of edges) { c.strokeStyle = lit && (a === hover || b === hover) ? ink : ghost; c.globalAlpha = lit && !(a === hover || b === hover) ? 0.35 : 1; c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke(); }
    c.font = `${12 / view.s}px ${colour("--grot") || "sans-serif"}`; c.textBaseline = "middle";
    for (const n of nodes) {
      const r = radius(n), found = q && name(n).toLowerCase().includes(q);
      c.globalAlpha = (lit && !lit.has(n)) || (q && !found) ? 0.25 : 1;
      c.fillStyle = n.kind === "index" ? blue : n.kind === "quick" ? mute : ink; c.strokeStyle = n.kind === "project" ? blue : ink; c.lineWidth = (n.kind === "project" ? 3 : 1.5) / view.s;
      c.beginPath();
      if (Notes.isSource(n.kind)) { c.rect(n.x - r, n.y - r, 2 * r, 2 * r); c.fillStyle = paper; c.fill(); c.stroke(); }
      else if (n.kind === "project") { c.arc(n.x, n.y, r, 0, 7); c.fillStyle = paper; c.fill(); c.stroke(); }
      else { c.arc(n.x, n.y, r, 0, 7); c.fill(); }
      if (found) { c.strokeStyle = blue; c.lineWidth = 2 / view.s; c.beginPath(); c.arc(n.x, n.y, r + 4 / view.s, 0, 7); c.stroke(); }
      if ((lit && lit.has(n)) || found || view.s > 1.5 || busy.includes(n) || nodes.length <= 40) { c.fillStyle = ink; const t = name(n); c.fillText(t.length > 38 ? t.slice(0, 37) + "…" : t, n.x + r + 4 / view.s, n.y); }
    }
    c.globalAlpha = 1;
  }
  function fit() {                      // the whole graph in view, until the person moves it themselves
    if (!nodes.length || !canvas.clientWidth) return;
    const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y), x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    view.s = Math.max(0.2, Math.min(2, (canvas.clientWidth - 260) / (x1 - x0 + 1), (canvas.clientHeight - 90) / (y1 - y0 + 1)));
    view.x = -(x0 + x1) / 2 * view.s - 60; view.y = -(y0 + y1) / 2 * view.s;
  }
  // A frame is drawn only while the layout is settling or after something changed: a settled graph that is left
  // alone costs nothing. A frame gives the layout 8 ms at most, so a large graph stays answerable while it settles.
  function loop() {
    frame = 0;
    if (stopped) return;
    if (heat > 0.03) { const began = performance.now(); for (let i = 0; i < 3 && (i === 0 || performance.now() - began < 8); i++) step(); if (!own) fit(); }
    draw();
    if (heat > 0.03 || drag) wake();
  }
  function wake() { if (!frame && !stopped) frame = requestAnimationFrame(loop); }
  const where = (e) => { const r = canvas.getBoundingClientRect(); return { x: (e.clientX - r.left - r.width / 2 - view.x) / view.s, y: (e.clientY - r.top - r.height / 2 - view.y) / view.s }; };
  const under = (p) => { let best = null, bd = Infinity; for (const n of nodes) { const d = Math.hypot(n.x - p.x, n.y - p.y) - radius(n); if (d < 8 / view.s && d < bd) { best = n; bd = d; } } return best; };
  let last = null;
  canvas.addEventListener("pointerdown", (e) => { canvas.setPointerCapture(e.pointerId); const p = where(e); drag = under(p); moved = false; last = { x: e.clientX, y: e.clientY }; canvas.style.cursor = "grabbing"; wake(); });
  canvas.addEventListener("pointermove", (e) => {
    const p = where(e);
    if (last && (Math.abs(e.clientX - last.x) > 2 || Math.abs(e.clientY - last.y) > 2)) moved = true;
    if (last && drag && moved) { drag.x = p.x; drag.y = p.y; heat = Math.max(heat, 0.3); }
    else if (last && moved) { view.x += e.clientX - last.x; view.y += e.clientY - last.y; own = true; }
    if (last && moved) last = { x: e.clientX, y: e.clientY };
    if (!last) { const was = hover; hover = under(p); canvas.style.cursor = hover ? "pointer" : "grab"; canvas.title = hover ? name(hover) : ""; if (hover !== was) wake(); }
    else wake();
  });
  canvas.addEventListener("pointerup", (e) => { const hit = drag; drag = null; last = null; canvas.style.cursor = "grab"; wake(); if (hit && !moved) location.hash = "#/notes/" + encodeURIComponent(hit.id); });
  canvas.addEventListener("pointerleave", () => { if (!last) hover = null; wake(); });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect(), mx = e.clientX - r.left - r.width / 2, my = e.clientY - r.top - r.height / 2, to = Math.max(0.2, Math.min(5, view.s * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    view.x = mx - (mx - view.x) * to / view.s; view.y = my - (my - view.y) * to / view.s; view.s = to; own = true; wake();
  }, { passive: false });
  canvas.addEventListener("keydown", (e) => { wake(); const d = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] }[e.key]; own = true; if (d) { e.preventDefault(); view.x += d[0]; view.y += d[1]; } else if (e.key === "+" || e.key === "=") view.s = Math.min(5, view.s * 1.15); else if (e.key === "-") view.s = Math.max(0.2, view.s / 1.15); });
  find.addEventListener("input", () => { state.q = find.value; wake(); });
  find.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const q = state.q.trim().toLowerCase(), hit = q ? nodes.find((n) => name(n).toLowerCase().includes(q)) : null;
    if (hit) { own = true; view.s = Math.max(view.s, 1.6); view.x = -hit.x * view.s; view.y = -hit.y * view.s; hover = hit; wake(); }
  });
  const pick = (key, label, options) => { const sel = h("select", { "aria-label": label, onchange: () => { state[key] = sel.value; choose(); } }, options.map(([v, t]) => h("option", { value: v }, t))); sel.value = state[key]; if (sel.selectedIndex < 0) sel.selectedIndex = 0; return sel; };
  const tags = [...new Set(all.flatMap((n) => n.tags))].sort((a, b) => a.localeCompare(b));
  put(bar, find, pick("kind", "Kind", [["", "Every kind"], ["idea", "Idea notes"], ["index", "Index notes"], ["project", "Projects"], ["project-note", "Project notes"], ["quick", "Quick notes"], ["book", "Books"], ["article", "Articles"]]),
    pick("tag", "Tag", [["", "Any tag"], ...tags.map((t) => [t, "#" + t])]), pick("book", "Book or article", [["", "Any book or article"], ...all.filter((n) => Notes.isSource(n.kind)).map((n) => [n.id, n.title])]), info);
  choose();
  // Most of the settling happens before the first picture, for a graph that is quick to settle; a large one gets a
  // quarter of a second and goes on settling in view.
  for (let i = 0, began = performance.now(); i < 120 && performance.now() - began < 250; i++) step();
  window.graphState = () => ({ points: nodes.map((n) => { const r = canvas.getBoundingClientRect(); return { id: n.id, title: n.title, kind: n.kind, joins: n.near.length, x: r.left + r.width / 2 + view.x + n.x * view.s, y: r.top + r.height / 2 + view.y + n.y * view.s }; }),
    joins: edges.length, hover: hover ? hover.id : null, zoom: view.s, settled: heat <= 0.03, drawing: !!frame,
    extent: nodes.reduce((m, n) => Math.max(m, Math.abs(n.x), Math.abs(n.y)), 0) });
  // the picture is drawn again when its size or the colours change (the window, the theme in Settings)
  const sized = new ResizeObserver(wake), themed = new MutationObserver(wake);
  sized.observe(canvas); themed.observe(document.documentElement, { attributes: true });
  viewStop = () => { stopped = true; cancelAnimationFrame(frame); sized.disconnect(); themed.disconnect(); delete window.graphState; };
  wake();
}
