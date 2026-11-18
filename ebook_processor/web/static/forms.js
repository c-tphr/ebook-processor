/* The forms a question can take, for every page that asks or shows one.

   A question is { prompt, answer } and, when it is not an open question, `form` with that form's own members:
     choice      choices (four texts), correct (the index of the right one)
     truefalse   truth                     (prompt is the statement)
     cloze       accept (the answers that count, the first being the one shown; prompt has ____ where they go)
   For these three, `answer` is the explanation shown after the reader has answered. (ebook_processor/forms.py)

     QForms.form(q)                  "open", "choice", "truefalse" or "cloze"
     QForms.label(q)                 "Multiple choice", ... ; "" for an open question
     QForms.key(q)                   the right answer in words
     QForms.plain(q)                 { question, answer } as plain text a reader could grade themselves against
     QForms.heading(q, filled)       the question as nodes for a heading; `filled` writes the missing words in
     QForms.matches(typed, accept)   whether typed words count as one of the accepted answers
     QForms.ask(q, onAnswer)         the place to answer: { el, key(event), focus() };
                                     onAnswer({ correct, text }) is called once, with what was given
     QForms.verdict(q, given)        what is shown after an answer: right or wrong, the right answer, the explanation
     QForms.choices(q)               the choices as a list with the right one marked, for pages that only show
     QForms.edit(q)                  fields to change a question of any form: { el, read() }; read() gives
                                     { question, answer } and, for a question that is not open, `form` with its members
     QForms.compose()                the same fields for a new question, with a choice of its kind: { el, read() }
     QForms.openEditor({ job, id, onChange })      a window to change one of a book's own questions: its wording,
                                     its answer key and its passage, with the model's rewrite to a wish, a check on
                                     request, the original put back, and the card started over
     QForms.openAdder({ job, section, onChange })  a window to write a question of your own for a section
                                     onChange({ id, edited | added | deleted | restarted }) is called after each change
*/
(function () {
"use strict";

const GAP = "____";
const LABEL = { choice: "Multiple choice", truefalse: "True or false", cloze: "Fill in the blank" };
const LETTERS = "ABCD";

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v; else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "disabled" || k === "value") node[k] = v; else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return node;
}

const form = (q) => (q && LABEL[q.form] ? q.form : "open");
const label = (q) => LABEL[form(q)] || "";
const text = (q) => (q.prompt !== undefined ? q.prompt : q.question);   // the notebook calls it "question"
function key(q) {
  const f = form(q);
  return f === "choice" ? q.choices[q.correct] : f === "truefalse" ? (q.truth ? "True" : "False") : f === "cloze" ? q.accept[0] : "";
}
function plain(q) {
  const f = form(q), ends = (s) => (/[.!?]$/.test(s.trim()) ? s.trim() : s.trim() + ".");
  if (f === "open") return { question: text(q), answer: q.answer };
  return { question: (f === "truefalse" ? "True or false: " : "") + text(q), answer: `${ends(key(q))} ${q.answer}`.trim() };
}
function heading(q, filled) {
  if (form(q) !== "cloze") return [text(q)];
  const [before, after] = text(q).split(GAP);
  return [before, el("span", { class: "qf-gap" + (filled ? " on" : ""), "aria-label": filled ? null : "blank" }, filled ? key(q) : ""), after || ""];
}

/* ---------- typed answers ---------- */
// What is forgiven: capital letters, accents, punctuation, a leading article, spacing, and a slip of the keys
// (one for an answer of five letters or more, two from nine). An answer with a digit in it must be exact.
function tidy(s) {
  return String(s).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/(\d)[,  ](?=\d{3}\b)/g, "$1").replace(/(\d)\.(?=\d)/g, "$1·")
    .replace(/&/g, " and ").replace(/[^\p{L}\p{N}·%]+/gu, " ").trim().replace(/^(the|a|an) /, "");
}
function distance(a, b, limit) {        // edits between two words or phrases, counting a swap of neighbours as one
  if (Math.abs(a.length - b.length) > limit) return limit + 1;
  let two = null, one = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      row[j] = Math.min(one[j] + 1, row[j - 1] + 1, one[j - 1] + cost);
      if (two && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) row[j] = Math.min(row[j], two[j - 2] + 1);
    }
    two = one; one = row;
  }
  return one[b.length];
}
function matches(typed, accept) {
  const mine = tidy(typed);
  if (!mine) return false;
  return (accept || []).some((answer) => {
    const want = tidy(answer);
    if (!want) return false;
    if (mine === want || mine.replace(/ /g, "") === want.replace(/ /g, "")) return true;
    if (/\d/.test(want) || /\d/.test(mine)) return false;
    const slips = want.length >= 9 ? 2 : want.length >= 5 ? 1 : 0;
    return slips > 0 && distance(mine, want, slips) <= slips;
  });
}

