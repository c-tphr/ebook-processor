// The note editor of ebook-processor: one pane of Markdown that is formatted while it is typed.
//
// It is CodeMirror 6 with the Markdown language, plus what a notes folder needs: wiki links ([[Note]],
// [[Note|shown]], [[Note#Heading]], [[Note#^block]]), tags and block ids are understood, the marks of a construct
// are hidden unless the cursor is in it, links complete as they are typed, and a link shows what it leads to.
// Tables are drawn as tables, an embed (![[Note]]) shows its text under its line, callouts (> [!note]) are
// labelled, footnotes ([^1]) are raised, and the box of a task (- [ ]) can be ticked.
// The page around it gives the editor what only the app knows (see `create`).
//
// Built into ebook_processor/web/static/editor.js by `npm run build` in this folder. The built file is committed.

import { EditorSelection, EditorState, Annotation, StateEffect, StateField, Prec } from "@codemirror/state";
import { EditorView, keymap, Decoration, ViewPlugin, WidgetType, placeholder, hoverTooltip } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { syntaxTree, Language, LanguageSupport } from "@codemirror/language";
import { markdownLanguage, markdownKeymap, pasteURLAsLink } from "@codemirror/lang-markdown";
import { autocompletion, completionKeymap, startCompletion } from "@codemirror/autocomplete";

/* ---------- what Markdown does not know: wiki links, tags, block ids ---------- */

