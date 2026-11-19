/* Notes: what the app's pages share for the notes folder.

   Loaded by the main page and by the reader, after settings.js.

     Notes.capture(where)            the Quick note box; `where` is the place in a book, or a function giving it
     Notes.install({ where, button }) Ctrl/Cmd + . and a button open the Quick note box on this page
     Notes.mount(element, options)   one note: its title, properties, text (the editor), sources and back-links
     Notes.loadEditor()              the editor's script (editor.js), fetched the first time a note is opened
     Notes.cite(job, block)          the link that cites a highlight or a note of a book: [[Book#^id]]
     Notes.toast(text, label, act)   a quiet notice at the edge of the window
     Notes.KINDS                     the names of the kinds of note

   A note is saved a moment after the last key, when the field is left, and with Ctrl/Cmd + S. Every save carries
   the stamp the note was loaded with; when the file was changed in another program meanwhile, the app does not
   write over it, and asks which version to keep.
*/
(function () {
"use strict";

const KINDS = { idea: "Idea note", index: "Index note", quick: "Quick note", "project-note": "Project note", project: "Project", book: "Book" };
const HIDDEN = ["id", "kind", "created", "tags", "sources"];   // properties that have a place of their own on the page
const hiddenFor = (kind) => [...HIDDEN, ...(kind === "project" ? ["status"] : kind === "project-note" ? ["project"] : [])];   // so have a project's state and a project note's project

/* ---------- a project's file: the two sections the page works on ---------- */
// A project is a note whose text has a section "Gathered" (links to what was collected for it) and a section
// "Outline" (a nested list of notes, passages and lines of the person's own). Both are found by their headings
// in the text of the editor, and a change made on the page rewrites that text, so it is saved like typing.
function sectionOf(lines, heading) {
  let fence = "";
  const head = (line) => { const m = /^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$/.exec(line); return m ? { level: m[1].length, text: m[2].trim().toLowerCase() } : null; };
  const real = lines.map((line) => { const f = /^ {0,3}(```|~~~)/.exec(line); if (fence) { if (f && f[1] === fence) fence = ""; return false; } if (f) { fence = f[1]; return false; } return true; });
  for (let n = 0; n < lines.length; n++) {
    const h = real[n] ? head(lines[n]) : null;
    if (!h || h.text !== heading.toLowerCase()) continue;
    let end = lines.length;
    for (let m = n + 1; m < lines.length; m++) { const other = real[m] ? head(lines[m]) : null; if (other && other.level <= h.level) { end = m; break; } }
    return { start: n + 1, end };
  }
  return null;
}
function outlineOf(text) {              // the entries of the outline, in order: { depth, text }
  const lines = text.split("\n"), part = sectionOf(lines, "Outline"), items = [], stack = [];
  if (!part) return items;
  for (const line of lines.slice(part.start, part.end)) {
    if (!line.trim()) continue;
    const m = /^([ \t]*)(?:[-*+]|\d+[.)])[ \t]+(.*)$/.exec(line);
    if (!m) { if (items.length) items[items.length - 1].text += " " + line.trim(); else items.push({ depth: 0, text: line.trim() }); continue; }
    const indent = m[1].replace(/\t/g, "    ").length;
    while (stack.length && indent < stack[stack.length - 1]) stack.pop();
    if (!stack.length || indent > stack[stack.length - 1]) stack.push(indent);
    items.push({ depth: stack.length - 1, text: m[2].trim() });
  }
  return items;
}
function withOutline(text, items) {     // the text with its outline written again from the entries
  let depth = 0;
  const made = items.map((i) => { depth = Math.max(0, Math.min(i.depth, depth + 1)); const line = "  ".repeat(depth) + "- " + i.text; return line; });
  const lines = text.split("\n"), part = sectionOf(lines, "Outline");
  if (!part) return text.replace(/\n*$/, "") + (text.trim() ? "\n\n" : "") + "## Outline\n\n" + made.join("\n") + "\n";
  lines.splice(part.start, part.end - part.start, "", ...made, ...(part.end < lines.length ? [""] : []));
  return lines.join("\n").replace(/\n*$/, "\n");
}
function gatheredOf(text) {             // the links under "Gathered", as written
  const lines = text.split("\n"), part = sectionOf(lines, "Gathered");
  return part ? [...lines.slice(part.start, part.end).join("\n").matchAll(/!?\[\[([^\[\]|#\n]*)(#[^\[\]|\n]*)?(?:\|([^\[\]\n]*))?\]\]/g)].map((m) => ({ raw: m[0].replace(/^!/, ""), shown: (m[3] || m[1]).trim() + (m[2] && !m[3] && m[2].startsWith("#^") ? " ¶" : "") })) : [];
}
const blockEnd = (items, n) => { let m = n + 1; while (m < items.length && items[m].depth > items[n].depth) m++; return m; };
const asWords = (text) => text.replace(/!?\[\[([^\[\]|#\n]*)(#[^\[\]|\n]*)?(?:\|([^\[\]\n]*))?\]\]/g, (_m, to, part, shown) => (shown || to).trim() + (part && !shown && part.startsWith("#^") ? " ¶" : ""));

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v; else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "checked" || k === "disabled" || k === "value" || k === "hidden" || k === "readOnly") node[k] = v; else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return node;
}
async function call(path, options = {}) {
  const headers = { "X-Ebook-Processor": "1" };
  if (options.json !== undefined) { options.body = JSON.stringify(options.json); headers["Content-Type"] = "application/json"; }
  if (options.keepalive && options.body && options.body.length > 60000) options.keepalive = false;   // browsers refuse more on a page that is closing
  const response = await fetch("/api" + path, { ...options, headers });
  const data = await response.json().catch(() => ({ error: "The app gave an unreadable answer." }));
  if (!response.ok) throw Object.assign(new Error(data.error || response.statusText), { status: response.status, data });
  return data;
}
const query = (values) => new URLSearchParams(Object.entries(values).filter(([, v]) => v != null && v !== "")).toString();
const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en", { year: "numeric", month: "short", day: "numeric" }) : "");

const CSS = `
.nt-toast { position: fixed; right: 18px; bottom: 18px; z-index: 60; background: var(--ink); color: var(--paper); padding: 10px 14px; display: flex; gap: 14px; align-items: center; font: 14px/1.3 var(--grot); max-width: min(420px, calc(100vw - 36px)); }
.nt-toast button { font: 12px/1 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; background: transparent; color: var(--paper); border: 1px solid var(--paper); border-radius: 0; padding: 7px 9px; cursor: pointer; }
.nt-toast button:hover { background: var(--paper); color: var(--ink); }
dialog.nt-capture { width: min(560px, calc(100vw - 24px)); padding: 0; border: 1px solid var(--ink); border-radius: 0; background: var(--paper); color: var(--ink); }
dialog.nt-capture::backdrop { background: color-mix(in srgb, var(--ink) 45%, transparent); }
.nt-capture header { display: flex; justify-content: space-between; align-items: center; padding: 10px 10px 10px 16px; border-bottom: 1px solid var(--ink); }
.nt-capture header b { font: 500 18px/1.2 var(--grot); letter-spacing: -0.02em; }
.nt-capture .in { padding: 14px 16px 16px; }
.nt-capture textarea { display: block; width: 100%; min-height: 130px; font: 17px/1.5 Georgia, "Iowan Old Style", serif; color: var(--ink); background: var(--sheet); border: 1px solid var(--ink); border-radius: 0; padding: 10px 12px; resize: vertical; }
.nt-capture .acts { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-top: 12px; }
.nt-capture small, .nt small { color: var(--mute); font-size: 13px; line-height: 1.35; }
.nt-capture button, .nt button { font: 12px/1 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; border: 1px solid var(--ink); border-radius: 0; background: transparent; color: var(--ink); padding: 10px 13px; cursor: pointer; }
.nt-capture button:hover, .nt button:hover { background: var(--ink); color: var(--paper); }
.nt-capture button.primary, .nt button.primary { background: var(--ink); color: var(--paper); }
.nt-capture button.primary:hover, .nt button.primary:hover { background: var(--blue); border-color: var(--blue); color: var(--on-blue); }
.nt button.quiet { border-color: transparent; text-decoration: underline; text-underline-offset: 3px; padding-left: 0; padding-right: 0; }
.nt button.quiet:hover { background: transparent; color: var(--blue); }
.nt button:disabled, .nt-capture button:disabled { opacity: 0.45; cursor: not-allowed; }
.nt kbd { font: 11px/1 var(--mono); border: 1px solid currentColor; padding: 2px 4px; margin-left: 6px; opacity: 0.7; }
.nt-err { border: 1px solid var(--ink); border-left-width: 8px; padding: 9px 13px; margin: 12px 0 0; font-size: 15px; }
.nt .nt-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; padding: 14px 0 10px; }
.nt .nt-head .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.nt .nt-status { font: 12px/1.5 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; color: var(--mute); }
.nt input.nt-title { display: block; width: 100%; font: 500 clamp(26px, 3.6vw, 40px)/1.15 var(--grot); letter-spacing: -0.035em; color: var(--ink); background: transparent; border: 0; border-bottom: 1px solid var(--ghost); border-radius: 0; padding: 4px 0 8px; }
.nt input.nt-title:focus { outline: 0; border-bottom-color: var(--ink); }
.nt.compact input.nt-title { font-size: 24px; letter-spacing: -0.025em; }   /* a narrow column: the whole title in view */
.nt input.nt-title::placeholder { color: var(--mute); }
.nt .nt-meta { display: flex; align-items: center; gap: 8px 14px; flex-wrap: wrap; margin: 12px 0 0; font: 12px/1.5 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; }
.nt .nt-meta .lab { display: inline-block; font: 11px/1 var(--mono); letter-spacing: 0.05em; padding: 4px 6px; border: 1px solid var(--ink); white-space: nowrap; }
.nt .nt-meta .lab.blue { background: var(--blue); border-color: var(--blue); color: var(--on-blue); }
.nt .nt-meta a.tag { color: var(--blue); text-decoration: none; text-transform: none; cursor: pointer; }
.nt .nt-meta a.tag:hover { text-decoration: underline; }
.nt .nt-note { border: 1px solid var(--ink); border-left-width: 8px; padding: 10px 14px; margin: 14px 0 0; display: flex; gap: 10px 16px; align-items: center; flex-wrap: wrap; font-size: 15px; }
.nt .nt-note > span { flex: 1 1 260px; }
.nt .nt-props { margin: 14px 0 0; border: 1px solid var(--ghost); background: var(--sheet); padding: 6px 14px 12px; }
.nt .nt-props .pr-row { display: grid; grid-template-columns: 150px minmax(0, 1fr) auto; gap: 8px 12px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--ghost); }
.nt .nt-props .pr-row > .k { font: 12px/1.5 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; overflow-wrap: anywhere; }
.nt .nt-props input[type=text], .nt .nt-props select { width: 100%; font: 15px/1.2 var(--grot); color: var(--ink); background: var(--paper); border: 1px solid var(--ink); border-radius: 0; padding: 7px 9px; }
.nt .nt-props code { font: 12px/1.5 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; }
.nt .nt-props .add { display: grid; grid-template-columns: 150px minmax(0, 1fr) auto; gap: 8px 12px; align-items: center; padding-top: 10px; }
.nt .nt-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(240px, 320px); gap: 28px var(--gut, 28px); margin-top: 22px; align-items: start; }
.nt.compact .nt-cols, .nt.book .nt-cols { grid-template-columns: minmax(0, 1fr); }
.nt .nt-aside .out { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0 8px; align-items: baseline; padding: 6px 0 6px calc(var(--d) * 18px); border-bottom: 1px solid var(--ghost); cursor: grab; }
.nt .nt-aside .out.over { box-shadow: inset 0 2px 0 var(--blue); } .nt .nt-aside .out.head > span:first-child { font-weight: 600; }
.nt .nt-aside .out > span:first-child { font: 15px/1.35 var(--grot); overflow-wrap: anywhere; }
.nt .nt-aside .out .acts { display: inline-flex; gap: 2px; } .nt .nt-aside .out .acts button { padding: 4px 6px; border-color: transparent; font-size: 12px; }
.nt .nt-aside .out .acts button:hover { border-color: var(--ink); }
.nt .nt-aside .addline { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; margin-top: 10px; }
.nt .nt-aside .addline input { font: 15px/1.2 var(--grot); color: var(--ink); background: var(--sheet); border: 1px solid var(--ink); border-radius: 0; padding: 7px 9px; min-width: 0; }
.nt .nt-aside .rowline { display: flex; gap: 8px 14px; flex-wrap: wrap; align-items: center; margin: 0 0 4px; }
.nt .nt-pick { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.nt .nt-pick select { font: 15px/1.2 var(--grot); color: var(--ink); background: var(--paper); border: 1px solid var(--ink); border-radius: 0; padding: 8px 9px; max-width: min(100%, 40ch); }
.nt .nt-more:not(:empty) { margin-top: 26px; } .nt.compact .nt-top:not(:empty) { margin-bottom: 4px; }
.nt .nt-aside .rel { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0 12px; align-items: start; border-bottom: 1px solid var(--ghost); }
.nt .nt-aside .rel .back { border-bottom: 0; } .nt .nt-aside .rel button { padding: 10px 0; }
.nt .nt-aside .rel.passage { padding: 10px 0; } .nt .nt-aside .rel.passage blockquote { font-size: 15px; border-left-color: var(--ghost); }
.nt .nt-aside .rel.passage .by { grid-column: 1 / -1; } .nt .nt-aside .rel.passage .by button { padding: 0; }
.nt .nt-editor { min-width: 0; --editor-min: 16em; }
.nt.compact .nt-editor { --editor-min: 9em; }
.nt .nt-aside h3 { font: 12px/1.5 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; font-weight: 400; margin: 0 0 8px; padding-bottom: 6px; border-bottom: 1px solid var(--ink); }
.nt .nt-aside section + section { margin-top: 26px; }
.nt .nt-aside blockquote { margin: 0; padding: 2px 0 2px 12px; border-left: 6px solid color-mix(in srgb, var(--blue) 40%, transparent); font: 16px/1.5 Georgia, "Iowan Old Style", serif; white-space: pre-wrap; }
.nt .nt-aside .src { margin: 0 0 16px; }
.nt .nt-aside .src .by { display: flex; gap: 6px 12px; flex-wrap: wrap; align-items: baseline; margin-top: 6px; font-size: 13px; color: var(--mute); }
.nt .nt-aside .back { display: block; margin: 0; padding: 9px 0; border-bottom: 1px solid var(--ghost); text-decoration: none; color: inherit; cursor: pointer; }
.nt .nt-aside .back:hover b { color: var(--blue); }
.nt .nt-aside .back b { display: block; font: 500 16px/1.3 var(--grot); letter-spacing: -0.01em; }
.nt .nt-aside .back span { display: block; font-size: 13px; line-height: 1.4; color: var(--mute); margin-top: 2px; }
.nt .nt-aside p.none { margin: 0; color: var(--mute); font-size: 14px; }
@media (max-width: 860px) { .nt .nt-cols { grid-template-columns: minmax(0, 1fr); } .nt .nt-props .pr-row, .nt .nt-props .add { grid-template-columns: minmax(0, 1fr); } }
`;
document.head.append(el("style", {}, CSS));

/* ---------- small things ---------- */
let editorLoading = null;
function loadEditor() {
  if (window.NoteEditor) return Promise.resolve();
  editorLoading = editorLoading || new Promise((resolve, reject) => {
    const script = el("script", { src: "/static/editor.js" });
    script.onload = resolve;
    script.onerror = () => { editorLoading = null; script.remove(); reject(new Error("The editor could not be loaded. Is the app still running?")); };
    document.head.append(script);
  });
  return editorLoading;
}
let toastTimer = null;
function toast(text, label, action) {
  document.querySelector(".nt-toast")?.remove(); clearTimeout(toastTimer);
  const box = el("div", { class: "nt-toast", role: "status" }, el("span", {}, text),
    label ? el("button", { type: "button", onclick: () => { box.remove(); action(); } }, label) : null);
  document.body.append(box);
  toastTimer = setTimeout(() => box.remove(), label ? 9000 : 4000);
}
const listeners = new Set();           // told when a note was made, saved, renamed or removed: fn(what, note)
const tell = (what, note) => { for (const fn of listeners) fn(what, note); };
const openNote = (id) => { if (Notes.open) Notes.open(id); else location.href = "/#/notes/" + encodeURIComponent(id); };
const cite = (job, block) => call("/vault/cite", { method: "POST", json: { job, block } });

/* ---------- a quick note, from any page ---------- */
let captureBox = null, captureDraft = "";
function capture(where) {
  if (captureBox && captureBox.open) return captureBox.querySelector("textarea").focus();
  const place = typeof where === "function" ? where() : where;
  const area = el("textarea", { "aria-label": "Quick note", placeholder: "A thought, before it goes." });
  area.value = captureDraft;
  const error = el("p", { class: "nt-err", hidden: true });
  const button = el("button", { type: "button", class: "primary", onclick: () => save() }, "Save to the inbox", el("kbd", {}, "ctrl ↵"));
  const box = captureBox = el("dialog", { class: "nt-capture", "aria-label": "Quick note" },
    el("header", {}, el("b", {}, "Quick note"), el("button", { type: "button", onclick: () => box.close() }, "Close")),
    el("div", { class: "in" }, area, error,
      el("div", { class: "acts" }, button, el("small", {}, place ? "It waits in the inbox of Notes, with its place in this book." : "It waits in the inbox of Notes."))));
  async function save() {
    const text = area.value.trim();
    if (!text) return box.close();
    button.disabled = true;
    try {
      const made = await call("/vault/notes", { method: "POST", json: { kind: "quick", body: text + "\n", where: place || undefined } });
      area.value = "";
      box.close();
      tell("made", made);
      toast("Quick note saved to the inbox.", "Open", () => openNote(made.id));
    } catch (e) { error.hidden = false; error.textContent = e.message; button.disabled = false; }
  }
  area.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.stopPropagation(); save(); } });
  box.addEventListener("click", (e) => { if (e.target === box) box.close(); });
  box.addEventListener("close", () => { captureDraft = area.value; box.remove(); captureBox = null; });   // what was typed is kept for the next time
  document.body.append(box);
  box.showModal();
  area.focus(); area.setSelectionRange(area.value.length, area.value.length);
}
function install({ where, button } = {}) {
  addEventListener("keydown", (e) => {
    if (e.key !== "." || !(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
    e.preventDefault(); e.stopPropagation();
    capture(where);
  }, true);
  if (button) button.addEventListener("click", () => capture(where));
}

/* ---------- one note ---------- */
// options: id, or draft { kind, body, sources: [link], cited: [source] } for a note that is made on its first save;
// compact (a narrow column: one column, sources above the text); close() adds a Close button; related: false leaves
// out the list of related notes;
// onOpen(id) another note is asked for; onPlace(source, event) a place in a book; onTag(tag); onGone(); onState(note).
function mount(root, options) {
  const opts = options || {};
  let note = null, props = [], editor = null, timer = null, saving = null, again = null, watching = null, asked = false, titled = false;
  let closed = false, dead = false;   // closed: the pane left the page, a last save may still be on its way; dead: nothing more happens
  const dirty = { body: false, props: false };
  const draft = opts.id ? null : (opts.draft || { kind: "idea", body: "" });
  const readOnly = () => !!note && note.kind === "book";

  const status = el("span", { class: "nt-status", role: "status" });
  const title = el("input", { type: "text", class: "nt-title", "aria-label": "Title", maxlength: "120", autocomplete: "off", spellcheck: "true" });
  const meta = el("div", { class: "nt-meta" }), notice = el("div", {}), propsBox = el("div", { class: "nt-props", hidden: true });
  // Beside the text: what the note cites, then what links to it and what is related. In a narrow column the
  // sources stand above the text and the rest below it.
  const edBox = el("div", { class: "nt-editor" });
  const top = el("div", { class: "nt-top" + (opts.compact ? " nt-aside" : "") }), more = el("div", { class: "nt-more" + (opts.compact ? " nt-aside" : "") });
  let related = null, relatedTurn = 0, outlineTimer = null;
  const buttons = el("span", { class: "row" });
  const shell = el("div", { class: "nt" + (opts.compact ? " compact" : "") },
    el("div", { class: "nt-head" }, status, buttons), title, meta, notice, propsBox,
    el("div", { class: "nt-cols" }, opts.compact ? [top, edBox, more] : [edBox, el("div", { class: "nt-aside" }, top, more)]));
  root.replaceChildren(shell);

  const say = (text) => { status.textContent = text; };
  const fail = (message) => { notice.replaceChildren(el("p", { class: "nt-err", role: "alert" }, message)); say("Not saved"); };
  const inbox = () => (note ? note.inbox : true);
  const kindNow = () => (prop("kind") || (note ? note.kind : draft.kind) || "idea");
  const prop = (key) => { const p = props.find((x) => x.key === key); return p && "value" in p ? p.value : undefined; };
  function setProp(key, value) {
    const p = props.find((x) => x.key === key);
    if (p) p.value = value; else props.push({ key, value });
    dirty.props = true; say("Not saved yet"); schedule();
  }

  /* -- what is shown around the text -- */
  function drawHead() {
    const book = readOnly(), kind = kindNow();
    shell.classList.toggle("book", book);
    title.readOnly = book;
    title.placeholder = inbox() ? "Give it a title" : "Title";
    buttons.replaceChildren(...[
      book ? null : el("button", { type: "button", class: "quiet", "aria-expanded": String(!propsBox.hidden), onclick: () => { propsBox.hidden = !propsBox.hidden; drawHead(); } }, "Properties"),
      book && note.job ? el("a", { class: "btn", href: "/read/" + note.job }, "Read") : null,
      book && note.job ? el("a", { class: "btn", href: "/#/notebook/" + note.job }, "Notebook") : null,
      note && !note.inbox && ["idea", "index", "quick", "project-note"].includes(kind) ? el("button", { type: "button", class: "quiet", onclick: pickProject }, "Add to a project") : null,
      note && (!book || !note.in_library) ? el("button", { type: "button", class: "quiet", onclick: remove }, "Delete") : null,
      opts.close ? el("button", { type: "button", onclick: () => opts.close() }, "Close") : null].filter(Boolean));
    const tags = note ? note.tags : [];
    meta.replaceChildren(...[
      el("span", { class: "lab" + (kind === "quick" ? " blue" : "") }, KINDS[kind] || kind),
      kind === "project" ? el("span", { class: "lab" + (prop("status") === "finished" ? "" : " blue") }, prop("status") === "finished" ? "Finished" : "Active") : null,
      note && note.of ? el("a", { class: "tag", onclick: () => go(note.of.id) }, "Project: " + note.of.title) : null,
      ...(note && note.gathered_for ? note.gathered_for.filter((g) => !note.of || g.id !== note.of.id).map((g) => el("a", { class: "tag", title: "This note was gathered for the project", onclick: () => go(g.id) }, "In " + g.title)) : []),
      note && note.inbox ? el("span", { class: "mute" }, "In the inbox") : null,
      ...tags.map((t) => el("a", { class: "tag", onclick: () => opts.onTag && leave().then(() => opts.onTag(t)) }, "#" + t)),
      note && note.created ? el("span", { class: "mute" }, "Made " + day(note.created)) : null,
      book && !note.in_library ? el("span", { class: "mute" }, "No longer in your library") : null].filter(Boolean));
  }
  function drawNotice() {
    if (asked) return;
    if (!note && draft && draft.kind !== "quick") return notice.replaceChildren();
    if (note && note.kind === "quick" && kindNow() === "quick") {
      return notice.replaceChildren(el("div", { class: "nt-note" },
        el("span", {}, "A quick note waits in the inbox. To keep it as an idea of your own, rewrite it, give it a title, and make it an idea note."),
        el("button", { type: "button", class: "primary", onclick: promote }, "Make this an idea note")));
    }
    notice.replaceChildren();
  }
  function drawProps() {
    const rows = [];
    const row = (label, control, extra) => el("div", { class: "pr-row" }, el("span", { class: "k" }, label), control, extra || el("span"));
    const kind = kindNow(), kinds = [["idea", KINDS.idea], ["index", KINDS.index], ["quick", KINDS.quick]];
    if (!kinds.some(([k]) => k === kind)) kinds.push([kind, KINDS[kind] || kind]);
    const select = el("select", { "aria-label": "Kind", onchange: () => { setProp("kind", select.value); drawHead(); drawNotice(); } }, kinds.map(([k, t]) => el("option", { value: k }, t)));
    select.value = kind;
    rows.push(row("Kind", select));
    const own = prop("tags");
    const tags = el("input", { type: "text", "aria-label": "Tags", placeholder: "pricing, markets", value: Array.isArray(own) ? own.join(", ") : typeof own === "string" ? own : "" });
    tags.addEventListener("change", () => setProp("tags", tags.value.split(/[,\s]+/).map((t) => t.replace(/^#/, "").trim()).filter(Boolean)));
    rows.push(row("Tags", tags, el("small", {}, "A #tag in the text counts too")));
    for (const p of props) {
      if (!p.key || hiddenFor(kind).includes(p.key)) continue;
      const drop = el("button", { type: "button", class: "quiet", "aria-label": `Remove ${p.key}`, onclick: () => { props = props.filter((x) => x !== p); dirty.props = true; schedule(); drawProps(); } }, "Remove");
      if (!("value" in p)) { rows.push(row(p.key, el("code", {}, p.raw), el("small", {}, "kept as written"))); continue; }
      const list = Array.isArray(p.value);
      const field = el("input", { type: "text", "aria-label": p.key, value: list ? p.value.join(", ") : p.value == null ? "" : String(p.value) });
      field.addEventListener("change", () => {
        const v = field.value.trim();
        setProp(p.key, list ? v.split(",").map((x) => x.trim()).filter(Boolean) : v === "" ? null : v === "true" ? true : v === "false" ? false
          : /^-?\d+(\.\d+)?$/.test(v) && String(Number(v)) === v ? Number(v) : v);
      });
      rows.push(row(p.key, field, drop));
    }
    const name = el("input", { type: "text", "aria-label": "Name of a new property", placeholder: "name" });
    const value = el("input", { type: "text", "aria-label": "Value of the new property", placeholder: "value" });
    const add = () => {
      const k = name.value.trim();
      if (!k) return name.focus();
      if (!/^[\p{L}\p{N}_][^:#\n]*$/u.test(k) || hiddenFor(kind).includes(k) || props.some((p) => p.key === k)) { name.select(); return toast(`“${k}” cannot be used as the name of a new property.`); }
      setProp(k, value.value.trim()); drawProps();
    };
    value.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    propsBox.replaceChildren(...rows, el("div", { class: "add" }, name, value, el("button", { type: "button", onclick: add }, "Add a property")));
  }
  function drawAside() {
    const parts = [];
    const sources = note ? note.sources : (draft && draft.cited) || [];
    if (sources.length) {
      parts.push(el("section", { class: "nt-sources" }, el("h3", {}, sources.length === 1 ? "Source" : "Sources"), sources.map((s) => el("div", { class: "src" },
        s.found === false ? el("p", { class: "none" }, "This passage is no longer in the book's file.") : el("blockquote", {}, s.text),
        el("div", { class: "by" }, el("span", {}, s.title),
          s.job && s.go ? el("a", { href: `/read/${s.job}?go=${s.go.chapter}:${s.go.word}`, onclick: (e) => { if (opts.onPlace) opts.onPlace(s, e); } }, "Open in the book")
            : s.in_library === false ? el("span", {}, "No longer in your library") : null)))));
    }
    if (note && note.written) {
      const w = note.written;
      parts.push(el("section", { class: "nt-written" }, el("h3", {}, "Written while reading"), el("div", { class: "src" }, el("div", { class: "by", style: "margin-top:0" }, el("span", {}, w.title),
        w.job && w.go ? el("a", { href: `/read/${w.job}?go=${w.go.chapter}:${w.go.word}`, onclick: (e) => { if (opts.onPlace) opts.onPlace(w, e); } }, "Open the place")
          : w.in_library === false ? el("span", {}, "No longer in your library") : null))));
    }
    if (note && note.kind === "project" && editor) parts.unshift(...projectParts());
    top.replaceChildren(...parts);
    parts.length = 0;
    if (note && (note.backlinks.length || !opts.compact)) {
      parts.push(el("section", { class: "nt-back" }, el("h3", {}, note.kind === "book" ? "Notes that cite this book" : "Linked from"),
        note.backlinks.length ? note.backlinks.map((b) => el("a", { class: "back", onclick: () => go(b.id) },
          el("b", {}, b.untitled ? "A note without a title" : b.title), el("span", {}, b.context)))
          : el("p", { class: "none" }, note.kind === "book" ? "No note cites this book yet." : "No other note links here yet.")));
    }
    if (related && related.notes.length + related.passages.length && !readOnly()) {
      const shares = (r) => (r.shares.length ? "shares: " + r.shares.join(", ") : "shares a tag");
      parts.push(el("section", { class: "nt-related" }, el("h3", { title: "Found on this computer, by the uncommon words and the tags they share with this note. Nothing is sent anywhere." }, "Related"),
        related.notes.map((r) => el("div", { class: "rel" },
          el("a", { class: "back", onclick: () => go(r.id) }, el("b", {}, r.untitled ? r.excerpt.split(/\s+/).slice(0, 10).join(" ") : r.title), el("span", {}, shares(r))),
          el("button", { type: "button", class: "quiet", title: "Put a link to this note where the cursor is", onclick: () => editor && editor.insert(`[[${r.link}]]`) }, "Link"))),
        related.passages.map((r) => el("div", { class: "rel passage" }, el("blockquote", {}, r.text),
          el("div", { class: "by" }, el("span", {}, r.book + (r.heading ? " / " + r.heading : "")),
            el("button", { type: "button", class: "quiet", title: "Cite this passage where the cursor is", onclick: () => editor && editor.insert(`[[${r.book}#^${r.block}]]`) }, "Cite"),
            r.job && r.go ? el("a", { href: `/read/${r.job}?go=${r.go.chapter}:${r.go.word}`, onclick: (e) => { if (opts.onPlace) opts.onPlace(r, e); } }, "Open in the book") : null)))));
    }
    more.replaceChildren(...parts);
  }
  // What else in the folder is about the same thing: asked when the note opens and after each save.
  async function findRelated() {
    if (closed || !editor || readOnly() || opts.related === false || (note && note.kind === "project")) return;
    const mine = ++relatedTurn, sources = note ? note.sources : (draft && draft.cited) || [];
    const text = [title.value, editor.getText(), ...sources.map((x) => x.text || "")].join("\n");
    try {
      const got = await call("/vault/related", { method: "POST", json: { text, tags: note ? note.tags : [],
        but: [note ? note.id : "", ...sources.map((x) => `${x.id}#${x.block}`)].filter(Boolean) } });
      if (mine === relatedTurn && !closed) { related = got; drawAside(); }
    } catch (e) { /* the list is a help, not a need */ }
  }
  // A project's page: its state, what it gathered, its outline to arrange, and the notes written for it.
  function projectParts() {
    const text = editor.getText(), items = outlineOf(text), gathered = gatheredOf(text), finished = prop("status") === "finished";
    const put = (next) => { editor.rewrite(withOutline(editor.getText(), next)); drawAside(); };
    const inOutline = new Set(items.map((i) => i.text.toLowerCase()));
    const move = (n, to) => {           // an entry with what is under it, to stand before entry `to`
      const end = blockEnd(items, n), block = items.slice(n, end), shift = (to < items.length ? items[to].depth : 0) - items[n].depth;
      if (to >= n && to <= end) return;
      const rest = [...items.slice(0, n), ...items.slice(end)], at = to > n ? to - block.length : to;
      rest.splice(at, 0, ...block.map((b) => ({ ...b, depth: Math.max(0, b.depth + shift) })));
      put(rest);
    };
    const up = (n) => { let m = n - 1; while (m >= 0 && items[m].depth > items[n].depth) m--; if (m >= 0 && items[m].depth === items[n].depth) move(n, m); };
    const down = (n) => { const end = blockEnd(items, n); if (end < items.length && items[end].depth === items[n].depth) { const after = blockEnd(items, end); const block = items.slice(n, end); put([...items.slice(0, n), ...items.slice(end, after), ...block, ...items.slice(after)]); } };
    const shift = (n, by) => { if (by > 0 && (n === 0 || items[n - 1].depth < items[n].depth)) return; const end = blockEnd(items, n); put(items.map((i, k) => (k >= n && k < end ? { ...i, depth: Math.max(0, i.depth + by) } : i))); };
    const drop = (n) => { const end = blockEnd(items, n); put(items.map((i, k) => (k > n && k < end ? { ...i, depth: i.depth - 1 } : i)).filter((_, k) => k !== n)); };   // what was under it moves out a level
    const act = (label, title, fn) => el("button", { type: "button", title, "aria-label": title, onclick: fn }, label);
    let dragged = null;
    const rows = items.map((i, n) => {
      const parent = n + 1 < items.length && items[n + 1].depth > i.depth;
      const row = el("div", { class: "out" + (parent ? " head" : ""), style: `--d:${i.depth}`, draggable: "true" }, el("span", {}, asWords(i.text)),
        el("span", { class: "acts" }, act("↑", "Move up", () => up(n)), act("↓", "Move down", () => down(n)), act("←", "Move out a level", () => shift(n, -1)),
          act("→", "Move in, under the entry above", () => shift(n, 1)), act("×", "Take out of the outline", () => drop(n))));
      row.addEventListener("dragstart", (e) => { dragged = n; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", i.text); });
      row.addEventListener("dragover", (e) => { if (dragged !== null) { e.preventDefault(); row.classList.add("over"); } });
      row.addEventListener("dragleave", () => row.classList.remove("over"));
      row.addEventListener("drop", (e) => { e.preventDefault(); row.classList.remove("over"); const from = dragged; dragged = null; if (from !== null) move(from, n); });
      return row;
    });
    const line = el("input", { type: "text", "aria-label": "A heading or a line of your own for the outline", placeholder: "A heading, or a line of your own" });
    const add = () => { const words = line.value.trim(); if (!words) return line.focus(); put([...items, { depth: 0, text: words }]); };
    line.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    return [
      el("section", { class: "nt-project" }, el("h3", {}, "Project"),
        el("div", { class: "rowline" },
          el("button", { type: "button", onclick: () => { setProp("status", finished ? "active" : "finished"); drawHead(); drawAside(); } }, finished ? "Reopen the project" : "Mark as finished"),
          el("button", { type: "button", title: "The outline as one Markdown file, with the text of the notes it names", onclick: async () => { await leave(); location.href = `/api/vault/notes/${encodeURIComponent(note.id)}/export.md`; } }, "Export the outline")),
        finished ? el("p", { class: "none" }, "A finished project is put away: it is not offered when a note is added to a project.") : null),
      el("section", { class: "nt-gathered" }, el("h3", {}, `Gathered${gathered.length ? " " + gathered.length : ""}`),
        gathered.length ? gathered.map((g) => el("div", { class: "rel" }, el("a", { class: "back", onclick: () => follow({ target: g.raw.slice(2, -2).split(/[#|]/)[0], anchor: (g.raw.slice(2, -2).split("|")[0].split("#")[1] || "") }) }, el("b", {}, g.shown)),
          inOutline.has(g.raw.toLowerCase()) ? el("small", { style: "padding:10px 0" }, "in the outline") : el("button", { type: "button", class: "quiet", onclick: () => put([...items, { depth: 0, text: g.raw }]) }, "To the outline")))
          : el("p", { class: "none" }, "Nothing yet. “Add to a project” on a note’s page gathers it here; a passage of a book is gathered by writing its link under Gathered.")),
      el("section", { class: "nt-outline" }, el("h3", {}, "Outline"), rows.length ? rows : el("p", { class: "none" }, "Put what you gathered in order, under headings of your own."),
        el("div", { class: "addline" }, line, el("button", { type: "button", onclick: add }, "Add"))),
      el("section", { class: "nt-pnotes" }, el("h3", {}, "Notes of this project"),
        note.project && note.project.notes.length ? note.project.notes.map((n) => el("a", { class: "back", onclick: () => go(n.id) }, el("b", {}, n.title), el("span", {}, n.excerpt.slice(0, 120))))
          : el("p", { class: "none" }, "Drafts, lists and minutes that belong to this project only."),
        el("div", { style: "margin-top:10px" }, el("button", { type: "button", onclick: async () => { await leave(); if (opts.onNew) opts.onNew({ kind: "project-note", project: note.id }); } }, "New project note")))];
  }
  // "Add to a project": the note is gathered by a link in the project's file; the note itself is not changed.
  async function pickProject() {
    let rows;
    try { rows = (await call("/vault/projects")).projects.filter((p) => p.status === "active"); } catch (e) { return toast(e.message); }
    if (!rows.length) return toast("You have no active project. Make one on the Notes page, under Projects.");
    const select = el("select", { "aria-label": "Project" }, rows.map((p) => el("option", { value: p.id }, p.title)));
    const done = () => { asked = false; notice.replaceChildren(); drawNotice(); };
    asked = true;
    notice.replaceChildren(el("div", { class: "nt-note nt-pick" }, el("span", {}, "Gather this note for which project?"), select,
      el("button", { type: "button", class: "primary", onclick: async () => {
        await save();
        try {
          const got = await call(`/vault/projects/${encodeURIComponent(select.value)}/gather`, { method: "POST", json: { note: note.id } });
          toast(got.added ? `Gathered for “${got.title}”.` : `“${got.title}” already has this note.`, "Open the project", () => go(got.id));
          take(await call("/vault/notes/" + encodeURIComponent(note.id)));
        } catch (e) { toast(e.message); }
        done();
      } }, "Add"), el("button", { type: "button", onclick: done }, "Cancel")));
  }
  function drawAll() { drawHead(); drawNotice(); drawProps(); drawAside(); }

  /* -- taking the note from the app -- */
  function take(got, text) {
    const renamed = note && note.title !== got.title, first = !note || note.id !== got.id;
    note = got;
    if (!dirty.props) props = got.props.map((p) => ({ ...p }));
    if (document.activeElement !== title && !titled) title.value = got.untitled ? "" : got.title;
    if (text !== undefined && editor && editor.getText() !== text) editor.setText(text);
    if (renamed && editor) editor.forgetLinks();
    drawAll();
    if (opts.onState && !closed) opts.onState(note);
  }
  async function load() {
    say("Loading");
    try {
      if (opts.id) take(await call("/vault/notes/" + encodeURIComponent(opts.id)));
      else { props = [{ key: "kind", value: draft.kind || "idea" }]; drawAll(); }
      await loadEditor();
    } catch (e) {
      if (dead) return;
      shell.replaceChildren(el("p", { class: "nt-err", role: "alert" }, e.message), opts.close ? el("p", {}, el("button", { type: "button", onclick: () => opts.close() }, "Close")) : null);
      if (e.status === 404 && opts.onMissing) opts.onMissing();
      return;
    }
    if (dead) return;
    editor = window.NoteEditor.create(edBox, {
      doc: note ? note.body : draft.body || "", label: "Note text", readOnly: readOnly(),
      placeholder: draft && draft.kind === "quick" ? "A thought, before it goes." : kindNow() === "index" ? "Gather the notes on a subject: type [[ to link one, and say why it belongs."
        : kindNow() === "project-note" ? "A draft, a list, minutes: what belongs to this project only." : "One idea, in your own words. Type [[ to link to another note.",
      onChange: () => { dirty.body = true; say("Not saved yet"); schedule(); if (note && note.kind === "project") { clearTimeout(outlineTimer); outlineTimer = setTimeout(() => { if (!closed) drawAside(); }, 400); } },
      onSave: () => save(),
      onOpen: follow,
      onTag: (tag) => opts.onTag && leave().then(() => opts.onTag(tag)),
      complete: ({ q }) => call("/vault/complete?" + query({ q })).then((r) => r.notes),
      anchors: ({ target }) => call("/vault/anchors?" + query({ target })),
      peek: ({ target, anchor }) => call("/vault/peek?" + query({ target, anchor, from: note ? note.id : "" })),
      embed: ({ target, anchor }) => call("/vault/peek?" + query({ target, anchor, from: note ? note.id : "", full: "1" })),
    });
    if (note && note.kind === "project") drawAside();
    say(note ? "Saved" : "");
    findRelated();
    watching = setInterval(look, 4000);
    addEventListener("focus", look);
    if (opts.focus !== false && !readOnly()) editor.focus(draft ? "start" : undefined);
  }

  /* -- saving -- */
  function schedule() { clearTimeout(timer); timer = setTimeout(() => save(), 1200); }
  function payload(extra) {
    const sent = extra.kind ? [...props.filter((p) => p.key !== "kind"), { key: "kind", value: extra.kind }] : props;
    return { body: editor.getText(), props: dirty.props || extra.kind ? sent : undefined, title: extra.title, force: extra.force };
  }
  function save(extra) {
    extra = extra || {};
    if (!editor || readOnly()) return Promise.resolve(note);
    if (saving) { again = { ...(again || {}), ...extra }; return saving; }
    clearTimeout(timer);
    if (!note && !extra.title && !title.value.trim() && !editor.getText().trim().replace(/^Source: \[\[[^\]]*\]\]$/, "")) return Promise.resolve(null);   // nothing written yet: no file is made
    const sent = payload(extra), wasProps = JSON.stringify(props);
    say("Saving");
    saving = (async () => {
      try {
        let got;
        if (!note) {
          got = await call("/vault/notes", { method: "POST", keepalive: !!extra.leaving, json: { title: extra.title || title.value.trim(), body: sent.body, kind: extra.kind || kindNow(),
            sources: draft.sources, tags: prop("tags"), project: draft.project } });
          tell("made", got);
        } else {
          got = await call("/vault/notes/" + encodeURIComponent(note.id), { method: "PUT", keepalive: !!extra.leaving,
            json: { body: dirty.body || extra.force ? sent.body : undefined, props: sent.props, title: sent.title, token: note.token, force: extra.force } });
          tell("saved", got);
        }
        if (dead) return got;
        if (editor.getText() === sent.body) dirty.body = false;
        if (JSON.stringify(props) === wasProps) dirty.props = false;
        asked = false;
        notice.replaceChildren();
        take(got, editor.getText() === sent.body ? got.body : undefined);
        say(dirty.body || dirty.props ? "Not saved yet" : "Saved");
        if (dirty.body || dirty.props) schedule(); else findRelated();
        return got;
      } catch (e) {
        if (dead || closed) return null;
        if (e.status === 409 && e.data && e.data.current) ask(e.data.current); else fail(e.message);
        return null;
      } finally {
        saving = null;
        if (again && !dead) { const next = again; again = null; save(next); }
      }
    })();
    return saving;
  }
  const unsaved = () => dirty.body || dirty.props;
  async function leave() { if (unsaved() && !asked) await save(); }

  /* -- the file changed in another program -- */
  async function look() {
    if (dead || closed || !note || saving || asked || document.hidden) return;
    let now;
    try { now = (await call(`/vault/notes/${encodeURIComponent(note.id)}/token`)).token; }
    catch (e) { if (e.status === 404 && !dead) { clearInterval(watching); fail("This note is no longer in the notes folder. It may have been renamed or removed in another program."); } return; }
    if (dead || saving || now === note.token) return;
    let current;
    try { current = await call("/vault/notes/" + encodeURIComponent(note.id)); } catch (e) { return; }
    if (dead || saving) return;
    if (unsaved()) ask(current); else { take(current, current.body); say("Saved"); }
  }
  function ask(current) {              // both changed: the person says which stays
    asked = true; clearTimeout(timer);
    say("Not saved");
    notice.replaceChildren(el("div", { class: "nt-note", role: "alert" },
      el("span", {}, "This note was changed outside the app while you were writing here. Which version do you want to keep?"),
      el("button", { type: "button", class: "primary", onclick: () => { asked = false; note.token = current.token; dirty.body = true; save({ force: true }); } }, "Keep what I have here"),
      el("button", { type: "button", onclick: () => { asked = false; dirty.body = dirty.props = false; notice.replaceChildren(); take(current, current.body); say("Saved"); } }, "Take the other version")));
  }

  /* -- title, kind, delete -- */
  title.addEventListener("input", () => { titled = true; });
  async function rename(meant) {       // meant: Enter was pressed; leaving the field counts only when the title was typed in
    const wanted = title.value.trim().replace(/\s+/g, " ");
    if (!editor || readOnly() || !wanted || !(titled || meant) || (note && wanted === note.title && !note.inbox)) return;
    if (note && note.kind === "quick" && kindNow() === "quick") return;   // a quick note gets its title when it is made an idea note
    titled = false;
    const got = await save({ title: wanted });
    if (got && got.relinked) toast(`Renamed. ${got.relinked} note${got.relinked === 1 ? "" : "s"} that link${got.relinked === 1 ? "s" : ""} here ${got.relinked === 1 ? "was" : "were"} updated.`);
  }
  title.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (note && note.kind === "quick" && kindNow() === "quick") return promote();
    if (editor) editor.focus();        // at once: what is typed next belongs to the text
    rename(true);
  });
  title.addEventListener("blur", () => { rename(); });
  async function promote() {
    const wanted = title.value.trim();
    if (!wanted) { title.focus(); return toast("Give the note a title first: it becomes the name of its file."); }
    const got = await save({ title: wanted, kind: "idea" });
    if (got) { props = got.props.map((p) => ({ ...p })); drawAll(); toast("It is an idea note now, out of the inbox."); }
  }
  async function remove() {
    if (!note || !confirm(note.kind === "book" ? "Move this book's file to the trash?" : "Move this note to the trash? It can be put back from there.")) return;
    clearTimeout(timer); dirty.body = dirty.props = false;
    try { const gone = await call("/vault/notes/" + encodeURIComponent(note.id), { method: "DELETE" }); tell("removed", gone); }
    catch (e) { return fail(e.message); }
    if (opts.onGone) opts.onGone(note);
  }

  /* -- going elsewhere -- */
  async function go(id) { await leave(); if (opts.onOpen) opts.onOpen(id); else openNote(id); }
  async function follow(link) {
    let to;
    try { to = await call("/vault/peek?" + query({ target: link.target, anchor: link.anchor, from: note ? note.id : "" })); } catch (e) { return toast(e.message); }
    if (to.id && to.kind === "book" && to.job && to.go) {
      await leave();
      if (opts.onPlace) return opts.onPlace({ ...to, job: to.job, go: to.go }, null);
      return void (location.href = `/read/${to.job}?go=${to.go.chapter}:${to.go.word}`);
    }
    if (to.id) return note && to.id === note.id ? null : go(to.id);
    if (!link.target) return null;
    if (!confirm(`There is no note called “${link.target}” yet. Make it?`)) return null;
    await leave();
    try { const made = await call("/vault/notes", { method: "POST", json: { title: link.target, body: "" } }); tell("made", made); if (editor) editor.forgetLinks(); go(made.id); }
    catch (e) { toast(e.message); }
    return null;
  }

  load();
  return {
    get note() { return note; },
    unsaved,
    save, leave,
    focus() { if (editor) editor.focus(); },
    reload: look,
    destroy(leaving) {                 // what was typed is still sent, after a save that is on its way; then nothing more happens
      closed = true; clearTimeout(timer); clearInterval(watching); removeEventListener("focus", look);
      const last = () => (unsaved() && !asked && editor ? save({ leaving: !!leaving }) : null);
      Promise.resolve(saving).then(last, last).then(() => { dead = true; if (editor) editor.destroy(); editor = null; });
    },
  };
}

const Notes = window.Notes = { KINDS, capture, install, mount, loadEditor, toast, cite, call, day, open: null,
  onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); } };
})();