/* ---------- answering ---------- */
function shuffled(n) {
  const order = Array.from({ length: n }, (_, i) => i);
  for (let i = n - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
  return order;
}
// `order` (for multiple choice) keeps the choices where they were when the same question is drawn again.
function ask(q, onAnswer, order) {
  const f = form(q);
  let done = false;
  const give = (correct, given) => { if (done) return; done = true; onAnswer({ correct, text: given }); };
  if (f === "choice") {
    const at = order && order.length === q.choices.length ? order : shuffled(q.choices.length);
    const buttons = at.map((i, n) => el("button", { type: "button", class: "qf-choice", onclick: () => give(i === q.correct, q.choices[i]) },
      el("kbd", {}, LETTERS[n]), el("span", {}, q.choices[i])));
    return { el: el("div", { class: "qf-ask", role: "group", "aria-label": "Choices" }, buttons), order: at, focus: () => buttons[0].focus(),
      key(e) {
        const n = "1234".indexOf(e.key) >= 0 ? "1234".indexOf(e.key) : "abcd".indexOf(e.key.toLowerCase());
        if (e.key.length !== 1 || n < 0 || n >= buttons.length) return false;
        buttons[n].click(); return true;
      } };
  }
  if (f === "truefalse") {
    const buttons = [true, false].map((v) => el("button", { type: "button", class: "qf-choice qf-tf", onclick: () => give(v === q.truth, v ? "True" : "False") },
      el("kbd", {}, v ? "T" : "F"), el("span", {}, v ? "True" : "False")));
    return { el: el("div", { class: "qf-ask qf-two", role: "group", "aria-label": "True or false" }, buttons), focus: () => buttons[0].focus(),
      key(e) {
        const k = e.key.toLowerCase();
        if (k !== "t" && k !== "f") return false;
        buttons[k === "t" ? 0 : 1].click(); return true;
      } };
  }
  const input = el("input", { type: "text", class: "qf-input", "aria-label": "The missing words", placeholder: "The missing words",
    autocomplete: "off", autocapitalize: "off", spellcheck: "false" });
  const send = () => { if (input.value.trim()) give(matches(input.value, q.accept), input.value.trim()); else input.focus(); };
  input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); send(); } });
  return { el: el("div", { class: "qf-ask qf-type" }, input, el("button", { type: "button", class: "primary", onclick: send }, "Check", el("kbd", {}, "↵"))),
    focus: () => input.focus(), key: () => false };
}
function verdict(q, given) {
  const f = form(q);
  return el("div", { class: "qf-verdict " + (given.correct ? "right" : "wrong"), role: "status" },
    el("b", { class: "qf-mark" }, given.correct ? "Right" : "Not right"),
    el("div", { class: "qf-rows" },
      given.text && !(given.correct && f !== "cloze") ? el("p", {}, el("span", { class: "m" }, "You answered"), given.text) : null,
      el("p", {}, el("span", { class: "m" }, "Right answer"), key(q)),
      q.answer ? el("p", {}, el("span", { class: "m" }, "Why"), q.answer) : null));
}
function choices(q) {
  const f = form(q);
  if (f === "choice") return el("ol", { class: "qf-list" }, q.choices.map((c, i) => el("li", { class: i === q.correct ? "right" : "" }, c, i === q.correct ? el("span", { class: "m" }, " right") : null)));
  if (f === "truefalse") return el("p", { class: "qf-key" }, el("span", { class: "m" }, "The statement is"), q.truth ? "true" : "false");
  if (f === "cloze") return el("p", { class: "qf-key" }, el("span", { class: "m" }, "Missing words"), q.accept[0],
    q.accept.length > 1 ? el("span", { class: "mute" }, "  (also: " + q.accept.slice(1).join(", ") + ")") : null);
  return null;
}

