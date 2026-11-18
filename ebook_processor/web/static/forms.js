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
  return { el: el("div", { class: "qf-edit" }, question, extra, answer), read: () => ({ question: question.value, answer: answer.value, ...members() }) };
}

document.head.append(el("style", {}, `
.qf-edit { display: grid; gap: 8px; }
.qf-edit input[type=text] { font: inherit; font-size: 15px; color: var(--ink); background: var(--sheet); border: 1px solid var(--ink); border-radius: 0; padding: 8px 10px; width: 100%; min-width: 0; }
.qf-rows-edit { display: grid; gap: 6px; } .qf-rows-edit > .m { font: 11.5px/1.5 var(--mono); letter-spacing: 0.05em; text-transform: uppercase; color: var(--mute); }
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

window.QForms = { form, label, key, plain, heading, matches, ask, verdict, choices, edit, GAP };
})();