const WORD = "\\p{L}\\p{N}_";
const WIKI = /^([^\[\]|#\n]*)(#[^\[\]|\n]*)?(\|[^\[\]\n]*)?\]\]/;   // what follows "[[" in a link; it follows LINK_RE of vault.py
const TAG = new RegExp(`^#[${WORD}/-]*[\\p{L}_][${WORD}/-]*`, "u");
const TAG_BEFORE = new RegExp(`[${WORD}/#&]`, "u");
const BLOCK_ID = /^\^[A-Za-z0-9-]+[ \t]*(?=\n|$)/;

const notesSyntax = {
  defineNodes: ["WikiLink", "WikiMark", "WikiTarget", "WikiAnchor", "WikiShown", "Tag", "BlockId", "Footnote"],
  parseInline: [
    {
      name: "Footnote",                 // [^1] in the text, and [^1]: at the start of the line that says what it is
      before: "Link",
      parse(cx, next, pos) {
        if (next !== 91 || cx.char(pos + 1) !== 94) return -1;
        const m = /^\[\^[^\]\s]+\]/.exec(cx.slice(pos, Math.min(cx.end, pos + 60)));
        return m ? cx.addElement(cx.elt("Footnote", pos, pos + m[0].length)) : -1;
      },
    },
    {
      name: "WikiLink",
      before: "Link",
      parse(cx, next, pos) {
        let open = pos;
        if (next === 33 /* ! */) open = pos + 1;      // an embed is a link with a ! before it
        if (cx.char(open) !== 91 || cx.char(open + 1) !== 91) return -1;
        const m = WIKI.exec(cx.slice(open + 2, cx.end));
        if (!m) return -1;
        const kids = [cx.elt("WikiMark", pos, open + 2)];
        let at = open + 2;
        if (m[1]) kids.push(cx.elt("WikiTarget", at, at + m[1].length));
        at += m[1].length;
        if (m[2]) { kids.push(cx.elt("WikiAnchor", at, at + m[2].length)); at += m[2].length; }
        if (m[3]) { kids.push(cx.elt("WikiShown", at, at + m[3].length)); at += m[3].length; }
        kids.push(cx.elt("WikiMark", at, at + 2));
        return cx.addElement(cx.elt("WikiLink", pos, at + 2, kids));
      },
    },
    {
      name: "Tag",
      parse(cx, next, pos) {
        if (next !== 35 /* # */) return -1;
        if (pos > cx.offset && TAG_BEFORE.test(cx.slice(pos - 1, pos))) return -1;
        const m = TAG.exec(cx.slice(pos, Math.min(cx.end, pos + 80)));
        return m ? cx.addElement(cx.elt("Tag", pos, pos + m[0].length)) : -1;
      },
    },
    {
      name: "BlockId",
      parse(cx, next, pos) {
        if (next !== 94 /* ^ */) return -1;
        if (pos > cx.offset && !/\s/.test(cx.slice(pos - 1, pos))) return -1;
        const m = BLOCK_ID.exec(cx.slice(pos, cx.end));
        return m ? cx.addElement(cx.elt("BlockId", pos, pos + m[0].trimEnd().length)) : -1;
      },
    },
  ],
};

// Markdown as GitHub writes it, with the three additions. Built here rather than with the package's markdown(),
// which would bring the HTML, CSS and JavaScript languages with it for the HTML inside Markdown.
const notesLanguage = new Language(markdownLanguage.data, markdownLanguage.parser.configure(notesSyntax), [], "markdown");

/* ---------- formatting as you type ---------- */

class Tick extends WidgetType {       // the box of a task: ticking it writes the x into the text
  constructor(done) { super(); this.done = done; }
  eq(other) { return other.done === this.done; }
  toDOM(view) {
    const box = document.createElement("input");
    box.type = "checkbox"; box.className = "cm-tick"; box.checked = this.done; box.setAttribute("aria-label", "Done");
    box.addEventListener("mousedown", (e) => e.preventDefault());
    box.addEventListener("click", (e) => {
      e.preventDefault();
      const pos = view.posAtDOM(box);
      if (/^\[[ xX]\]$/.test(view.state.sliceDoc(pos, pos + 3))) view.dispatch({ changes: { from: pos + 1, to: pos + 2, insert: this.done ? " " : "x" }, userEvent: "input" });
    });
    return box;
  }
  ignoreEvent() { return true; }
}

/* ---------- tables and embeds: drawn as blocks ---------- */
// A cell as words. In a table a bar inside a link is written \\|, as Markdown asks.
const plainCell = (text) => text.replace(/\\\|/g, "|").replace(/!?\[\[([^\]|#]*)(#[^\]|]*)?(?:\|([^\]]*))?\]\]/g, (_m, to, _part, shown) => shown || to).trim();
const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/);

class TableBlock extends WidgetType {   // a Markdown table as a table; a click in it goes back to its text
  constructor(source) { super(); this.source = source; }
  eq(other) { return other.source === this.source; }
  toDOM(view) {
    const rows = this.source.split("\n").filter((line) => line.trim()), table = document.createElement("table");
    const align = cells(rows[1] || "").map((c) => (/^\s*:-+:\s*$/.test(c) ? "center" : /-+:\s*$/.test(c) ? "right" : ""));
    table.className = "cm-table";
    rows.forEach((row, n) => {
      if (n === 1) return;
      const tr = table.insertRow();
      cells(row).forEach((c, i) => {
        const cell = n === 0 ? document.createElement("th") : document.createElement("td");
        cell.textContent = plainCell(c);
        if (align[i]) cell.style.textAlign = align[i];
        tr.append(cell);
      });
    });
    const wrap = document.createElement("div");
    wrap.className = "cm-table-wrap"; wrap.title = "Click to edit the table as text"; wrap.append(table);
    wrap.addEventListener("mousedown", (e) => { e.preventDefault(); view.dispatch({ selection: { anchor: view.posAtDOM(wrap) }, scrollIntoView: true }); view.focus(); });
    return wrap;
  }
  ignoreEvent() { return true; }
}

class EmbedBlock extends WidgetType {   // what ![[Note]] embeds, shown under its line
  constructor(link, got, open) { super(); this.link = link; this.got = got; this.open = open; }
  eq(other) { return other.link.target === this.link.target && other.link.anchor === this.link.anchor && other.got === this.got; }
  toDOM() {
    const box = document.createElement("div"), head = document.createElement("b"), got = this.got;
    box.className = "cm-embed";
    const part = this.link.anchor && !this.link.anchor.startsWith("^") ? " › " + this.link.anchor : "";
    head.textContent = !got ? "…" : got.found ? (got.title || "") + part : got.title ? `Nothing called ${this.link.anchor.replace(/^\^/, "")} in ${got.title}` : `No note called ${this.link.target} yet`;
    box.append(head);
    if (got && got.found && got.body) { const text = document.createElement("div"); text.textContent = got.body; box.append(text); }
    if (got && got.found && this.open) { head.classList.add("cm-embed-open"); head.addEventListener("mousedown", (e) => { e.preventDefault(); this.open(this.link); }); }
    return box;
  }
  ignoreEvent() { return true; }
}

// Whether the keyboard is in the editor, kept in the state so that what is drawn as a block can follow it: a
// table shows as text only while the cursor is in it and the editor has the keyboard.
const setFocus = StateEffect.define();
const focusField = StateField.define({ create: () => false, update: (value, tr) => tr.effects.reduce((v, e) => (e.is(setFocus) ? e.value : v), value) });
const focusWatch = EditorView.focusChangeEffect.of((_state, focusing) => setFocus.of(focusing));

const blocks = (embeds, opts) => {
  const build = (state) => {
    const decos = [], focused = state.field(focusField, false);
    const touched = (from, to) => focused && state.selection.ranges.some((r) => r.from <= to && r.to >= from);
    syntaxTree(state).iterate({
      enter(ref) {
        if (ref.name === "Table") {
          const from = state.doc.lineAt(ref.from).from, to = state.doc.lineAt(ref.to).to;
          if (!touched(from, to)) decos.push(Decoration.replace({ widget: new TableBlock(state.sliceDoc(from, to)), block: true }).range(from, to));
          return false;
        }
        if (ref.name === "FencedCode" || ref.name === "CodeBlock") return false;
        if (ref.name === "WikiLink" && state.sliceDoc(ref.from, ref.from + 1) === "!") {
          const link = wikiAt(state, ref.from);
          if (link) decos.push(Decoration.widget({ widget: new EmbedBlock(link, embeds.get(link.target, link.anchor), opts.onOpen), block: true, side: 1 }).range(state.doc.lineAt(ref.to).to));
          return false;
        }
        return undefined;
      },
    });
    return Decoration.set(decos, true);
  };
  return StateField.define({
    create: build,
    update(value, tr) {
      return tr.docChanged || tr.selection || tr.effects.some((e) => e.is(redraw) || e.is(setFocus)) || syntaxTree(tr.startState) !== syntaxTree(tr.state) ? build(tr.state) : value;
    },
    provide: (field) => EditorView.decorations.from(field),
  });
};

class Glyph extends WidgetType {
  constructor(text, cls) { super(); this.text = text; this.cls = cls; }
  eq(other) { return other.text === this.text && other.cls === this.cls; }
  toDOM() { const el = document.createElement("span"); el.className = this.cls; el.textContent = this.text; el.setAttribute("aria-hidden", "true"); return el; }
  ignoreEvent() { return false; }
}

const redraw = StateEffect.define();
const fromOutside = Annotation.define();
const hidden = Decoration.replace({});

function format(view, links) {
  const { state } = view, decos = [], focused = view.hasFocus;
  const inSel = (from, to) => focused && state.selection.ranges.some((r) => r.from <= to && r.to >= from);
  const hide = (from, to) => { if (to > from) decos.push(hidden.range(from, to)); };
  const mark = (cls, from, to, attributes) => { if (to > from) decos.push(Decoration.mark({ class: cls, attributes }).range(from, to)); };
  const lines = (cls, from, to) => {
    for (let line = state.doc.lineAt(from); ; line = state.doc.line(line.number + 1)) {
      decos.push(Decoration.line({ class: cls }).range(line.from));
      if (line.to >= to || line.number >= state.doc.lines) break;
    }
  };
  const text = (node) => (node ? state.sliceDoc(node.from, node.to) : "");
  for (const { from, to } of view.visibleRanges) {
    syntaxTree(state).iterate({
      from, to,
      enter(ref) {
        const name = ref.name, node = ref.node;
        const heading = /^ATXHeading(\d)$/.exec(name);
        if (heading) {
          const line = state.doc.lineAt(ref.from);
          decos.push(Decoration.line({ class: "cm-h cm-h" + heading[1] }).range(line.from));
          const sign = node.getChild("HeaderMark");
          if (sign && !inSel(line.from, line.to)) {
            let end = sign.to;
            while (end < ref.to && /[ \t]/.test(state.sliceDoc(end, end + 1))) end++;
            if (end < ref.to) hide(sign.from, end);      // a heading with no words yet keeps its mark in view
          }
        } else if (name === "Emphasis" || name === "StrongEmphasis" || name === "Strikethrough") {
          mark(name === "Emphasis" ? "cm-em" : name === "StrongEmphasis" ? "cm-strong" : "cm-strike", ref.from, ref.to);
          if (!inSel(ref.from, ref.to)) for (let kid = node.firstChild; kid; kid = kid.nextSibling) if (/Mark$/.test(kid.name)) hide(kid.from, kid.to);
        } else if (name === "InlineCode") {
          mark("cm-code", ref.from, ref.to);
          if (!inSel(ref.from, ref.to)) for (const kid of node.getChildren("CodeMark")) hide(kid.from, kid.to);
        } else if (name === "FencedCode" || name === "CodeBlock") {
          lines("cm-codeblock", ref.from, ref.to);
          for (let kid = node.firstChild; kid; kid = kid.nextSibling) if (kid.name === "CodeMark" || kid.name === "CodeInfo") mark("cm-mute", kid.from, kid.to);
          return false;
        } else if (name === "Blockquote") {
          const first = state.doc.lineAt(ref.from), callout = /^(\s*>\s*)\[!([A-Za-z]+)\][+-]?[ \t]*/.exec(first.text);
          lines(callout ? "cm-quote cm-callout" : "cm-quote", ref.from, ref.to);
          if (callout) {                // > [!note] Title: a labelled box, as Obsidian draws it
            const from = first.from + callout[1].length, to = first.from + callout[0].length, label = callout[2][0].toUpperCase() + callout[2].slice(1).toLowerCase();
            if (!inSel(first.from, first.to)) decos.push(Decoration.replace({ widget: new Glyph(label + (to < first.to ? ": " : ""), "cm-callout-label") }).range(from, to));
            else mark("cm-mute", from, to);
          }
        } else if (name === "QuoteMark") {
          const line = state.doc.lineAt(ref.from);
          if (!inSel(line.from, line.to)) hide(ref.from, state.sliceDoc(ref.to, ref.to + 1) === " " ? ref.to + 1 : ref.to);
          else mark("cm-mute", ref.from, ref.to);
        } else if (name === "TaskMarker") {
          const done = /x/i.test(text(ref));
          if (!inSel(ref.from, ref.to)) decos.push(Decoration.replace({ widget: new Tick(done) }).range(ref.from, ref.to));
          else mark("cm-mute", ref.from, ref.to);
          if (done && node.parent) mark("cm-done", ref.to, node.parent.to);
        } else if (name === "Footnote") {
          const defines = state.sliceDoc(ref.to, ref.to + 1) === ":" && ref.from === state.doc.lineAt(ref.from).from;
          mark(defines ? "cm-footnote-def" : "cm-footnote", ref.from + 2, ref.to - 1, { "data-footnote": state.sliceDoc(ref.from + 2, ref.to - 1) });
          if (!inSel(ref.from, ref.to) && !defines) { hide(ref.from, ref.from + 2); hide(ref.to - 1, ref.to); }
          else { mark("cm-mute", ref.from, ref.from + 2); mark("cm-mute", ref.to - 1, ref.to); }
        } else if (name === "ListMark") {
          const line = state.doc.lineAt(ref.from), bullet = node.parent && node.parent.parent && node.parent.parent.name === "BulletList";
          const task = node.parent && node.parent.getChild("Task");
          if (task && !inSel(ref.from, ref.to)) hide(ref.from, Math.min(ref.to + 1, task.from));   // a task shows its box, not a bullet
          else if (bullet && !inSel(ref.from, ref.to)) decos.push(Decoration.replace({ widget: new Glyph("•", "cm-bullet") }).range(ref.from, ref.to));
          else mark("cm-listmark", ref.from, ref.to);
          decos.push(Decoration.line({ class: "cm-li" }).range(line.from));
        } else if (name === "HorizontalRule") {
          decos.push(Decoration.line({ class: "cm-hr" }).range(state.doc.lineAt(ref.from).from));
        } else if (name === "Link") {
          const marks = node.getChildren("LinkMark"), url = node.getChild("URL");
          if (marks.length >= 2 && url) {
            const live = !inSel(ref.from, ref.to);
            mark("cm-link" + (live ? " cm-live" : ""), marks[0].to, marks[1].from, { "data-href": text(url) });
            if (live) { hide(marks[0].from, marks[0].to); hide(marks[1].from, ref.to); }
            else { mark("cm-mute", marks[0].from, marks[0].to); mark("cm-mute", marks[1].from, ref.to); }
            return false;
          }
        } else if (name === "URL" || name === "Autolink") {
          mark("cm-url", ref.from, ref.to);
        } else if (name === "WikiLink") {
          const marks = node.getChildren("WikiMark"), target = node.getChild("WikiTarget"), anchor = node.getChild("WikiAnchor"), shown = node.getChild("WikiShown");
          if (marks.length < 2) return false;
          const live = !inSel(ref.from, ref.to), to = text(target).trim(), part = text(anchor).slice(1).trim();
          const known = links.state(to, part);
          const cls = "cm-wikilink" + (live ? " cm-live" : "") + (known === false ? " cm-broken" : "");
          mark(cls, marks[0].to, marks[1].from, { "data-target": to, "data-anchor": part });
          if (!live) { mark("cm-mute", marks[0].from, marks[0].to); mark("cm-mute", marks[1].from, marks[1].to); return false; }
          hide(marks[0].from, marks[0].to); hide(marks[1].from, marks[1].to);
          if (shown) hide(marks[0].to, shown.from + 1);   // all but the words after the bar
          else if (anchor && part.startsWith("^")) {       // a passage: the note's name and a mark, not the block's id
            decos.push(Decoration.replace({ widget: new Glyph(target ? " ¶" : "¶", "cm-wikilink cm-live cm-anchor") }).range(anchor.from, anchor.to));
          } else if (anchor) {
            decos.push(Decoration.replace({ widget: new Glyph(target ? " › " : "", "cm-wikilink cm-live cm-anchor") }).range(anchor.from, anchor.from + 1));
          }
          return false;
        } else if (name === "Tag") {
          mark("cm-tag", ref.from, ref.to, { "data-tag": text(ref).slice(1) });
        } else if (name === "BlockId") {
          mark("cm-blockid", ref.from, ref.to);
        }
        return undefined;
      },
    });
  }
  return Decoration.set(decos, true);
}

const formatting = (links) => ViewPlugin.fromClass(class {
  constructor(view) { this.decorations = format(view, links); }
  update(u) {
    if (u.docChanged || u.viewportChanged || u.selectionSet || u.focusChanged || syntaxTree(u.startState) !== syntaxTree(u.state)
        || u.transactions.some((tr) => tr.effects.some((e) => e.is(redraw)))) this.decorations = format(u.view, links);
  }
}, { decorations: (plugin) => plugin.decorations });

/* ---------- what a link leads to: asked of the page once, then remembered ---------- */

function linkBook(view, peek) {
  const known = new Map();
  const key = (target, anchor) => target.toLowerCase() + "#" + anchor.toLowerCase();
  const ask = (target, anchor) => {
    const k = key(target, anchor);
    if (!known.has(k)) {
      known.set(k, Promise.resolve(peek({ target, anchor: anchor ? (anchor.startsWith("^") ? anchor : anchor) : "" })).then(
        (got) => { known.set(k, got || { found: false }); if (view.current) view.current.dispatch({ effects: redraw.of(null) }); return known.get(k); },
        () => { known.delete(k); return null; }));
    }
    return known.get(k);
  };
  return {
    ask,
    state(target, anchor) {             // true, false, or undefined while it is not known
      const got = ask(target, anchor);
      return got && typeof got.then !== "function" ? !!got.found : undefined;
    },
    forget() { known.clear(); if (view.current) view.current.dispatch({ effects: redraw.of(null) }); },
  };
}

function embedBook(view, embed) {       // the text of each embed, asked of the page once
  const known = new Map();
  return {
    get(target, anchor) {
      const k = target.toLowerCase() + "#" + anchor.toLowerCase();
      if (!known.has(k)) {
        known.set(k, null);
        Promise.resolve(embed({ target, anchor })).then((got) => { known.set(k, got || { found: false }); if (view.current) view.current.dispatch({ effects: redraw.of(null) }); },
          () => { known.delete(k); });
      }
      return known.get(k);
    },
    forget() { known.clear(); },
  };
}

function wikiAt(state, pos) {
  for (let node = syntaxTree(state).resolveInner(pos, 1); node; node = node.parent) {
    if (node.name === "WikiLink") {
      const target = node.getChild("WikiTarget"), anchor = node.getChild("WikiAnchor");
      return { from: node.from, to: node.to, target: target ? state.sliceDoc(target.from, target.to).trim() : "",
        anchor: anchor ? state.sliceDoc(anchor.from + 1, anchor.to).trim() : "" };
    }
  }
  return null;
}

const KINDS = { idea: "Idea note", index: "Index note", quick: "Quick note", "project-note": "Project note", project: "Project", book: "Book", article: "Article" };

function footnoteAt(state, pos) {
  for (let node = syntaxTree(state).resolveInner(pos, 1); node; node = node.parent) {
    if (node.name === "Footnote") {
      const id = state.sliceDoc(node.from + 2, node.to - 1);
      const said = new RegExp("^\\[\\^" + id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\]:[ \\t]*(.*)$", "m").exec(state.doc.toString());
      return { from: node.from, to: node.to, id, text: said ? said[1] : "" };
    }
  }
  return null;
}

const hover = (links) => hoverTooltip(async (view, pos) => {
  const note = footnoteAt(view.state, pos);
  if (note) {
    return { pos: note.from, end: note.to, above: true, create() {
      const dom = document.createElement("div"), head = document.createElement("b");
      dom.className = "cm-peek"; head.textContent = "Footnote " + note.id; dom.append(head);
      const p = document.createElement("p"); p.textContent = note.text || "This footnote has no text yet. Write a line that starts with [^" + note.id + "]:"; dom.append(p);
      return { dom };
    } };
  }
  const link = wikiAt(view.state, pos);
  if (!link) return null;
  const got = await links.ask(link.target, link.anchor);
  if (!got) return null;
  return {
    pos: link.from, end: link.to, above: true,
    create() {
      const dom = document.createElement("div");
      dom.className = "cm-peek";
      const head = document.createElement("b");
      head.textContent = got.found ? (got.title || "This note") + (got.kind ? " · " + (KINDS[got.kind] || got.kind) : "")
        : got.title ? `Nothing called ${link.anchor.replace(/^\^/, "")} in ${got.title}` : "No note of this name yet";
      dom.append(head);
      if (got.found && got.text) { const p = document.createElement("p"); p.textContent = got.text.length > 600 ? got.text.slice(0, 600) + " …" : got.text; dom.append(p); }
      return { dom };
    },
  };
}, { hoverTime: 350 });

/* ---------- completing a link while it is typed ---------- */

const completeLinks = (opts) => async (context) => {
  const line = context.state.doc.lineAt(context.pos);
  const m = /\[\[([^\[\]|\n]*)$/.exec(line.text.slice(0, context.pos - line.from));
  if (!m) return null;
  const typed = m[1], start = context.pos - typed.length, hash = typed.indexOf("#");
  const put = (insert) => (view, _completion, from, to) => {
    const closed = view.state.sliceDoc(to, to + 2) === "]]";
    view.dispatch({ changes: { from, to: closed ? to + 2 : to, insert: insert + "]]" }, selection: { anchor: from + insert.length + 2 }, userEvent: "input.complete" });
  };
  if (hash < 0) {
    let rows;
    try { rows = await opts.complete({ q: typed }); } catch (e) { return null; }
    if (context.aborted) return null;
    return {
      from: start, filter: false,
      options: (rows || []).map((r, i) => ({ label: r.link, displayLabel: r.untitled ? (r.excerpt || r.title) : r.title,
        detail: KINDS[r.kind] || "", apply: put(r.link), boost: -i })),
    };
  }
  const rest = typed.slice(hash + 1).toLowerCase();
  let got;
  try { got = await opts.anchors({ target: typed.slice(0, hash) }); } catch (e) { return null; }
  if (context.aborted || !got) return null;
  const target = hash ? got.link || typed.slice(0, hash) : "";   // the note's full name, when only its start was typed
  const blocks = rest.startsWith("^");
  const options = blocks
    ? got.blocks.filter((b) => ("^" + b.id).toLowerCase().startsWith(rest) || b.text.toLowerCase().includes(rest.slice(1)))
      .map((b, i) => ({ label: "^" + b.id, displayLabel: b.text || b.id, detail: "passage", apply: put(target + "#^" + b.id), boost: -i }))
    : got.headings.filter((heading) => heading.toLowerCase().includes(rest))
      .map((heading, i) => ({ label: heading, detail: "heading", apply: put(target + "#" + heading), boost: -i }))
      .concat(rest ? [] : got.blocks.length ? [{ label: "^", displayLabel: "^ a passage of the note", detail: `${got.blocks.length}`, boost: -99,
        apply: (view, _c, from, to) => { view.dispatch({ changes: { from, to, insert: target + "#^" }, selection: { anchor: from + target.length + 2 } }); startCompletion(view); } }] : []);
  return { from: start, filter: false, options };
};

/* ---------- small commands ---------- */

const wrap = (sign) => (view) => {
  view.dispatch(view.state.changeByRange((range) => {
    const inner = view.state.sliceDoc(range.from, range.to);
    const around = view.state.sliceDoc(range.from - sign.length, range.from) === sign && view.state.sliceDoc(range.to, range.to + sign.length) === sign;
    if (around) {
      return { changes: [{ from: range.from - sign.length, to: range.from }, { from: range.to, to: range.to + sign.length }],
        range: EditorSelection.range(range.from - sign.length, range.to - sign.length) };
    }
    return { changes: [{ from: range.from, insert: sign }, { from: range.to, insert: sign }],
      range: inner ? EditorSelection.range(range.from + sign.length, range.to + sign.length) : EditorSelection.cursor(range.from + sign.length) };
  }), { userEvent: "input", scrollIntoView: true });
  return true;
};
const startLink = (view) => {
  const { from, to } = view.state.selection.main, inner = view.state.sliceDoc(from, to);
  view.dispatch({ changes: { from, to, insert: "[[" + inner }, selection: { anchor: from + 2 + inner.length }, userEvent: "input" });
  startCompletion(view);
  return true;
};

/* ---------- looks: the page's colours and faces, by its CSS variables ---------- */

const looks = EditorView.theme({
  "&": { color: "var(--ink)", backgroundColor: "transparent", fontSize: "17px" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: 'Georgia, "Iowan Old Style", serif', lineHeight: "1.6", overflow: "visible" },
  ".cm-content": { padding: "0", caretColor: "var(--ink)", minHeight: "var(--editor-min, 12em)" },
  ".cm-line": { padding: "0 2px" },
  ".cm-cursor": { borderLeftColor: "var(--ink)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": { backgroundColor: "color-mix(in srgb, var(--blue) 26%, transparent)" },
  ".cm-placeholder": { color: "var(--mute)", fontStyle: "italic" },
  ".cm-h": { fontFamily: "var(--grot)", fontWeight: "500", letterSpacing: "-0.02em", lineHeight: "1.2" },
  ".cm-h1": { fontSize: "1.7em", paddingTop: "0.5em", paddingBottom: "0.15em" },
  ".cm-h2": { fontSize: "1.4em", paddingTop: "0.5em", paddingBottom: "0.1em" },
  ".cm-h3": { fontSize: "1.18em", paddingTop: "0.4em" },
  ".cm-h4, .cm-h5, .cm-h6": { fontSize: "1em", fontWeight: "600", paddingTop: "0.3em" },
  ".cm-em": { fontStyle: "italic" },
  ".cm-strong": { fontWeight: "700" },
  ".cm-strike": { textDecoration: "line-through" },
  ".cm-code": { fontFamily: "var(--mono)", fontSize: "0.85em", background: "var(--sheet)", padding: "1px 3px" },
  ".cm-codeblock": { fontFamily: "var(--mono)", fontSize: "0.85em", background: "var(--sheet)", padding: "0 10px" },
  ".cm-quote": { borderLeft: "3px solid var(--ghost)", paddingLeft: "12px", color: "var(--mute)" },
  ".cm-callout": { borderLeft: "6px solid var(--ink)", background: "var(--sheet)", color: "var(--ink)" },
  ".cm-callout-label": { fontFamily: "var(--mono)", fontSize: "0.7em", letterSpacing: "0.05em", textTransform: "uppercase", fontWeight: "600" },
  ".cm-tick": { width: "0.9em", height: "0.9em", margin: "0 0.5em 0 0", verticalAlign: "-0.05em", accentColor: "var(--blue)", cursor: "pointer" },
  ".cm-done": { color: "var(--mute)", textDecoration: "line-through" },
  ".cm-footnote": { fontFamily: "var(--mono)", fontSize: "0.7em", verticalAlign: "super", color: "var(--blue)" },
  ".cm-footnote-def": { fontFamily: "var(--mono)", fontSize: "0.8em", color: "var(--blue)" },
  ".cm-table-wrap": { padding: "0.4em 0", overflowX: "auto", cursor: "text" },
  ".cm-table": { borderCollapse: "collapse", fontFamily: "var(--grot)", fontSize: "0.88em", lineHeight: "1.35" },
  ".cm-table th, .cm-table td": { border: "1px solid var(--ghost)", padding: "5px 10px", textAlign: "left", verticalAlign: "top" },
  ".cm-table th": { fontWeight: "600", borderBottom: "1px solid var(--ink)" },
  ".cm-embed": { margin: "0.3em 0 0.5em", padding: "8px 12px", border: "1px solid var(--ghost)", borderLeft: "6px solid var(--ghost)", background: "var(--sheet)", fontSize: "0.92em", whiteSpace: "pre-wrap" },
  ".cm-embed b": { display: "block", fontFamily: "var(--mono)", fontSize: "11px", fontWeight: "400", letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--mute)", marginBottom: "4px", whiteSpace: "normal" },
  ".cm-embed b.cm-embed-open": { cursor: "pointer", color: "var(--blue)" },
  ".cm-mute, .cm-listmark, .cm-blockid": { color: "var(--mute)" },
  ".cm-blockid": { fontFamily: "var(--mono)", fontSize: "0.75em" },
  ".cm-bullet": { color: "var(--mute)" },
  ".cm-hr": { color: "var(--mute)" },
  ".cm-link, .cm-url": { color: "var(--blue)", textDecoration: "underline", textUnderlineOffset: "3px" },
  ".cm-wikilink": { color: "var(--blue)" },
  ".cm-wikilink.cm-live, .cm-link.cm-live": { cursor: "pointer", textDecoration: "underline", textDecorationThickness: "1px", textUnderlineOffset: "3px" },
  ".cm-wikilink.cm-broken": { color: "var(--mute)", textDecorationStyle: "dashed" },
  ".cm-anchor": { opacity: "0.7" },
  ".cm-tag": { fontFamily: "var(--mono)", fontSize: "0.8em", color: "var(--blue)", cursor: "pointer" },
  ".cm-tooltip": { border: "1px solid var(--ink)", borderRadius: "0", backgroundColor: "var(--paper)", color: "var(--ink)" },
  ".cm-tooltip.cm-tooltip-autocomplete > ul": { fontFamily: "var(--grot)", fontSize: "14px", maxHeight: "16em" },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li": { padding: "5px 10px", lineHeight: "1.3" },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--ink)", color: "var(--paper)" },
  ".cm-completionDetail": { fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.05em", textTransform: "uppercase", fontStyle: "normal", opacity: "0.7", marginLeft: "1em" },
  ".cm-completionIcon": { display: "none" },
  ".cm-peek": { maxWidth: "46ch", padding: "9px 12px", fontFamily: "var(--grot)", fontSize: "14px", lineHeight: "1.4" },
  ".cm-peek b": { display: "block", fontWeight: "500", fontSize: "12px", fontFamily: "var(--mono)", letterSpacing: "0.04em", textTransform: "uppercase" },
  ".cm-peek p": { margin: "6px 0 0", fontFamily: 'Georgia, "Iowan Old Style", serif', fontSize: "15px" },
});

/* ---------- the editor ---------- */

// create(parent, options) -> the editor. Options, all optional:
//   doc            the text to start with
//   readOnly       the text is shown, formatted, and cannot be changed
//   placeholder    shown while there is no text
//   label          what assistive technology calls the field
//   onChange()     the text was changed by the person
//   onSave()       Ctrl/Cmd + S
//   onOpen({target, anchor, newTab})   a link to a note was clicked
//   onTag(tag)     a tag was clicked
//   complete({q}) -> [{title, link, kind, untitled, excerpt}]      notes to offer after [[
//   anchors({target}) -> {link, headings: [..], blocks: [{id, text}]}   parts of a note to offer after [[Note#
//   peek({target, anchor}) -> {found, title, kind, text}            what a link leads to
//   embed({target, anchor}) -> {found, title, body}                 the text that ![[...]] shows under its line
function create(parent, options = {}) {
  const opts = { complete: async () => [], anchors: async () => ({ link: "", headings: [], blocks: [] }), peek: async () => null, ...options };
  if (!opts.embed) opts.embed = opts.peek;
  const holder = { current: null };
  const links = linkBook(holder, opts.peek), embeds = embedBook(holder, opts.embed);
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: opts.doc || "",
      extensions: [
        history(),
        EditorView.lineWrapping,
        EditorView.contentAttributes.of({ spellcheck: "true", autocapitalize: "sentences", "aria-label": opts.label || "Note" }),
        keymap.of([
          { key: "Mod-s", run: () => { if (opts.onSave) opts.onSave(); return true; } },
          { key: "Mod-b", run: wrap("**") }, { key: "Mod-i", run: wrap("*") }, { key: "Mod-k", run: startLink },
          ...completionKeymap, ...historyKeymap, indentWithTab, ...defaultKeymap.filter((b) => b.key !== "Mod-Enter"),
        ]),
        new LanguageSupport(notesLanguage), Prec.high(keymap.of(markdownKeymap)), pasteURLAsLink,
        autocompletion({ override: [completeLinks(opts)], activateOnTyping: true, icons: false }),
        formatting(links),
        focusField, focusWatch, blocks(embeds, opts),
        hover(links),
        looks,
        opts.placeholder ? placeholder(opts.placeholder) : [],
        opts.readOnly ? [EditorState.readOnly.of(true), EditorView.editable.of(false)] : [],
        EditorView.updateListener.of((u) => {
          if (u.docChanged && opts.onChange && !u.transactions.every((tr) => tr.annotation(fromOutside))) opts.onChange();
        }),
        EditorView.domEventHandlers({
          mousedown(event, v) {
            if (event.button !== 0 || !(event.target instanceof Element)) return false;
            const mod = event.metaKey || event.ctrlKey;
            const wiki = event.target.closest(".cm-wikilink");
            if (wiki && (mod || wiki.classList.contains("cm-live"))) {
              const link = wiki.hasAttribute("data-target") ? { target: wiki.getAttribute("data-target"), anchor: wiki.getAttribute("data-anchor") || "" }
                : wikiAt(v.state, v.posAtDOM(wiki));
              if (!link || !opts.onOpen) return false;
              event.preventDefault();
              opts.onOpen({ target: link.target, anchor: link.anchor, newTab: mod });
              return true;
            }
            const url = event.target.closest(".cm-link");
            if (url && (mod || url.classList.contains("cm-live")) && /^https?:\/\//i.test(url.getAttribute("data-href") || "")) {
              event.preventDefault();
              window.open(url.getAttribute("data-href"), "_blank", "noopener,noreferrer");
              return true;
            }
            const tag = event.target.closest(".cm-tag");
            if (tag && opts.onTag && (mod || !v.hasFocus)) { event.preventDefault(); opts.onTag(tag.getAttribute("data-tag")); return true; }
            return false;
          },
        }),
      ],
    }),
  });
  holder.current = view;
  return {
    view,
    getText: () => view.state.doc.toString(),
    setText(text) {                    // put other text in place, as when the file changed on disk: not a change by the person
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text }, annotations: fromOutside.of(true) });
    },
    rewrite(text) {                    // the page changed the text for the person (an outline was rearranged): saved like typing, and it can be undone
      const now = view.state.doc.toString();
      let a = 0, b = 0;
      while (a < now.length && a < text.length && now[a] === text[a]) a++;
      while (b < now.length - a && b < text.length - a && now[now.length - 1 - b] === text[text.length - 1 - b]) b++;
      view.dispatch({ changes: { from: a, to: now.length - b, insert: text.slice(a, text.length - b) }, userEvent: "input" });
    },
    insert(text) { view.dispatch(view.state.replaceSelection(text), { userEvent: "input", scrollIntoView: true }); view.focus(); },
    focus(where) {
      if (where === "start") view.dispatch({ selection: { anchor: 0 } });
      if (where === "end") view.dispatch({ selection: { anchor: view.state.doc.length } });
      view.focus();
    },
    hasFocus: () => view.hasFocus,
    forgetLinks: () => { embeds.forget(); links.forget(); },  // notes were made, renamed or removed: ask again what links lead to
    destroy() { holder.current = null; view.destroy(); },
  };
}

window.NoteEditor = { create };