let serial = 0;
function edit(q) {
  const f = form(q), name = "qf-" + (++serial);
  const area = (title, value, placeholder) => { const t = el("textarea", { "aria-label": title, placeholder }); t.value = value || ""; return t; };
  const line = (title, value, placeholder) => el("input", { type: "text", "aria-label": title, placeholder: placeholder || title, value: value || "" });
  const question = area(f === "open" || f === "choice" ? "Question" : "Statement", text(q),
    f === "cloze" ? `The statement, with ${GAP} where the missing words go` : f === "truefalse" ? "The statement" : "The question");
  const answer = area(f === "open" ? "Answer" : "Explanation", q.answer, f === "open" ? "Its answer" : "Why the right answer is right");
  let extra = null, members = () => ({});
  if (f === "choice") {
    const rows = q.choices.map((c, i) => [el("input", { type: "radio", name, "aria-label": `Choice ${i + 1} is the right one`, ...(i === q.correct ? { checked: true } : {}) }), line(`Choice ${i + 1}`, c)]);
    extra = el("div", { class: "qf-rows-edit" }, el("span", { class: "m" }, "Choices, with the right one marked"), rows.map(([radio, input]) => el("label", { class: "qf-row" }, radio, input)));
    members = () => ({ form: { form: f, choices: rows.map(([, input]) => input.value), correct: Math.max(0, rows.findIndex(([radio]) => radio.checked)) } });
  } else if (f === "truefalse") {
    const radios = [true, false].map((v) => el("input", { type: "radio", name, ...(v === q.truth ? { checked: true } : {}) }));
    extra = el("div", { class: "qf-rows-edit" }, el("span", { class: "m" }, "The statement is"),
      el("span", { class: "qf-row" }, el("label", {}, radios[0], " True"), el("label", {}, radios[1], " False")));
    members = () => ({ form: { form: f, truth: radios[0].checked } });
  } else if (f === "cloze") {
    const missing = line("Missing words", q.accept[0]), also = line("Also accepted", q.accept.slice(1).join("; "), "Also accepted, separated by ;");
    extra = el("div", { class: "qf-rows-edit" }, el("span", { class: "m" }, "The missing words, and other wordings that count"), missing, also);
    members = () => ({ form: { form: f, accept: [missing.value, ...also.value.split(";")].map((a) => a.trim()).filter(Boolean) } });
  }
  const cap = (title) => el("span", { class: "m qf-cap" }, title);
  return { el: el("div", { class: "qf-edit" }, cap(question.getAttribute("aria-label")), question, extra, cap(answer.getAttribute("aria-label")), answer),
    read: () => ({ question: question.value, answer: answer.value, ...members() }) };
}

/* ---------- a new question, of a kind the reader picks ---------- */
const KINDS = [["open", "Open"], ["choice", "Multiple choice"], ["truefalse", "True or false"], ["cloze", "Fill in the blank"]];
const EMPTY = { open: {}, choice: { form: "choice", choices: ["", "", "", ""], correct: 0 }, truefalse: { form: "truefalse", truth: true },
  cloze: { form: "cloze", accept: [""] } };
function compose() {
  let kind = "open", fields = edit({ question: "", answer: "" });
  const box = el("div", { class: "qf-compose" }), picker = el("div", { class: "qf-kinds", role: "group", "aria-label": "Kind" });
  const draw = () => {
    picker.replaceChildren(...KINDS.map(([k, t]) => el("button", { type: "button", "aria-pressed": String(k === kind), onclick: () => {
      if (k === kind) return;
      const had = fields.read();      // what was typed so far goes along to the other kind
      kind = k; fields = edit({ question: had.question, answer: had.answer, ...EMPTY[k] }); draw();
    } }, t)));
    box.replaceChildren(picker, fields.el);
  };
  draw();
  return { el: box, read: () => fields.read() };
}

/* ---------- changing a book's own question, and adding one ---------- */
async function api(path, options = {}) {
  const headers = { "X-Ebook-Processor": "1" };
  if (options.json !== undefined) { options.body = JSON.stringify(options.json); headers["Content-Type"] = "application/json"; }
  const response = await fetch("/api" + path, { ...options, headers });
  const data = await response.json().catch(() => ({ error: "The app gave an unreadable answer." }));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}
