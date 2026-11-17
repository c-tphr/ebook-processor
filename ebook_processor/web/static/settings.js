/* The settings window, shared by every page of the app.

   It is loaded in the head of each page, before anything is drawn, so the colour theme is right from the first
   paint. It keeps one object of settings (window.AppSettings.values), stored by the app in its data folder, and
   tells the page when one changes, whether the change was made here, in another tab, or in the reader that the
   notebook shows beside itself.

     AppSettings.ready             a promise: the stored settings have arrived
     AppSettings.values            the settings, defaults filled in
     AppSettings.set(key, value)   change one; null puts the default back
     AppSettings.onChange(fn)      fn(key, value) after every change; key "practice" carries the schedule
     AppSettings.open(tab)         show the window on a tab: general, add, reader, notebook, practice
     AppSettings.processing(...)   the options of a run, as used by the Add a book form and by the Add a book tab
*/
(function () {
"use strict";

const DEFAULTS = {
  theme: "system",                                    // general
  face: "serif", size: 19, leading: 1.6, measure: "medium", read_mode: "page", read_preview: false,   // reader
  nb_beside: true, nb_side: "left", nb_view: "scroll", nb_split: 0.5,            // notebook and its side reader
};
const RUN_KEYS = ["density", "max_questions", "kind", "content", "rounds", "model", "checker_model", "effort", "jobs", "epubcheck", "preview"];
const TABS = [["general", "General"], ["add", "Add a book"], ["reader", "Reader"], ["notebook", "Notebook"], ["practice", "Practice"]];
const values = { ...DEFAULTS };
const listeners = new Set();
const channel = "BroadcastChannel" in window ? new BroadcastChannel("ebook-processor-settings") : null;

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v; else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "checked" || k === "disabled" || k === "value") node[k] = v; else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return node;
}
async function api(path, options = {}) {
  const headers = { "X-Ebook-Processor": "1" };
  if (options.json !== undefined) { options.body = JSON.stringify(options.json); headers["Content-Type"] = "application/json"; }
  const response = await fetch("/api" + path, { ...options, headers });
  const data = await response.json().catch(() => ({ error: "The app gave an unreadable answer." }));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

/* ---------- the values ---------- */
function applyTheme() {
  const root = document.documentElement;
  if (values.theme === "light" || values.theme === "dark") root.dataset.theme = values.theme; else root.removeAttribute("data-theme");
  try { localStorage.setItem("ep-theme", values.theme); } catch (e) { /* private window: the theme arrives a moment later */ }
}
try { const t = localStorage.getItem("ep-theme"); if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; } catch (e) { /* as above */ }

function take(key, value) {
  if (value == null) { if (key in DEFAULTS) values[key] = DEFAULTS[key]; else delete values[key]; } else values[key] = value;
  if (key === "theme") applyTheme();
  for (const fn of listeners) fn(key, values[key]);
}
function set(key, value) {
  if (value === undefined) value = null;
  take(key, value);
  if (channel) channel.postMessage({ key, value });
  return api("/settings", { method: "PUT", json: { [key]: value } }).catch(() => {});
}
function tellPractice(settings) {
  for (const fn of listeners) fn("practice", settings);
}
if (channel) channel.onmessage = (e) => {
  if (!e.data) return;
  if (e.data.practice) return tellPractice(e.data.practice);
  if (typeof e.data.key === "string") take(e.data.key, e.data.value);
};
const ready = api("/settings").then((stored) => { Object.assign(values, stored); applyTheme(); return values; }).catch(() => values);
let infoPromise = null;
const info = () => (infoPromise = infoPromise || api("/info").catch((e) => { infoPromise = null; throw e; }));
const runDefaults = () => Object.fromEntries(RUN_KEYS.map((k) => [k, values["add_" + k]]));

/* ---------- the options of a run ---------- */
const EFFORT = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Maximum" };
let serial = 0;
// `initial` holds the starting values by key; what it leaves out comes from the tool's own defaults in `inf`.
// `onchange(key, value)` is called when the user changes one. The result has the two groups of fields, `read()`
// for all the values and `set(key, value)` to change one from outside.
function processing(inf, initial, onchange) {
  const d = inf.defaults || {}, dens = inf.densities || {}, models = inf.models || [];
  const v = { density: "medium", max_questions: d.max_questions, kind: d.kind, content: d.content, rounds: d.rounds, jobs: d.jobs,
    model: "", checker_model: "", effort: "", epubcheck: false, preview: false };
  for (const [k, x] of Object.entries(initial || {})) if (x != null && k in v) v[k] = x;
  const name = "st-density-" + (++serial);
  const fire = (key) => { if (onchange) onchange(key, read()[key]); };
  const select = (key, options, value) => {
    const s = el("select", { onchange: () => { if (key === "model" || key === "checker_model") sync(true); fire(key); } },
      options.map(([val, t]) => el("option", { value: val }, t)));
    s.value = String(value); if (s.selectedIndex < 0) s.selectedIndex = 0;
    return s;
  };
  const field = (label, control, hint) => el("label", { class: "st-field" }, el("span", { class: "m" }, label), control, hint ? el("small", {}, hint) : null);
  const density = el("div", { class: "st-seg", role: "radiogroup", "aria-label": "Section length" },
    ["short", "medium", "long"].map((k) => el("label", {},
      el("input", { type: "radio", name, value: k, checked: k === v.density, onchange: () => fire("density") }), k,
      el("small", {}, dens[k] ? `${Number(dens[k][0]).toLocaleString("en")}–${Number(dens[k][1]).toLocaleString("en")} words` : ""))));
  // Models come from Claude Code (what this login may use) and, when it is installed and signed in, from Codex,
  // whose values start with "codex". Without a list, the two fields are free text.
  const ofClaude = models.filter((m) => (m.provider || "claude") === "claude"), ofCodex = models.filter((m) => m.provider === "codex");
  const defaultModel = ofClaude.find((m) => m.value === "default"), named = ofClaude.filter((m) => m.value !== "default");
  const claudeDefault = defaultModel ? `Claude Code's default (${defaultModel.description})` : "Claude Code's default";
  const modelHint = el("span", {}, models.length ? "" : "A Claude Code model (opus, sonnet, or a full name), or a Codex one with codex: before it (codex:gpt-6.1-sol).");
  // `lead` options stand before the groups; `first` ones open the Claude Code group. The Codex group ends with
  // a way to name a model that is not in Codex's list (a new one, or all of them when the list could not be read).
  const OTHER = "codex:?";
  const modelSelect = (key, lead, first, value) => {
    const option = ([val, t]) => el("option", { value: val }, t);
    const claude = [...first, ...named.map((m) => [m.value, m.name])].map(option), codex = ofCodex.map((m) => option([m.value, m.name]));
    const other = option([OTHER, "Another Codex model…"]);
    let last = "";
    const s = el("select", { onchange: () => {
      if (s.value === OTHER) {
        const name = (prompt("The model's name as Codex knows it, for example gpt-6.1-sol:") || "").trim().replace(/^codex:/i, "");
        if (!name) { s.value = last; return; }
        s.have("codex:" + name); s.value = "codex:" + name;
      }
      last = s.value; sync(true); fire(key);
    } }, lead.map(option),
      codex.length ? [el("optgroup", { label: "Claude Code" }, claude), el("optgroup", { label: "Codex" }, codex, other)] : claude);
    s.have = (val) => {                 // a Codex model that is not listed gets an option of its own
      if (!codex.length || !/^codex:.+/.test(val) || val === OTHER || [...s.options].some((o) => o.value === val)) return;
      other.before(option([val, val.slice(6)]));
    };
    s.have(String(value));
    s.value = String(value); if (s.selectedIndex < 0) s.selectedIndex = 0;
    last = s.value;
    return s;
  };
  const checkerHint = el("span", {}), effortHint = el("span", {});
  const text = (key, placeholder, value) => el("input", { type: "text", placeholder, value: value || "", onchange: () => fire(key) });
  const c = {
    max_questions: select("max_questions", [[1, "1"], [2, "Up to 2"], [3, "Up to 3"], [4, "Up to 4"]], v.max_questions),
    kind: select("kind", [["auto", "Decide section by section"], ["expository", "Exposition (essays, science, history)"],
      ["narrative", "Narrative (novels, stories, memoir)"], ["technical", "Technical (textbooks, manuals)"]], v.kind),
    content: select("content", [["text", "Read tables, code and formulas"], ["figures", "Also look at the pictures (slower)"], ["prose", "Prose only"]], v.content),
    rounds: select("rounds", [[3, "Check, revise, check again (up to 3 checks)"], [2, "Up to 2 checks"], [1, "One check, no revision"], [0, "No check (fastest)"]], v.rounds),
    model: models.length
      ? modelSelect("model", [], [["", claudeDefault]], v.model)
      : text("model", "Claude Code's default", v.model),
    checker_model: models.length
      ? modelSelect("checker_model", [["", "Same as the model above"]], [["claude", claudeDefault]], v.checker_model)
      : text("checker_model", "Same as above", v.checker_model),
    effort: select("effort", [["", "Each tool's default"], ...Object.entries(EFFORT)], v.effort),
    jobs: select("jobs", [[1, "1"], [2, "2"], [3, "3"], [4, "4"], [6, "6"]], v.jobs),
    epubcheck: select("epubcheck", [["", "Skip"], ["1", "Run on both books (needs Java)"]], v.epubcheck ? "1" : ""),
    preview: select("preview", [["", "None"], ["1", "A page of questions before each section"]], v.preview ? "1" : ""),
  };
  function sync(byUser) {
    if (!models.length) return;
    const pick = (value) => (value && value !== "claude" ? models.find((m) => m.value === value) : defaultModel);
    const drafting = pick(c.model.value), checking = c.checker_model.value ? pick(c.checker_model.value) : drafting;
    const about = (m) => (m ? [m.description, m.resolved && m.resolved !== m.value ? m.resolved : ""].filter(Boolean).join(" / ") : "");
    const unlisted = "Not in Codex's list. The name is passed to Codex as it is.";
    modelHint.textContent = c.model.value ? (drafting ? about(drafting) : unlisted) : "Drafts and revises the questions.";
    checkerHint.textContent = c.checker_model.value ? (pick(c.checker_model.value) ? about(checking) : unlisted) : "Checks them, in a separate session.";
    // effort is one setting for the whole run, so offer only the levels both models take
    const levels = (drafting?.efforts || []).filter((e) => e in EFFORT && (checking?.efforts || []).includes(e));
    const was = c.effort.value, keep = levels.includes(was) ? was : "";
    c.effort.replaceChildren(el("option", { value: "" }, levels.length ? "Each tool's default" : "Not adjustable for this choice"),
      ...levels.map((e) => el("option", { value: e }, EFFORT[e] || e)));
    c.effort.value = keep; c.effort.disabled = !levels.length;
    effortHint.textContent = levels.length ? "" : "At least one chosen model has no effort setting.";
    if (byUser && keep !== was) fire("effort");
  }
  sync(false);
  function read() {
    return { density: density.querySelector("input:checked").value, max_questions: +c.max_questions.value, kind: c.kind.value,
      content: c.content.value, rounds: +c.rounds.value, model: c.model.value.trim(), checker_model: c.checker_model.value.trim(),
      effort: c.effort.value, jobs: +c.jobs.value, epubcheck: c.epubcheck.value === "1", preview: c.preview.value === "1" };
  }
  function setOne(key, value) {
    if (value == null) value = { density: "medium", model: "", checker_model: "", effort: "", epubcheck: false, preview: false }[key] ?? d[key];
    if (key === "density") { const box = density.querySelector(`input[value="${value}"]`); if (box) box.checked = true; return; }
    if (!c[key]) return;
    if (c[key].have) c[key].have(String(value));
    c[key].value = key === "epubcheck" || key === "preview" ? (value ? "1" : "") : String(value);
    if (c[key].tagName === "SELECT" && c[key].selectedIndex < 0) c[key].selectedIndex = 0;
    if (key === "model" || key === "checker_model") sync(false);
  }
  const main = el("div", { class: "st-grid" },
    el("div", { class: "st-field wide" }, el("span", { class: "m" }, "Section length"), density,
      el("small", {}, "A checkpoint follows each section. Breaks fall at natural boundaries, so lengths vary.")),
    field("Questions per checkpoint", c.max_questions), field("Kind of book", c.kind),
    field("Tables, code, formulas, figures", c.content, "What the model may see and ask about beyond the running text."),
    field("Independent check", c.rounds, "A question that fails its last check is left out and listed in the report."));
  const advanced = el("div", { class: "st-grid" },
    field("Model", c.model, modelHint), field("Model for checking", c.checker_model, checkerHint), field("Reasoning effort", c.effort, effortHint),
    field("Calls at the same time", c.jobs), field("EPUBCheck", c.epubcheck),
    field("Question previews in the exported book", c.preview, "For the book with visible checkpoints. The reader in this app has its own setting, on the Reader tab of Settings."));
  return { main, advanced, read, set: setOne };
}

/* ---------- the window ---------- */
const CSS = `
.st-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px var(--gut, 24px); margin-top: 26px; }
.st-grid > .wide { grid-column: 1 / -1; }
.st-field { display: block; }
.st-field > .m { display: block; margin-bottom: 6px; font: 12px/1.5 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; }
.st-field > small { display: block; color: var(--mute); margin-top: 6px; font-size: 13px; line-height: 1.35; }
.st-field select, .st-field input[type=text], .st-field input[type=number] { font: 15px/1.2 var(--grot); color: var(--ink); background: var(--sheet);
  border: 1px solid var(--ink); border-radius: 0; padding: 10px 11px; width: 100%; }
.st-field input[type=number] { width: 11ch; font-family: var(--mono); }
.st-seg { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--ink); }
.st-seg label { text-align: center; padding: 11px 4px 10px; cursor: pointer; border-right: 1px solid var(--ink); font: 12px/1.3 var(--mono); letter-spacing: 0.05em; text-transform: uppercase; }
.st-seg label:last-child { border-right: 0; }
.st-seg input { position: absolute; opacity: 0; pointer-events: none; }
.st-seg small { display: block; font-size: 10.5px; letter-spacing: 0; color: var(--mute); font-variant-numeric: tabular-nums; }
.st-seg label:has(input:checked) { background: var(--ink); color: var(--paper); }
.st-seg label:has(input:checked) small { color: var(--paper); opacity: 0.75; }
.st-seg label:has(input:focus-visible) { outline: 2px solid var(--blue); outline-offset: 2px; }
@media (max-width: 760px) { .st-grid { grid-template-columns: minmax(0, 1fr); } }

.stg { padding: 0; border: 1px solid var(--ink); border-radius: 0; background: var(--paper); color: var(--ink); font: 15px/1.45 var(--grot);
  width: min(920px, calc(100vw - 24px)); height: min(700px, calc(100vh - 24px)); max-width: none; max-height: none; }
.stg[open] { display: grid; grid-template-rows: auto minmax(0, 1fr); }
.stg::backdrop { background: rgba(0, 0, 0, 0.32); }
.stg > header { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 12px 12px 20px; border-bottom: 1px solid var(--ink); }
.stg > header b { font: 500 20px/1.2 var(--grot); letter-spacing: -0.03em; display: flex; align-items: baseline; gap: 10px; }
.stg > header b::before { content: ""; width: 10px; height: 10px; background: var(--ink); }
.stg button { font: 12px/1 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; border: 1px solid var(--ink); border-radius: 0;
  background: transparent; color: var(--ink); padding: 10px 13px; cursor: pointer; }
.stg button:hover { background: var(--ink); color: var(--paper); }
.stg button[aria-pressed=true] { background: var(--ink); color: var(--paper); }
.stg button.quiet { border-color: transparent; text-decoration: underline; text-underline-offset: 3px; padding-left: 0; padding-right: 0; }
.stg button.quiet:hover { background: transparent; color: var(--blue); }
.stg .cols { display: grid; grid-template-columns: 200px minmax(0, 1fr); min-height: 0; }
.stg nav { display: flex; flex-direction: column; border-right: 1px solid var(--ink); padding: 10px 0; overflow: auto; }
.stg nav button { text-align: left; border: 0; padding: 13px 20px; font-size: 12px; line-height: 1.2; }
.stg nav button:hover { background: transparent; color: var(--blue); }
.stg nav button[aria-selected=true], .stg nav button[aria-selected=true]:hover { background: var(--ink); color: var(--paper); }
.stg .tab { overflow: auto; padding: 22px 24px 30px; }
.stg h3 { font: 500 26px/1.1 var(--grot); letter-spacing: -0.035em; margin: 0 0 8px; }
.stg h4 { font: 12px/1.5 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; font-weight: 400; margin: 30px 0 0; padding-bottom: 8px; border-bottom: 1px solid var(--ink); }
.stg .lede { margin: 0 0 18px; max-width: 62ch; }
.stg .st-row { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 6px 18px; align-items: center; padding: 14px 0; border-top: 1px solid var(--ghost); }
.stg .st-row > .m { font: 12px/1.5 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; }
.stg .st-row > small { grid-column: 2; color: var(--mute); font-size: 13px; line-height: 1.35; }
.stg .st-choice { display: inline-flex; flex-wrap: wrap; align-items: center; }
.stg .st-choice button { margin-right: -1px; }
.stg .st-choice .n { font: 12px/1 var(--mono); letter-spacing: 0.04em; margin-left: 14px; }
.stg .st-kv { display: flex; align-items: baseline; font: 12px/1.9 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; }
.stg .st-kv > i { flex: 1; min-width: 16px; border-bottom: 1px dotted var(--mute); margin: 0 8px; transform: translateY(-4px); }
.stg .st-kv > b { font-weight: 400; text-align: right; overflow-wrap: anywhere; text-transform: none; }
.stg .st-grid { margin-top: 18px; }
.stg .err { border: 1px solid var(--ink); border-left-width: 8px; padding: 10px 14px; }
@media (max-width: 640px) {
  .stg .cols { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); }
  .stg nav { flex-direction: row; border-right: 0; border-bottom: 1px solid var(--ink); padding: 0; }
  .stg nav button { white-space: nowrap; padding: 13px 14px; }
  .stg .st-row { grid-template-columns: minmax(0, 1fr); } .stg .st-row > small { grid-column: 1; }
}
`;
document.head.append(el("style", {}, CSS));

let dialog = null, nav = null, tab = null, current = "general";
const row = (label, control, hint) => el("div", { class: "st-row" }, el("span", { class: "m" }, label), control, hint ? el("small", {}, hint) : null);
function choice(key, options) {         // one of a few values, as buttons side by side
  const box = el("span", { class: "st-choice", role: "group" });
  const draw = () => box.replaceChildren(...options.map(([v, t]) => el("button", { type: "button", "aria-pressed": String(values[key] === v), onclick: () => { set(key, v); draw(); } }, t)));
  draw();
  return box;
}
const kv = (key, value) => el("div", { class: "st-kv" }, el("span", {}, key), el("i"), el("b", {}, value));

const DRAW = {
  async general() {
    const about = el("div", {});
    info().then((i) => about.replaceChildren(kv("Version", i.version || "unknown"), kv("Books are kept in", i.data || "unknown"),
      kv("Claude Code", i.claude && i.claude.ok ? i.claude.version.replace(/\s*\(.*/, "") + " / ready" : "not ready"),
      kv("Codex", !i.codex || !i.codex.installed ? "not installed" : !i.codex.ok ? "not signed in"
        : i.codex.version.replace(/^codex-cli\s*/, "") + " / ready" + (i.codex.models_note ? " / its models could not be listed" : ""))), () => {});
    return [el("h3", {}, "General"),
      row("Colours", choice("theme", [["system", "As the system"], ["light", "Light"], ["dark", "Dark"]]), "For every page of the app."),
      el("h4", {}, "About"), about];
  },
  async add() {
    const inf = await info();
    const form = processing(inf, runDefaults(), (key, value) => set("add_" + key, value));
    return [el("h3", {}, "Add a book"),
      el("p", { class: "lede" }, "What the Add a book form starts with. Each can still be changed there for one book. Books already processed are not affected."),
      form.main, el("h4", {}, "Advanced"), form.advanced,
      el("div", { style: "margin-top:26px" }, el("button", { type: "button", class: "quiet", onclick: async () => {
        for (const k of RUN_KEYS) if (values["add_" + k] != null) set("add_" + k, null);
        show("add");
      } }, "Put back the tool's own defaults"))];
  },
  async reader() {
    const n = el("span", { class: "n" });
    const size = (d) => { set("size", Math.max(14, Math.min(30, values.size + d))); n.textContent = values.size + " px"; };
    n.textContent = values.size + " px";
    return [el("h3", {}, "Reader"),
      el("p", { class: "lede" }, "How a book's text is set, in the reader and in the reader beside the notebook."),
      row("Typeface", choice("face", [["serif", "Serif"], ["sans", "Grotesk"]])),
      row("Size", el("span", { class: "st-choice" }, el("button", { type: "button", onclick: () => size(-1) }, "Smaller"), el("button", { type: "button", onclick: () => size(1) }, "Larger"), n)),
      row("Line spacing", choice("leading", [[1.4, "Tight"], [1.6, "Normal"], [1.8, "Open"]])),
      row("Column width", choice("measure", [["narrow", "Narrow"], ["medium", "Medium"], ["wide", "Wide"]])),
      row("A new book opens in", choice("read_mode", [["page", "Pages"], ["scroll", "Scroll"], ["rsvp", "RSVP"]]), "A book you have started opens in the view you left it in."),
      row("Preview questions", choice("read_preview", [[false, "Off"], [true, "On"]]), "A section begins with the questions of its checkpoint that you have not answered yet, to read with them in mind. RSVP pauses there to show them. Answers to previewed questions are marked as such in the notebook."),
      el("h4", {}, "Keys"),
      el("p", { style: "margin:12px 0 0;max-width:62ch" }, "← → turn pages or step sentences. Space plays RSVP. 1 2 3 change the view. c opens the contents, / searches. With text selected, h highlights it and n adds a note; with nothing selected, n notes the paragraph under the pointer.")];
  },
  async notebook() {
    return [el("h3", {}, "Notebook"),
      el("p", { class: "lede" }, "Open on a note, a highlight, a section or a checkpoint answer shows its place in the book. Looking a passage up beside the notebook does not move your place in the book."),
      row("Open a passage", choice("nb_beside", [[true, "Beside the notebook"], [false, "In the full reader"]]), "Beside needs a wide window. In a narrow one the full reader opens."),
      row("Reader on the", choice("nb_side", [["left", "Left"], ["right", "Right"]])),
      row("Reader shows", choice("nb_view", [["scroll", "Scroll"], ["page", "Pages"]]), "Typeface, size and spacing are those of the Reader tab.")];
  },
  async practice() {
    let s = await api("/practice/settings");
    const store = async (key, value) => {
      try { s = await api("/practice/settings", { method: "PUT", json: { ...s, [key]: value } }); } catch (e) { alert(e.message); return show("practice"); }
      tellPractice(s); if (channel) channel.postMessage({ practice: s });
    };
    const number = (key, label, hint, step) => el("label", { class: "st-field" }, el("span", { class: "m" }, label),
      el("input", { type: "number", step, value: s[key] ?? "", placeholder: "none", onchange: (e) => {
        const v = e.target.value.trim();
        store(key, v === "" ? null : key === "new_per_day" ? parseInt(v, 10) : parseFloat(v));
      } }), el("small", {}, hint));
    const toggle = (key, label, hint) => {
      const box = el("span", { class: "st-choice", role: "group" });
      const draw = () => box.replaceChildren(...[[true, "On"], [false, "Off"]].map(([v, t]) => el("button", { type: "button", "aria-pressed": String(s[key] === v), onclick: async () => { await store(key, v); draw(); } }, t)));
      draw();
      return el("div", { class: "st-field" }, el("span", { class: "m" }, label), box, el("small", {}, hint));
    };
    return [el("h3", {}, "Practice"),
      el("p", { class: "lede" }, "Each Remembered multiplies a card's interval; each Forgot shortens it and brings the card back later in the session. Forgetting it again there drops it to one day. A question becomes a card when you meet it at its checkpoint, and joins the schedule the first time you remember it."),
      el("div", { class: "st-grid" },
        number("remember_multiplier", "Remembered multiplies by", "Higher spaces cards out faster.", "0.1"),
        number("forget_multiplier", "Forgot multiplies by", "0.5 halves the interval; 0 starts the card over.", "0.05"),
        number("first_interval", "Starting interval, days", "What a card begins with when it joins the schedule.", "1"),
        number("max_interval", "Longest interval, days", "Empty for no longest interval.", "1"),
        number("new_per_day", "New cards per day", "Questions you missed or skipped at a checkpoint. Empty for no limit.", "1"),
        toggle("same_day_first_review", "First review the same day", "A card is due again on the day it is learned. Off: the day after."),
        toggle("fuzz", "Vary due dates slightly", "Keeps cards learned together from always coming back together."))];
  },
};

function build() {
  if (dialog) return;
  nav = el("nav", { role: "tablist", "aria-label": "Settings", "aria-orientation": "vertical" });
  tab = el("div", { class: "tab", role: "tabpanel", tabindex: "-1" });
  dialog = el("dialog", { class: "stg", "aria-label": "Settings" },
    el("header", {}, el("b", {}, "Settings"), el("button", { type: "button", onclick: () => dialog.close() }, "Close")),
    el("div", { class: "cols" }, nav, tab));
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });   // a click on the dimmed page
  nav.addEventListener("keydown", (e) => {
    const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const i = TABS.findIndex(([k]) => k === current);
    show(TABS[(i + step + TABS.length) % TABS.length][0], true);
  });
  document.body.append(dialog);
}
async function show(name, focus) {
  current = TABS.some(([k]) => k === name) ? name : "general";
  nav.replaceChildren(...TABS.map(([k, t]) => el("button", { type: "button", role: "tab", id: "stg-tab-" + k, "aria-selected": String(k === current),
    tabindex: k === current ? "0" : "-1", onclick: () => show(k) }, t)));
  if (focus) nav.querySelector("[aria-selected=true]").focus();
  tab.setAttribute("aria-labelledby", "stg-tab-" + current);
  const mine = current;
  let body;
  try { body = await DRAW[current](); } catch (e) { body = [el("h3", {}, TABS.find(([k]) => k === mine)[1]), el("p", { class: "err" }, e.message)]; }
  if (mine !== current) return;         // another tab was chosen while this one loaded
  tab.replaceChildren(...body); tab.scrollTop = 0;
}
async function open(name) {
  await ready;
  build();
  if (!dialog.open) dialog.showModal();
  await show(name);
}

window.AppSettings = { values, ready, set, open, processing, runDefaults, info, defaults: DEFAULTS,
  onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  get isOpen() { return !!(dialog && dialog.open); } };
})();
