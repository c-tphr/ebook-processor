# The knowledge system: plan

This is the working plan for the knowledge system, agreed with the project owner in a six-round interview on 2026-11-18. It is written for whoever builds it, so that the work can start from this file alone. It holds what was decided, the phases, a design for each phase, and the questions that are still open. Keep it up to date as phases are finished: mark what is done, and move what was passed over to `DEFERRED.md`.

**Status: Phases 1 and 2 are on `main`. Phase 3 (index notes, projects, more of Markdown in the editor) is being built in the order of section 6d.**

## 1. What it is for

The app today prepares a book, lets you read it with checkpoints, and keeps highlights, notes and questions per book. The knowledge system grows that into a place where reading becomes your own thinking, in the spirit of Zettelkasten: notes in your own words, one idea each, linked to each other and to the passages they came from.

The owner's main job for it is **developing their own ideas**. Finding what was read, feeding projects, and remembering are served too, but they do not drive the design.

## 2. Decisions

Every row is the owner's answer. Do not reopen these without asking.

| Subject | Decision |
| --- | --- |
| Kinds of note | Idea notes ("permanent notes": one idea, own words, cites its sources), index notes (gather and order other notes), quick notes ("fleeting notes", in an inbox), project notes (belong to one project) |
| Where notes live | Plain Markdown files in a folder. The files are the truth; the app reads and writes them |
| The folder | A new folder the app creates. It can be opened in Obsidian as a vault |
| File names | The note's title: `Prices carry information.md`, linked as `[[Prices carry information]]`. When a note is renamed in the app, the app rewrites the links to it |
| Book material | One Markdown file per book, written and kept current by the app: its highlights and notes, each with a stable anchor, so that an idea note can cite one exact highlight. The reader does not edit these files |
| Where the writing happens | In the app. Obsidian is an optional second window onto the same files |
| The editor | One pane that formats as you type (like Obsidian's live preview), built on CodeMirror 6 shipped inside the app |
| Obsidian features to understand | Link details (`[[Note\|words]]`, `[[Note#Heading]]`, `[[Note#^id]]`), embeds (`![[Note]]`, `![[Note#^id]]`), properties (the block at the top of a file, shown as fields), callouts, footnotes, tables, task lists |
| Links | A plain link inside a sentence. No named relations |
| Other order | Tags, and index notes. No numbered "trains of thought". No folders per subject, and no folders per kind of note |
| How an idea note starts | On the spot in the reader, from a highlight (editor beside the text, citation filled in). In a later session, in the Process view. From a checkpoint (the section note offered there can be made an idea note). From nothing, on any page |
| The Process view | Every highlight, passage note and quick note starts as unprocessed, grouped by book and section. Each ends as: an idea note written from it, linked to an existing note, or set aside (one click, also for a whole section) |
| Views | A graph, the neighbourhood of a note, old and unlinked notes brought back, a timeline. Search and back-links are taken as given |
| Projects | A workspace: a page with the project's description, the notes and passages gathered for it (by linking, not moving), its own project notes, and an outline that can be exported as Markdown. Active or finished. It does **not** replace "what I am working on" in Settings for steering questions |
| The model | Related notes are found locally at all times (no model, nothing sent). "Find connections" asks the model on request and says why each fits. Answers to a question come from the owner's notes and highlights, with citations, and say so when the material does not cover it. The model does **not** draft idea notes |
| Notes in Practice | On request, per note: "Make a question" proposes one from the note, in the ticked kinds, to accept, reword or discard, as for a highlight. Or the owner writes one |
| Quick notes: capture | From any page of the app (a key and a button; in the reader it records the place in the book). And any Markdown file dropped into the inbox folder is picked up |
| The per-book Notebook page | It stays as it is, and is also reached from a new third main area, **Notes**, beside Library and Practice |
| Delivery | Each phase is usable by itself, goes to the development branch, and to `main` when the owner says. Before each phase, interview the owner on what that phase raises |

Choices made by the builder and confirmed by the owner ("This looks good"):

- **Folder layout.** A note's kind is a property inside the file. Two sub-folders exist for practical reasons: `Books` (files the app keeps) and `Inbox` (quick notes, and where dropped files are picked up). Everything else is at the top level.
- **Edits made outside the app.** The app reloads a note that changed on disk. If the note also has unsaved changes in the app, it asks which to keep.
- **Phase 1 editor scope.** Headings, emphasis, lists, quotes, code, links with all three link details, tags, properties. Embeds, callouts, footnotes, tables and task lists come in Phase 3.
- **Local related notes arrive in Phase 2**, not with the model's help in Phase 5.

## 3. Phases

| Phase | What the owner can do when it is done |
| --- | --- |
| **1. Foundation** | Open the Notes area. Write idea notes and quick notes in the new editor, with links that complete as you type, back-links, tags, properties and search. Start an idea note from a highlight, from a checkpoint's section note, or from nothing. Capture a quick note from any page; files dropped in the inbox are picked up. Every book has its file in the folder. Open the folder in Obsidian and see the same notes |
| **2. Process** | Work through unprocessed highlights, passage notes and quick notes, grouped by book and section. Each ends as a new idea note, a link to an existing one, or set aside. The local "related notes" list is beside what is being written |
| **3. Index notes and projects** | Index notes as entry points. A project page with description, gathered notes and passages, its own notes, and an outline to arrange and export. Embeds, callouts, footnotes, tables and task lists in the editor |
| **4. Seeing and revisiting** | The neighbourhood of a note, the graph, old and unlinked notes brought back, the timeline |
| **5. The model** | "Find connections" with a reason for each. A question answered from notes and highlights, cited. "Make a question" on a note, into Practice |

Known risks, told to the owner:

- The editor is the largest risk. Formatting as you type on CodeMirror is most of Phase 1, and it is the app's first outside code in the browser.
- Phase 1 is big. If it must be split: first the folder, book files, plain notes and search; then the editor and capture from the reader.

## 4. Design

This section is the builder's. It can change as the work shows what is right; the decisions in section 2 cannot.

### 4.1 The folder

```
<notes folder>/
  Books/
    The Lighthouse Keeper's Almanac.md      written by the app, one per book
  Inbox/
    2026-11-18 1530 a thought.md            quick notes; dropped files are picked up here
  Prices carry information.md               an idea note
  What I know about pricing.md              an index note
  Pricing talk.md                           a project (Phase 3)
  Pricing talk - outline.md                 a project note (Phase 3)
```

- Where the folder is: a setting, with a default beside the app's data folder (the data folder defaults to `~/ebook-processor-runs`; propose `~/ebook-processor-notes`). Ask in the Phase 1 interview. The server learns it at start (`serve --notes PATH`) and from Settings.
- A title must be a valid file name on Windows, macOS and Linux: no `\ / : * ? " < > |`, no leading or trailing dot or space, not a reserved Windows name, at most about 120 characters. Two notes cannot have the same title in any letter case (macOS and Windows file systems do not tell them apart).
- Never write outside the folder. Resolve every path and check it is inside, as the server already does for downloads.
- Writes are atomic (write a temporary file, then rename), as `pipeline.write_json` does.

### 4.2 A note's file

```markdown
---
id: n7f3a2c91
kind: idea            # idea | index | quick | project-note | project | book
created: 2026-11-18T15:30:00Z
tags: [pricing, markets]
sources:
  - "[[Books/The Lighthouse Keeper's Almanac#^h3f9a]]"
---
Prices carry information that no single person holds. ...
```

- **Front matter** is YAML, kept to a small, flat subset that the app can read and write without a YAML library (the app has no runtime dependencies beyond `genanki`): scalars, inline lists, and a block list of strings. Unknown keys written by Obsidian or by hand are kept as they are, in order. Write a careful reader and writer for this subset with tests; do not add PyYAML unless the subset proves too small.
- `id` never changes and is what the app's own records point to (the Process status of an item, a practice card made from a note). The title can change.
- `kind` is the only thing that says what a note is. A file without front matter (dropped into the inbox, or made in Obsidian) is a quick note when it is in `Inbox`, an idea note otherwise; the app adds front matter the first time it saves the note.
- Tags: the `tags` property and `#tag` in the text both count, as in Obsidian.
- Links: `[[Title]]`, `[[Title|shown]]`, `[[Title#Heading]]`, `[[Title#^blockid]]`, and `[[Books/Title#^id]]` for a book. Resolve as Obsidian does: by file name without `.md`, case-insensitively, path optional when the name is unique.

### 4.3 A book's file

Written by the app whenever the book's highlights or notes change (`notes.json` stays the store the reader and the notebook use; the file is made from it, not the other way round).

```markdown
---
id: b-<job id>
kind: book
title: The Lighthouse Keeper's Almanac
authors: [...]
app: /read/<job id>
---
> [!info] This file is kept by ebook-processor. Edits made here are overwritten.

## The Lamp Room

### Section label

> The lamp is lit at sunset and put out at sunrise, whatever the weather. ^h3f9a

A note on that highlight. ^h3f9a-note

A note on a paragraph. ^n81c2
```

- Every highlight and note ends with a block id made from its id in `notes.json` (`^h3f9a`). Obsidian block ids allow letters, digits and `-`. This is what `[[Books/Title#^h3f9a]]` points to, in the app and in Obsidian.
- Order is reading order, under the book's chapters and sections (the same outline `notes.notebook` builds).
- When a highlight is removed, its block goes; links to it then point to nothing. The app shows such a link as broken and keeps the note's own text. When highlights merge (`notes.add_highlight` joins touching ones), keep the surviving id and, if cheap, an alias for the one that went.
- A book whose title changes or is deleted: decide in the Phase 1 interview (keep the file, or mark it).
- Checkpoint answers and questions are not in the file in Phase 1 unless the owner asks for them (open question).

### 4.4 The server

New module `ebook_processor/vault.py` (the folder, files, front matter, links, the index) and `ebook_processor/bookfile.py` (a book's file from `notes.json` and the manifest). Keep `notes.py` as it is.

- **The index.** On start, and when a file's modification time changes, read every `.md` file and keep in memory: id, title, kind, tags, links out (with the sentence around each, for back-links), headings, block ids, first lines, modified time. A few thousand small files are read in well under a second; no database. Search is a scan of this index plus the file texts (also over book highlights, which are in the book files).
- **Changes on disk.** No file watcher (no dependency, and they differ per system). Check modification times when the Notes area asks for anything, at most once a second. This is enough to notice Obsidian's edits.
- **Renaming** rewrites `[[Old]]`, `[[Old|...]]`, `[[Old#...]]` in every file that links to the note, then renames the file. Do it under the jobs lock, and write each file atomically.
- **Routes**, all under `/api/vault/`: list and search notes; read one (text, properties, back-links); create; save (with the modification time the editor loaded, so that a change on disk in between is detected and reported instead of overwritten); rename; delete (move to a `.trash` folder inside the vault, as Obsidian can, rather than removing); the inbox; tags. Every route needs the CSRF header like the others.
- `Jobs.notes_do` and the question endpoints call a hook that rewrites the book's file after a change.
- The server's `PAGES` dictionary lists every static file by name and content type; a new file must be added there, and to the list in `tests/test_reading.py::test_pages_are_the_ones_the_app_started_with`. `pyproject.toml` packages `web/static/*` (one level: put new static files directly in `web/static/`, or extend the pattern).

### 4.5 The pages

- A third main area, **Notes**, in `index.html` (`#/notes`, `#/notes/<id>`), beside Library (`#/`) and Practice (`#/practice`). The page is vanilla JavaScript with a small `h()` helper, like the rest; keep to that.
- The editor is its own script, `web/static/editor.js`: CodeMirror 6 with the Markdown language, bundled into one file. Build it once with `esbuild` from an `editor/` source folder in the repository (Node 22 and npm are available in the cloud environment; the user never needs them) and commit the built file. Record the exact package versions and the build command in the README's developer section, and keep the licence notices (CodeMirror is MIT).
- Formatting as you type is done with CodeMirror decorations over the Markdown syntax tree: hide the marks of a construct unless the selection touches it, and style the content. Start from headings, emphasis, strong, inline code, links and wiki links, lists, quotes, code blocks, tags; the front matter is shown as a properties panel above the text, not as text. Completion for `[[` comes from the index (titles, then `#` headings and `^` blocks of the chosen note).
- A click on a link opens the note; a link to a book block opens the reader at the highlight (the side reader from the notebook can be reused: `/read/<id>?pane=1&go=c:w`).
- In the reader, a highlight's menu gets "Idea note": it opens the editor in the drawer or beside the text with `sources` filled in. The offer after a checkpoint gets the same.
- Quick note: a key and a button on every page (the top strip in `index.html`, the bar in `reader.html`), a small box, saved to `Inbox`.
- Settings gets a **Notes** tab (the folder, and whatever the interview adds). `AppSettings` stores short values in `<data>/reader-settings.json`; long texts need the `LONG_SETTINGS` list on the server.

### 4.6 Later phases, in brief

- **Phase 2, Process.** Status per item (highlight id, note id, quick note id): unprocessed, noted (with the idea note's id), linked (with the note's id), set aside. Keep it in the app's data folder per book (`process.json`) and in the vault for quick notes (a property). The view is grouped by book and section and shows the passage, with the editor beside it. Local related notes: score other notes and highlights by shared rare words and shared tags (a small TF-IDF over the index, recomputed lazily); no model.
- **Phase 3, index notes and projects.** An index note is a note with `kind: index`. A project is a note with `kind: project` plus properties (`status`, `description`); its page lists what links to it or is listed in its outline, and exports the outline with the notes' text. Embeds are rendered by the editor as a block widget. Callouts, footnotes, tables and task lists are more decorations.
- **Phase 4, views.** Neighbourhood: two steps out from a note, as a small diagram. Graph: all notes; draw it on a canvas with a simple force layout written for the app (no library) unless it proves poor. Old notes: not opened for long, or without links. Timeline: created and edited notes, highlights made, by day.
- **Phase 5, the model.** Reuse `llm.py` (`default_backend`, the two tools, retries, the connection watch) and the pattern of `highlight_questions.py`: instructions in `instructions/*.md`, a reply schema, a check of the reply, one retry with feedback. For answers: pick candidate notes and highlights locally (the same scoring as related notes), send those with the question, and require citations by id; verify each citation exists before showing the answer. For questions from a note: `hq.propose` with the note as the passage. Everything is on request; say on the page what is sent.

## 5. How to work on this

These are the standing rules of this project; they are here so that the work can go on without the earlier conversation.

- **Repository and branch.** `c-tphr/ebook-processor`. Develop on `claude/dreamy-allen-6un3tt` and push there. Push to `main` only when the owner says so, except that a minor, self-contained change may go to `main` once the tests pass (the owner's standing permission). A phase is not minor. Do not open a pull request unless asked. `c-tphr/eink-learner-aid` is not touched.
- **Interview first.** For product questions, ask the owner with pros, cons and a recommended option first, in plain language; make technical decisions yourself and say which you made. Before each phase, ask what that phase raises.
- **Tests.** `python3 -m pytest -q tests` runs everything (about three minutes). The browser tests need `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers` here and are skipped without Playwright. `tests/scripted.py` is the stand-in for the models; `tests/browser/conftest.py` has the app and reader fixtures. GitHub runs nine jobs after every push (Python on Linux, Windows and macOS; Chromium, Firefox and WebKit); wait for them before saying a push is good, and read the failed jobs' logs when one fails: they have found real bugs that passed locally.
- **Real model.** Claude Code is available in the cloud environment for a few real calls to check what a model actually writes. Keep them few and say what was seen.
- **Writing.** Plain English in everything the repository holds. Comments are short and say what the code is for. The README gets a section for each feature; `DEFERRED.md` records what was passed over and what is not yet verified.
- **Commits** end with the co-author and session lines used in this repository's history. No model name anywhere else.
- **Windows.** The app runs on Windows. File names, path separators, atomic renames over an existing file (`os.replace`), and files open in another program all matter for the vault. Test them.

## 6. Open questions for the Phase 1 interview

Ask these before building Phase 1. Recommended answers are the builder's.

1. **Where the folder is.** A default (`~/ebook-processor-notes`), chosen in Settings, movable later? Recommended: yes to all three; moving is "point the app at another folder", the app does not copy files.
2. **The name of the area and of the kinds in the app.** "Notes" with "Idea notes", "Index notes", "Quick notes", "Project notes"? Or Zettelkasten's words (permanent, fleeting)?
3. **What a new idea note looks like.** Only a title and text? A prompt to write one idea in your own words? Is the title required before the first save (it is the file name)? Recommended: an untitled note is saved to the inbox under a time-based name until it gets a title.
4. **Citing a source.** When an idea note starts from a highlight: is the highlight quoted in the note's text, linked in a "sources" property, or both? Recommended: a link in the text under the note (`Source: [[Books/...#^id]]`) and in `sources`, with the quotation shown on hover and beside the editor, not copied.
5. **What goes into a book's file.** Highlights and their notes, passage, section, chapter and book notes are settled. Also checkpoint answers? Questions from highlights? Recommended: not in Phase 1.
6. **Quick notes.** The key for capture; whether a quick note keeps a link to where in which book it was written; how a quick note becomes an idea note (in place, by giving it a title and a kind) before the Process view exists.
7. **Search.** What it searches by default (notes, or notes and book highlights), and whether it is one box at the top of the Notes area that also finds titles to jump to.
8. **Deleting.** To a trash folder inside the vault (recommended), with a way to bring a note back.
9. **A book removed from the library.** Its file in `Books` stays (recommended: idea notes cite it), marked as no longer in the library.
10. **Splitting Phase 1.** Deliver it whole, or first files and search, then the editor and the reader's "Idea note"? Recommended: whole, unless the editor takes long; then tell the owner and split.

## 6a. Answers from the Phase 1 interview

Filled in as the owner answers. Questions of section 6 that have an answer here are closed.

| Question | Answer |
| --- | --- |
| Names (2) | Plain names. The area is "Notes"; the kinds are "Idea note", "Index note", "Quick note", "Project note" |
| A note without a title (3) | It waits in the inbox under a name made from the date and its first words. Giving it a title moves it out of the inbox and names its file |
| Citing a source (4) | A link, not a copy: the note gets a line `Source: [[Book#^highlight]]` and the same link in its `sources` property. The highlighted words are shown beside the editor and on hover over the link |
| Where the folder is (1) | `ebook-processor-notes` in the home folder, made the first time Notes is opened. Settings shows it and lets the owner point the app at another folder; the app does not move files |
| What a book's file holds (5) | Only what the owner marked and wrote: highlights, their notes, and notes on paragraphs, sections, chapters and the book. No questions, no checkpoint answers, no link back into the app |
| A quick note becomes an idea note (6) | In place: open it from the inbox, give it a title, rewrite it, and "Make this an idea note". One note, one file |
| Search (7) | One box over notes of every kind and the highlights and passage notes of every book, results grouped with notes first, titles offered to jump to, filters by kind, tag and book. Searching the full text of every book is a TODO for later, recorded in `DEFERRED.md` |
| Deleting (8) | The file moves to a `.trash` folder inside the notes folder. Links to it show as broken. The Notes area lists the trash and can put a note back |
| "Idea note" in the reader | The editor opens beside the text, as the reader sits beside the notebook today; over the text in a narrow window |
| The Notes front page | The inbox on top (quick and untitled notes, with a count), then the notes worked on last, with tags and books beside them. Search and "New note" always at the top |
| The capture key | Ctrl/Cmd + . on every page, also while typing in another field, and a button at the top of every page |
| A book removed from the library (9) | Its file stays, with a line at the top saying the book is no longer in the library. Links keep working in the folder; they no longer open the reader |

The Phase 1 interview is complete. Question 10 (splitting Phase 1) was not asked: build it whole, in the order below, and tell the owner if the editor takes long.

## 6b. Phase 1: order of work

Each step ends with its tests passing. Tick a step here when it is pushed.

1. [x] `ebook_processor/vault.py`: the folder (make it, `Books`, `Inbox`, `.trash`); safe titles and file names; the front-matter reader and writer (small YAML subset, unknown keys kept); parsing links, tags, headings and block ids; the in-memory index with a refresh by modification time; create, read, save (refusing when the file changed on disk since it was loaded), rename with links rewritten, delete to trash and restore; search over notes and book files. Tests in `tests/test_vault.py`, including Windows-unsafe names and letter case.
2. [x] `ebook_processor/bookfile.py`: a book's file from `notes.json` and the manifest, with block ids; rewritten after every change to the book's highlights and notes; marked when the book is removed. Tests.
3. [x] Server: the notes folder (`serve --notes`, the `notes_folder` setting, default `~/ebook-processor-notes`), routes under `/api/vault/`, the hook that rewrites a book's file, book files written for the books already in the library when Notes is first opened. Tests over HTTP.
4. [x] The editor: `editor/` source, CodeMirror 6 bundled with esbuild into `web/static/editor.js`, added to `PAGES`. Formatting as you type for headings, emphasis, strong, inline code, code blocks, lists, quotes, links, wiki links (all three details), tags; `[[` completion from the index; the properties panel. Build command and versions in the README's developer section.
5. [x] The Notes area in `index.html`: front page (inbox, recent, tags, books), search with filters, a note's page (editor, properties, back-links, the source passage beside it), new idea note, "Make this an idea note" for a quick note, rename, trash and restore, the "changed on disk" question. A Notes tab in Settings (the folder).
6. [x] Capture: Ctrl/Cmd + . and a button on every page (both HTML files), saved to the inbox, with the place in the book when made in the reader.
7. [x] The reader: "Idea note" on a highlight, with the editor beside the text and the source filled in; the same from the section note offered after a checkpoint.
8. [x] Browser tests for 5 to 7 in the three engines; README section "Notes"; `DEFERRED.md`; a look at the folder in real Obsidian is the owner's (not available here): say so.

What Phase 1 turned out to be, where it differs from the design above:

- **Where the folder is.** `ebook-processor-notes` beside the data folder, which is the home folder for the default data folder. `serve --notes FOLDER` stores the folder as the setting `notes_folder`; there is one source of truth.
- **A note is addressed by its `id`.** A file with no id of its own (dropped in, or made in Obsidian) has a provisional one made from its path until the app first saves it.
- **Nothing is kept until something is written.** "New note" and "Idea note" open a draft; the file is made by the first save. An untitled note is then in the inbox under the date and its first words.
- **A quick note written in the reader** keeps its place in two properties, `book: "[[Title]]"` and `place: "chapter:word"`, not in its text.
- **The pane beside the text in the reader** is the same component as a note's page (`Notes.mount` in `notes.js`), in a column that the text makes room for.
- **Links in any property count as links**, not only those in `sources`.
- **Titles also exclude `# ^ [ ]`**, which would break a link to the note.
- **Routes** are as planned, plus `POST /api/vault/cite` (the link that cites a highlight or a note of a book, making the folder and the book's file when needed) and `GET /api/vault/notes/<id>/token` (has the file changed).
- **The editor** was built without `@codemirror/lang-markdown`'s `markdown()` helper, which pulls in the HTML, CSS and JavaScript languages; the language is assembled in `editor/src/index.js` instead. The built file is about 360 KB and is loaded only when a note is opened.
- **What was passed over** is in `DEFERRED.md` under "The notes folder".

For Phase 2, start from: `vault.py` (`Vault.search`, `Vault.backlinks`, `Note.passages`), `Jobs._note` and `Jobs.vault_cite` in the server, `Notes.mount` in `notes.js`, and `showNotes` in `index.html`.

## 6c. Phase 2: answers of the interview, and the order of work

Asked on 2026-11-18, after Phase 1 went to `main` (`6cb78e6`).

| Question | Answer |
| --- | --- |
| What already exists | Nothing to carry over: the owner has no older highlights to protect. Everything marked in a book waits to be processed; there is no cut-off date |
| How to work | A list grouped by book and section; the chosen item's passage and the editor open beside the list |
| Linking to an existing note | Two buttons each time: "Add as a source" (the citation is added to the note, done) and "Open and write" (the same, and the note opens to write about it) |
| A highlight and its note | One item, processed together. An idea note started from it begins with the note's words |
| Items that a note already cites | Processed automatically, however the citation was made (reader, Process view, by hand, Obsidian). When the citing note goes, the item waits again |
| One highlight, several ideas | It leaves the waiting list after the first note, shows under Processed with the notes that cite it, and "Another idea note" starts a second |
| Set aside | Kept in a list; one click brings back an item, a section or a book |
| Related notes | Notes first, then passages of any book, found locally. On a note: open, insert a link, add the current passage as a source. On a passage: see it, cite it too |
| Quick notes | Their own group at the top of the Process view; the inbox stays |
| Where it is reached | "Process" with its count on the Notes front page, and on each book's page for that book. No other reminders |
| Order | A switch: by book (last read first, reading order inside) or oldest first |

Builder's choices: every note written in a book is an item (on a paragraph, a section, a chapter, the book), not only highlights. "Processed" is not stored: it is read from the links of the notes. "Set aside" is stored, per book in `process.json` in the book's folder, and for a quick note in its property `aside`. A quick note is added to an existing note by appending its words there and moving the quick note to the trash. Related notes are scored by shared uncommon words and shared tags, and the list says which words.

Order of work:

1. [x] `vault.py`: which notes cite which block of a book; related notes and passages; a property set by the app. `process.py`: a book's items in reading order, set aside. Tests.
2. [x] Server: `/api/process` (the view), set aside and bring back, add to an existing note, `/api/vault/related`. Tests over HTTP.
3. [x] The Process view in `index.html` (`#/notes/process`), its entry on the Notes front page and on a book's page; related notes in `notes.js`.
4. [x] Browser tests, README, `DEFERRED.md`.

## 6d. Phase 3: answers of the interview, and the order of work

Asked on 2026-11-19, after Phase 2 went to `main` (`dcf0eda`).

| Question | Answer |
| --- | --- |
| Writing an index note | Free text with links: an ordinary note of the kind "Index note" |
| Adding to an index, and where indexes show | Only by writing in the index (no "Add to an index" button). Index notes are listed on the Notes front page |
| What a project has gathered | It is in the project's own file, as links. A gathered note is not changed |
| The outline | A nested list under "Outline" in the project's file; entries are a note, a passage, or a line of the owner's. Arranged on the project's page, and editable as text |
| Export | The outline with the notes' text: headings of the outline as headings, each note's text under its entry, passages quoted with their book, links as plain words |
| Project notes | Ordinary files at the top of the folder, of the kind "Project note", tied to their project by a property. Listed on the project's page; kept out of the front page's recent notes |
| "Add to a project" | On a note's page only. Passages are gathered by writing their link in the project's file |
| A highlight gathered for a project | It is not processed by that: it keeps waiting until a note of the owner's own cites it |
| Where projects are found | A section of the Notes front page: the active ones, "New project", and a link to the finished ones |
| Finished | The project leaves the active list and the "Add to a project" menu; its page and notes stay as they are and it can be reopened |
| Tables | Shown as a table while the cursor is elsewhere; edited as Markdown text |

Builder's choices: a project is a note of the kind `project` with the property `status` (`active` or `finished`); its file has the sections `## Gathered` (a list of links) and `## Outline` (a nested list), which the app finds by their headings. A project note carries `project: "[[Project]]"`. The outline is arranged on the page by working on the editor's text, so it is saved like anything typed. Embeds show the embedded text in a box under their line; callouts get a label and a rule; footnotes show as raised marks with their text on hover; task boxes can be ticked.

Order of work:

1. [ ] `vault.py`: sections of a file, gathering into a project, a project's export, what a link embeds. Server: projects and index notes on the front page, `gather`, `export.md`, project notes. Tests.
2. [ ] The editor: embeds, callouts, footnotes, tables, task lists.
3. [ ] `notes.js` and `index.html`: index notes, the project page (status, gathered, outline, project notes, export), "Add to a project", the front page.
4. [ ] Browser tests, README, `DEFERRED.md`.

## 7. Record of progress

| Date | What |
| --- | --- |
| 2026-11-18 | Six-round interview; this plan written. `main` is at `9b1dbf0` (editing a book's questions). Nothing of the knowledge system is built |
| 2026-11-18 | Phase 1 interview done (three rounds). Building starts at step 1 of section 6b |
| 2026-11-18 | Phase 1 built in the order of section 6b and pushed to the development branch: `vault.py`, `bookfile.py`, the server routes, the editor, `notes.js`, the Notes area, capture, and the reader's Idea note |
| 2026-11-18 | Phase 1 pushed to `main` at `6cb78e6` on the owner's word. Phase 2 interview done (three rounds); building starts |
| 2026-11-18 | Phase 2 built: `process.py`, citing and related notes in `vault.py`, `/api/process`, the Process view (`showProcess` in `index.html`), related notes in `notes.js`. The Process status is per book in `process.json` (set aside only); for a quick note it is the property `aside` |
| 2026-11-19 | Phase 2 pushed to `main` at `dcf0eda` on the owner's word. Phase 3 interview done (three rounds); building starts |