let open = null;                        // the window that is open, if one is
function windowFor(title, sub) {
  if (open) open.close();
  const body = el("div", { class: "qf-body" }), foot = el("div", { class: "qf-foot" }), note = el("p", { class: "qf-note", role: "status", hidden: true });
  const root = el("dialog", { class: "qf-dlg", "aria-label": title },
    el("header", {}, el("b", {}, title), el("span", { class: "m" }, sub || ""), el("button", { type: "button", onclick: () => root.close() }, "Close")),
    body, note, foot);
  for (const type of ["keydown", "keyup", "keypress"]) root.addEventListener(type, (e) => e.stopPropagation());   // typing here is not a key of the page
  root.addEventListener("click", (e) => { if (e.target === root) root.close(); });
  root.addEventListener("close", () => { root.remove(); if (open && open.root === root) open = null; });
  document.body.append(root);
  root.showModal();
  const it = { root, body, foot, close: () => root.close(),
    say(text, bad) { note.textContent = text || ""; note.hidden = !text; note.classList.toggle("bad", !!bad); },
    async run(button, work) {         // one request at a time, with its failure said in the window
      const buttons = [...root.querySelectorAll("button")];
      const label = button ? button.textContent : "";
      for (const b of buttons) b.disabled = true;
      if (button) button.textContent = "Working";
      it.say("");
      try { return await work(); }
      catch (e) { it.say(e.message, true); return undefined; }
      finally { for (const b of buttons) b.disabled = false; if (button && button.isConnected) button.textContent = label; }
    } };
  open = it;
  return it;
}
// The paragraphs of the section, to tick the ones an answer rests on. `ticked` are references; `read()` gives the
// ticked ones, or undefined while the reader has not touched the list (the app then keeps what it has).
function passagePicker(paragraphs, ticked, open_) {
  let touched = false;
  const boxes = paragraphs.map((p) => [p.ref, el("input", { type: "checkbox", ...(ticked.includes(p.ref) ? { checked: true } : {}), onchange: () => { touched = true; count(); } })]);
  const summary = el("summary", {});
  const count = () => { const n = boxes.filter(([, b]) => b.checked).length; summary.textContent = `Passage: ${n} of the section's ${boxes.length} paragraph${boxes.length === 1 ? "" : "s"}`; };
  count();
  const box = el("details", { class: "qf-passage", ...(open_ ? { open: true } : {}) }, summary,
    el("p", { class: "qf-hint" }, "The paragraphs the answer rests on. They are shown with the card, under \u201cFrom the book\u201d, and are what a check reads first."),
    paragraphs.map((p, n) => el("label", {}, boxes[n][1], el("span", {}, p.text))));
  return { el: box, read: () => (touched ? boxes.filter(([, b]) => b.checked).map(([ref]) => ref) : undefined),
    set(refs) { for (const [ref, b] of boxes) b.checked = refs.includes(ref); touched = true; count(); box.open = true; } };
}
function verdictBox(v) {
  if (!v) return null;
  return el("div", { class: "qf-verdict " + (v.passed ? "right" : "wrong") },
    el("b", { class: "qf-mark" }, v.passed ? "The check passed it" : "The check objects"),
    v.passed ? (v.minor_note ? el("p", {}, el("span", { class: "m" }, "Remark"), v.minor_note) : null)
      : [el("p", {}, el("span", { class: "m" }, "Why"), v.problem), v.suggested_fix ? el("p", {}, el("span", { class: "m" }, "Suggested"), v.suggested_fix) : null,
        el("p", {}, el("span", { class: "m" }, ""), "The question is kept as you wrote it. Change it, or leave it.")]);
}

