/* The Process view: making something of what was marked.

   A part of the library page (index.html), in a file of its own. It is an ordinary script that shares the page's
   names: h, api, put, $, fmt, kv and the rest are defined in index.html, and the page calls showProcess(params). */
"use strict";
/* ---------- notes: processing what was marked ---------- */
// Everything marked in a book, and every quick note, ends in one of three ways: an idea note is written from it,
// it is added to a note that exists, or it is set aside. The list is on the left; the item chosen, and then the
// note being written, on the right. What is shown is kept in the address, so that Back returns to it.
async function showProcess(params) {
  const app = $("app");
  const state = { show: params.get("show") || "waiting", order: params.get("order") || AppSettings.values.process_order || "book", book: params.get("book") || "", item: params.get("item") || "" };
  const counts = h("div"), bar = h("div", { class: "pz-bar" }), list = h("div", { class: "pz-list" }), pane = h("div", { class: "pz-pane" });
  put(app, h("a", { class: "back m", href: state.book ? "#/run/" + state.book : "#/notes" }, state.book ? "← Book page" : "← Notes"),
    h("div", { class: "title" }, h("h1", {}, "Process"), counts), bar, h("div", { class: "pz-cols" }, list, pane));
  let data = null, chosen = null, writing = false, turn = 0, drawn = 0;
  const clip = (text, n) => (text.length > n ? text.slice(0, n).replace(/\s+\S*$/, "") + " …" : text);
  const keep = () => history.replaceState(null, "", "#/notes/process?" + new URLSearchParams(Object.entries(state).filter(([k, v]) => v && !(k === "show" && v === "waiting") && !(k === "order" && v === "book"))));
  const every = () => [...data.quick, ...data.books.flatMap((b) => b.items.map((i) => Object.assign(i, { book: b })))];
  const shown = () => every().filter((i) => i.state === state.show);
  const ordered = () => (state.order === "oldest" ? shown().slice().sort((a, b) => (a.created || "").localeCompare(b.created || "")) : shown());
  const post = async (path, json) => { try { return await api(path, { method: "POST", json }); } catch (e) { alert(e.message); return null; } };
  function stop() { if (noteView) { noteView.destroy(); noteView = null; } writing = false; }
  async function load() {
    const mine = ++turn;
    try { data = await api("/process" + (state.book ? "?job=" + encodeURIComponent(state.book) : "")); }
    catch (e) { return put(list, h("p", { class: "msg err" }, e.message)); }
    if (mine !== turn) return;
    if (chosen) chosen = every().find((i) => i.id === chosen.id) || null;
    else if (state.item) chosen = every().find((i) => i.id === state.item) || null;
    draw();
    if (!writing) drawPane();
  }
  async function aside(items, on) {     // one item, a section or a book: set aside, or brought back
    const next = on && chosen && items.includes(chosen) ? ordered().find((i) => !items.includes(i)) || null : chosen;
    for (const q of items.filter((i) => i.kind === "quick")) if (!await post("/process/aside", { quick: q.id, aside: on })) return;
    const byBook = new Map();
    for (const i of items.filter((x) => x.kind !== "quick")) byBook.set(i.job, [...(byBook.get(i.job) || []), i.id]);
    for (const [job, ids] of byBook) if (!await post("/process/aside", { job, ids, aside: on })) return;
    if (!writing) chosen = next;
    await load();
  }
  // Choosing an item changes two rows of the list. The list itself is not drawn again: with thousands of items
  // that took seconds for each click.
  const mark = (item, on) => { const at = item && $("pz-" + item.id); if (at) at.setAttribute("aria-current", String(on)); };
  function choose(item) { stop(); mark(chosen, false); chosen = item; mark(chosen, true); state.item = item ? item.id : ""; keep(); drawPane(); }
  function row(i, where) {
    const text = i.text || i.note;
    return h("div", { class: "pz-item", id: "pz-" + i.id, role: "button", tabindex: "0", "aria-current": String(!!chosen && chosen.id === i.id),
      onclick: (e) => { if (!e.target.closest("button, a")) choose(i); },
      onkeydown: (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); choose(i); } } },
      h("q", {}, clip(text, 240)),
      i.text && i.note ? h("span", { class: "own" }, clip(i.note, 160)) : null,
      h("span", { class: "meta" }, where ? h("span", {}, where) : null,
        i.kind === "highlight" ? null : h("span", {}, { quick: "Quick note", note: "Your note on a paragraph", "section-note": "Your note on the section", "chapter-note": "Your note on the chapter", "book-note": "Your note on the book" }[i.kind]),
        i.notes.map((n) => h("a", { href: "#/notes/" + encodeURIComponent(n.id), style: "color:var(--blue);text-transform:none" }, n.untitled ? "a note without a title" : n.title)),
        i.state === "waiting" ? h("button", { class: "text", type: "button", onclick: () => aside([i], true) }, "Set aside") : null,
        i.state === "aside" ? h("button", { class: "text", type: "button", onclick: () => aside([i], false) }, "Bring back") : null));
  }
  function draw() {
    drawn++;                              // a list still being filled in is given up
    const n = data.counts, tab = (key, label) => h("button", { type: "button", "aria-pressed": String(state.show === key), onclick: () => { state.show = key; keep(); draw(); } }, `${label} ${n[key]}`);
    put(counts, kv("Waiting", fmt(n.waiting)), kv("Processed", fmt(n.done)), kv("Set aside", fmt(n.aside)));
    const order = h("select", { "aria-label": "Order", onchange: () => { state.order = order.value; AppSettings.set("process_order", order.value); keep(); draw(); } },
      h("option", { value: "book" }, "By book, in reading order"), h("option", { value: "oldest" }, "Oldest first"));
    order.value = state.order;
    put(bar, h("span", { class: "filters", role: "group", "aria-label": "Show", style: "margin:0" }, tab("waiting", "Waiting"), tab("done", "Processed"), tab("aside", "Set aside")), order,
      state.book ? h("a", { class: "m", href: "#/notes/process" }, "Every book") : null);
    const all = shown(), word = state.show === "aside" ? "Bring back" : "Set aside", can = state.show !== "done";
    const group = (items, on) => (can && items.length > 1 ? h("button", { class: "text", type: "button", onclick: () => {
      if (items.length < 8 || confirm(`${word} ${items.length} items?`)) aside(items, on);
    } }, `${word} all ${items.length}`) : null);
    if (!all.length) {
      return put(list, h("p", { class: "empty", style: "padding-top:26px" }, state.show === "waiting" ? (n.done + n.aside ? "Nothing waits. Everything you marked has become a note or was set aside."
        : "Nothing to process yet. Highlight something while you read, or write a quick note with Ctrl + . on any page.") : state.show === "done" ? "Nothing processed yet." : "Nothing is set aside."));
    }
    // A long list is drawn in parts: the first rows at once, the rest in the pauses of the page, so that thousands
    // of items do not keep the page from answering. `parts` holds elements and, for rows, functions that make one.
    if (state.order === "oldest") {
      const box = h("div", { style: "margin-top:26px;border-top:1px solid var(--ink)" });
      put(list, box);
      return fill(box, ordered().map((i) => () => row(i, i.book ? i.book.title + (i.section_label ? " / " + i.section_label : "") : "")));
    }
    const parts = [], quick = data.quick.filter((i) => i.state === state.show);
    if (quick.length) parts.push(h("h3", {}, "Quick notes", group(quick, state.show === "waiting")), quick.map((i) => () => row(i)));
    for (const b of data.books) {
      const mine = b.items.filter((i) => i.state === state.show);
      if (!mine.length) continue;
      parts.push(h("h3", {}, b.title, group(mine, state.show === "waiting")));
      const sections = [];
      for (const i of mine) {
        const label = [i.chapter_title, i.section_label && i.section_label !== i.chapter_title ? i.section_label : ""].filter(Boolean).join(" / ");
        if (!sections.length || sections[sections.length - 1].label !== label) sections.push({ label, items: [] });
        sections[sections.length - 1].items.push(i);
      }
      for (const s of sections) parts.push(s.label ? h("h4", {}, h("span", {}, s.label), sections.length > 1 ? group(s.items, state.show === "waiting") : null) : null, s.items.map((i) => () => row(i)));
    }
    put(list);
    fill(list, parts.flat(Infinity).filter((k) => k != null && k !== false));
  }
  function fill(box, parts) {             // 300 at once, then 300 in each pause, until all are there or the list is drawn anew
    const mine = ++drawn;
    let at = 0;
    const some = () => {
      if (mine !== drawn || !box.isConnected) return;
      box.append(...parts.slice(at, at + 300).map((k) => (typeof k === "function" ? k() : k)));
      at += 300;
      if (at < parts.length) setTimeout(some, 0); else box.dataset.all = "1";
    };
    some();
  }
  // The note that is written from an item, or the note an item was added to, in the right-hand column.
  function write(options) {
    stop(); writing = true;
    put(pane);
    noteView = Notes.mount(pane, { ...options, compact: true, onPlace: openPlace,
      close: () => { stop(); const next = chosen && chosen.state === state.show ? chosen : ordered()[0] || null; chosen = next; state.item = next ? next.id : ""; keep(); draw(); drawPane(); },
      onGone: () => { stop(); load(); },
      onOpen: (other) => { location.hash = "#/notes/" + encodeURIComponent(other); },
      onTag: (tag) => { location.hash = notesAt({ tag }); } });
  }
  async function idea(i) {
    if (i.kind === "quick") return write({ id: i.id });   // a quick note becomes an idea note in place
    let cited;
    try { cited = await Notes.cite(i.job, i.block); } catch (e) { return alert(e.message); }
    write({ draft: { kind: "idea", body: `${i.words ? i.words.trim() : ""}\n\nSource: ${cited.link}\n`, sources: [cited.link], cited: [cited] } });
  }
  async function addTo(i, note, open) {
    const got = await post("/process/add", i.kind === "quick" ? { note: note.id, quick: i.id } : { note: note.id, job: i.job, block: i.block });
    if (!got) return;
    Notes.toast(i.kind === "quick" ? `Its words were added to “${got.title}”. The quick note is in the trash.` : `Added to “${got.title}” as a source.`);
    if (open) { write({ id: got.id }); load(); }
    else { chosen = ordered().find((x) => x !== i && x.id !== i.id) || null; state.item = chosen ? chosen.id : ""; keep(); await load(); }
  }
  function drawPane() {
    const i = chosen;
    if (!i) return put(pane, h("p", { class: "empty" }, "Choose something on the left. Each item ends in one of three ways: you write an idea note from it, you add it to a note you have, or you set it aside."));
    const targets = h("div", { id: "pz-targets" }), find = h("input", { type: "text", "aria-label": "Find a note to add this to", placeholder: "Find a note by its title", autocomplete: "off" });
    const target = (n, why) => h("div", { class: "pz-to" }, h("b", {}, n.title), h("span", { class: "row" },
      h("button", { class: "text", type: "button", onclick: () => addTo(i, n, false) }, i.kind === "quick" ? "Add its words" : "Add as a source"),
      h("button", { class: "text", type: "button", onclick: () => addTo(i, n, true) }, "Open and write")), why ? h("small", {}, why) : null);
    const shares = (n) => (n.shares && n.shares.length ? "shares: " + n.shares.join(", ") : n.tags && n.tags.length ? "shares a tag" : "");
    let asked = 0;
    async function offer() {            // related notes while the box is empty; notes by title while something is typed in it
      const mine = ++asked, q = find.value.trim();
      let rows;
      try {
        rows = q ? (await api("/vault/complete?" + new URLSearchParams({ q }))).notes.filter((n) => !Notes.isSource(n.kind) && n.id !== i.id && !n.untitled)
          : (await api("/vault/related", { method: "POST", json: { text: `${i.text}\n${i.note}`, but: [i.id, ...i.notes.map((n) => n.id)] } })).notes.filter((n) => !n.untitled);
      } catch (e) { rows = []; }
      if (mine !== asked) return;
      put(targets, rows.length ? rows.slice(0, 6).map((n) => target(n, q ? n.excerpt : shares(n)))
        : h("p", { class: "empty", style: "padding:10px 0" }, q ? "No note of that name." : "No related note was found. Type a title above to choose one."));
    }
    let typing = null;
    find.addEventListener("input", () => { clearTimeout(typing); typing = setTimeout(offer, 200); });
    put(pane,
      i.text ? h("blockquote", {}, i.text) : null, i.note ? h("p", { class: "mine" }, i.note) : null,
      h("div", { class: "by" }, h("span", {}, i.kind === "quick" ? "A quick note" + (i.title ? ` / ${i.title}` : "") : [i.book.title, i.chapter_title, i.section_label !== i.chapter_title ? i.section_label : ""].filter(Boolean).join(" / ")),
        i.go ? h("a", { href: `/read/${i.job}?go=${i.go.chapter}:${i.go.word}` }, "Open in the book") : null),
      i.notes.length ? [h("h5", {}, "Cited by"), i.notes.map((n) => h("div", { class: "pz-to" }, h("b", {}, n.untitled ? "A note without a title" : n.title),
        h("span", { class: "row" }, h("button", { class: "text", type: "button", onclick: () => write({ id: n.id }) }, "Open"))))] : null,
      h("div", { class: "actions", style: "margin-top:20px" },
        h("button", { class: "primary", type: "button", id: "pz-idea", onclick: () => idea(i) }, i.kind === "quick" ? "Make it an idea note" : i.notes.length ? "Another idea note" : "Idea note"),
        i.state === "waiting" ? h("button", { type: "button", onclick: () => aside([i], true) }, "Set aside") : null,
        i.state === "aside" ? h("button", { type: "button", onclick: () => aside([i], false) }, "Bring back") : null),
      h("h5", {}, "Or add it to a note you have"), h("div", { style: "margin:10px 0 4px" }, find), targets);
    offer();
  }
  notesReload = () => { if (!writing) load(); else api("/process" + (state.book ? "?job=" + encodeURIComponent(state.book) : "")).then((d) => { data = d; if (chosen) chosen = every().find((i) => i.id === chosen.id) || chosen; draw(); }, () => {}); };
  load();
}