async function openEditor({ job, id, onChange }) {
  const told = (what) => { if (onChange) onChange({ id, ...what }); };
  const base = `/jobs/${job}/book-questions/${encodeURIComponent(id)}`;
  let shown;
  try { shown = await api(base); } catch (e) { alert(e.message); return; }
  const w = windowFor("Edit question", shown.section.label);
  function draw(draft, message) {
    const q = draft ? { ...shown, ...draft, ...(draft.form || {}) } : shown;
    const fields = edit(q);
    const passage = passagePicker(shown.paragraphs, draft && draft.supporting_paragraphs ? draft.supporting_paragraphs : shown.supporting_paragraphs, false);
    if (draft && draft.supporting_paragraphs) passage.set(draft.supporting_paragraphs);
    const wish = el("input", { type: "text", id: "qf-wish", "aria-label": "What the model should change", maxlength: 300, placeholder: "Or tell the model what to change: make it harder, ask about the cause instead" });
    const changed = () => ({ ...fields.read(), supporting_paragraphs: passage.read() });
    const save = async (button, andCheck) => {
      const saved = await w.run(button, () => api(base, { method: "PUT", json: changed() }));
      if (!saved) return;
      shown = saved; told({ edited: true });
      if (!andCheck) return w.close();
      const checked = await w.run(button, () => api(base + "/check", { method: "POST", json: {} }));
      if (checked) shown = checked;
      draw(null, checked ? "" : undefined);
    };
    const rewrite = async (button) => {
      const made = await w.run(button, () => api(base + "/rewrite", { method: "POST", json: { wish: wish.value, draft: fields.read() } }));
      if (made) draw(made, "The model's rewrite is in the form. Change it if you like; Save keeps it.");
    };
    wish.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); rewrite(wishButton); } });
    const wishButton = el("button", { type: "button", id: "qf-rewrite", onclick: (e) => rewrite(e.currentTarget) }, "Rewrite");
    const state = shown.left_out_because ? `Left out by the check: ${shown.left_out_because} Once you change it, it is yours and is kept.`
      : shown.added ? "Written by you." : shown.status === "edited" ? "Edited by you." : "";
    w.body.replaceChildren(...[state ? el("p", { class: "qf-hint", id: "qf-state" }, state) : null, fields.el, passage.el,
      el("div", { class: "qf-wish" }, wish, wishButton), verdictBox(shown.verdict)].filter(Boolean));
    w.foot.replaceChildren(
      el("span", { class: "qf-side" },
        shown.has_original ? el("button", { type: "button", class: "quiet", id: "qf-restore", onclick: async (e) => {
          const back = await w.run(e.currentTarget, () => api(base + "/restore", { method: "POST", json: {} }));
          if (back) { shown = back; told({ edited: true }); draw(null, "The run's own wording is back."); } } }, "Put back the original") : null,
        shown.answered ? el("button", { type: "button", class: "quiet", id: "qf-restart", onclick: async (e) => {
          if (!confirm("Start this question over? It will count as never answered: asked again at its checkpoint, or new in Practice if you are past it.")) return;
          const fresh = await w.run(e.currentTarget, () => api(base + "/restart", { method: "POST", json: {} }));
          if (fresh) { shown = fresh; told({ restarted: true }); draw(null, "Started over."); } } }, "Start this card over") : null,
        shown.added ? el("button", { type: "button", class: "quiet", id: "qf-delete", onclick: async (e) => {
          if (!confirm("Delete this question of yours?")) return;
          const gone = await w.run(e.currentTarget, () => api(base, { method: "DELETE" }));
          if (gone) { told({ deleted: true }); w.close(); } } }, "Delete") : null),
      el("span", { class: "qf-side" },
        el("button", { type: "button", id: "qf-check", onclick: (e) => save(e.currentTarget, true) }, "Save and check"),
        el("button", { type: "button", class: "primary", id: "qf-save", onclick: (e) => save(e.currentTarget, false) }, "Save")));
    if (message !== undefined) w.say(message);
  }
  draw(null);
}

async function openAdder({ job, section, onChange }) {
  let data;
  try { data = await api(`/jobs/${job}/sections/${encodeURIComponent(section)}/paragraphs`); } catch (e) { alert(e.message); return; }
  const mode = ["pick", "model", "model_review"].includes(window.AppSettings && AppSettings.values.own_passage) ? AppSettings.values.own_passage : "section";
  const w = windowFor("Add a question", data.section.label);
  const fields = compose(), refs = data.paragraphs.map((p) => p.ref);
  const passage = passagePicker(data.paragraphs, mode === "section" ? refs : [], mode === "pick");
  const byModel = mode === "model" || mode === "model_review";
  const how = el("p", { class: "qf-hint" }, mode === "section" ? "Its passage is the whole section, unless you tick paragraphs below."
    : mode === "pick" ? "Tick the paragraphs the answer rests on."
    : "The model chooses the paragraphs the answer rests on when you save" + (mode === "model_review" ? ", and shows them to you first." : "."));
  passage.el.hidden = byModel;
  let reviewed = false;
  w.body.replaceChildren(fields.el, how, passage.el);
  w.foot.replaceChildren(el("span", { class: "qf-side" }), el("span", { class: "qf-side" },
    el("button", { type: "button", class: "primary", id: "qf-save", onclick: async (e) => {
      const picked = passage.read();
      if (mode === "pick" && (!picked || !picked.length)) return w.say("Tick at least one paragraph the answer rests on.", true);
      const body = { section, ...fields.read(), passage: mode, supporting_paragraphs: byModel && !reviewed ? undefined : picked };
      const made = await w.run(e.currentTarget, () => api(`/jobs/${job}/book-questions`, { method: "POST", json: body }));
      if (!made) return;
      if (made.review) {              // the model's choice, for the reader to confirm or change
        reviewed = true; passage.el.hidden = false; passage.set(made.review);
        how.textContent = "The model chose these paragraphs. Change them if you like, then save.";
        return;
      }
      if (onChange) onChange({ id: made.id, added: true });
      w.close();
    } }, "Save")));
}

document.head.append(el("style", {}, `
.qf-kinds { display: flex; flex-wrap: wrap; margin-bottom: 4px; }
.qf-kinds button { font: 12px/1 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; border: 1px solid var(--ink); border-radius: 0; background: transparent; color: var(--ink); padding: 9px 12px; margin-right: -1px; cursor: pointer; }
.qf-kinds button[aria-pressed=true] { background: var(--ink); color: var(--paper); }
.qf-compose { display: grid; gap: 8px; }
.qf-dlg { padding: 0; border: 1px solid var(--ink); border-radius: 0; background: var(--paper); color: var(--ink); font: 15px/1.45 var(--grot);
  width: min(760px, calc(100vw - 24px)); max-height: calc(100vh - 24px); max-width: none; }
.qf-dlg[open] { display: grid; grid-template-rows: auto minmax(0, 1fr) auto auto; }
.qf-dlg::backdrop { background: rgba(0, 0, 0, 0.32); }
.qf-dlg > header { display: flex; align-items: baseline; gap: 14px; padding: 12px 12px 12px 20px; border-bottom: 1px solid var(--ink); }
.qf-dlg > header b { font: 500 20px/1.2 var(--grot); letter-spacing: -0.03em; }
.qf-dlg > header .m { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 12px/1.5 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; color: var(--mute); }
.qf-dlg button { font: 12px/1 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; border: 1px solid var(--ink); border-radius: 0; background: transparent; color: var(--ink); padding: 10px 13px; cursor: pointer; }
.qf-dlg button:hover:not(:disabled) { background: var(--ink); color: var(--paper); }
.qf-dlg button.primary { background: var(--ink); color: var(--paper); }
.qf-dlg button.primary:hover:not(:disabled) { background: var(--blue); border-color: var(--blue); }
.qf-dlg button.quiet { border-color: transparent; text-decoration: underline; text-underline-offset: 3px; padding-left: 0; padding-right: 0; }
.qf-dlg button.quiet:hover:not(:disabled) { background: transparent; color: var(--blue); }
.qf-dlg button:disabled { opacity: 0.5; cursor: default; }
.qf-dlg .qf-kinds button[aria-pressed=true]:hover { background: var(--ink); color: var(--paper); }
.qf-body { overflow: auto; padding: 18px 20px; display: grid; gap: 14px; align-content: start; }
.qf-body textarea { display: block; width: 100%; min-height: 76px; resize: vertical; font: 16px/1.45 var(--grot); color: var(--ink); background: var(--sheet); border: 1px solid var(--ink); border-radius: 0; padding: 9px 11px; }
.qf-hint { margin: 0; color: var(--mute); font-size: 13.5px; line-height: 1.4; }
.qf-note { margin: 0; padding: 10px 20px; border-top: 1px solid var(--ghost, var(--ink)); font-size: 14px; }
.qf-note.bad { background: var(--ink); color: var(--paper); }
.qf-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; padding: 12px 20px; border-top: 1px solid var(--ink); }
.qf-side { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.qf-wish { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
.qf-wish input { font: inherit; color: var(--ink); background: var(--sheet); border: 1px solid var(--ink); border-radius: 0; padding: 9px 11px; min-width: 0; }
.qf-passage summary { cursor: pointer; font: 12px/1.5 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; }
.qf-passage .qf-hint { margin: 8px 0; }
.qf-passage label { display: flex; gap: 10px; align-items: baseline; padding: 5px 0; cursor: pointer; font-size: 14px; line-height: 1.4; }
.qf-passage input { flex: none; transform: translateY(2px); }
.qf-edit { display: grid; gap: 8px; }
.qf-edit input[type=text] { font: inherit; font-size: 15px; color: var(--ink); background: var(--sheet); border: 1px solid var(--ink); border-radius: 0; padding: 8px 10px; width: 100%; min-width: 0; }
.qf-edit > .qf-cap { margin-top: 4px; }
.qf-rows-edit { display: grid; gap: 6px; } .qf-edit > .qf-cap, .qf-rows-edit > .m { font: 11.5px/1.5 var(--mono); letter-spacing: 0.05em; text-transform: uppercase; color: var(--mute); }
.qf-row { display: flex; gap: 10px; align-items: center; } .qf-row label { display: inline-flex; gap: 6px; align-items: center; margin-right: 14px; cursor: pointer; }
.qf-gap { display: inline-block; min-width: 4.5em; border-bottom: 0.12em solid currentColor; margin: 0 0.15em; text-align: center; line-height: 1.05; }
.qf-gap.on { min-width: 0; padding: 0 0.2em; color: var(--blue); border-bottom-color: var(--blue); }
.qf-ask { display: grid; gap: 8px; margin: 4px 0 0; }
.qf-ask.qf-two { grid-template-columns: repeat(2, minmax(0, 1fr)); max-width: 420px; }
.qf-ask.qf-type { grid-template-columns: minmax(0, 1fr) auto; max-width: 560px; }
.qf-ask .qf-choice { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; align-items: baseline; width: 100%; text-align: left; text-transform: none;
  letter-spacing: 0; font: inherit; font-size: 17px; line-height: 1.4; color: var(--ink); background: var(--sheet); border: 1px solid var(--ink); border-radius: 0; padding: 13px 16px; cursor: pointer; }
.qf-ask .qf-choice:hover, .qf-ask .qf-choice:focus-visible { background: var(--ink); color: var(--paper); outline: none; }
.qf-ask .qf-choice kbd { font: 12px/1 var(--mono); letter-spacing: 0.06em; border: 1px solid currentColor; padding: 3px 6px; opacity: 0.75; margin: 0; }
.qf-ask .qf-input { font: inherit; font-size: 18px; line-height: 1.3; color: var(--ink); background: var(--sheet); border: 1px solid var(--ink); border-radius: 0; padding: 12px 14px; width: 100%; min-width: 0; }
.qf-verdict { border: 1px solid var(--ink); border-left-width: 10px; padding: 14px 18px; margin: 4px 0 0; }
.qf-verdict.right { border-left-color: var(--blue); }
.qf-verdict .qf-mark { display: block; font: 12px/1.4 var(--mono); letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 8px; }
.qf-verdict.right .qf-mark { color: var(--blue); }
.qf-verdict p { margin: 6px 0 0; display: grid; grid-template-columns: 124px minmax(0, 1fr); gap: 12px; align-items: baseline; text-wrap: pretty; }
.qf-verdict p .m, .qf-key .m { font: 11.5px/1.5 var(--mono); letter-spacing: 0.05em; text-transform: uppercase; color: var(--mute); }
.qf-list { margin: 6px 0 0; padding-left: 1.6em; list-style: upper-alpha; }
.qf-list li { margin: 2px 0; color: var(--mute); } .qf-list li.right { color: var(--ink); font-weight: 600; }
.qf-list li .m { font: 11px/1 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--blue); font-weight: 400; }
.qf-key { margin: 6px 0 0; } .qf-key .m { margin-right: 10px; }
@media (max-width: 560px) { .qf-verdict p { grid-template-columns: minmax(0, 1fr); gap: 0; } }
`));

window.QForms = { form, label, key, plain, heading, matches, ask, verdict, choices, edit, compose, openEditor, openAdder, GAP,
  get isOpen() { return !!open; } };
})();
