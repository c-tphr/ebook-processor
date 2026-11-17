# ebook-processor

Give it an EPUB. It divides the book into sections, writes recall questions for the end of each section, has every question checked against the text by a second, independent Claude session, and gives you:

- the book with **checkpoints**: at the end of each section you stop and recall what you just read, before going on;
- the questions on their own, as **structured data**, an **Anki deck** and a **text file** for other spaced-repetition apps.

It runs on your own computer, from the command line or as a local web app, and uses your Claude plan through Claude Code, your ChatGPT plan through Codex, or both in one run. No API key is needed.

## What you get

For `Book.epub`, in a folder `Book-processed` beside it:

| File | What it is |
| --- | --- |
| `Book.checkpoints.epub` | For any reading app. After each section comes a page with one question, alone; turn the page for its answer. |
| `Book.prepared.epub` | For the Picco firmware of [eink-learner-aid](https://github.com/c-tphr/eink-learner-aid), and for readers built for this format. The text is untouched: the file only gains invisible markers and a data file, `rsvp/checkpoints.json`. An ordinary reading app shows it as the plain book. |
| `Book.questions.json` | Every question with its answer, chapter, section, the passage that supports it and the checker's verdict. Schema: `ebook_processor/schema/questions.schema.json`. |
| `Book.anki.apkg` | An Anki deck, `Books::<Title>`. Front: the question, with the book and chapter above it. Back: the answer, the section, and the supporting passage from the book. |
| `Book.cards.tsv` | The same cards as tab-separated text with a header row (`id`, `question`, `answer`, `book`, `chapter`, `section`, `passage`, `tags`), for Mochi, RemNote, Quizlet and others. |
| `Book.report.md`, `.json` | What was made, which questions were left out and why, what the checker remarked on, and how much Claude usage the run took. |
| `work/` | The run's saved state. Keep it if you want to remove questions later or continue an unfinished run. |

Your original EPUB is only read. It is never changed.

The deck holds text of the book (the supporting passages). That is fine for your own use; do not share a deck made from a book in copyright.

## Set up

You need Python 3.10 or later and [Claude Code](https://code.claude.com/docs/en/setup), signed in. It works on macOS, Linux and Windows through WSL.

```bash
git clone https://github.com/c-tphr/ebook-processor.git
```

```bash
cd ebook-processor && python3 -m pip install -e .
```

Check that Claude Code is ready (run `claude` once and sign in with `/login` if it is not):

```bash
claude --version
```

### Codex (optional)

The questions can also be written or checked by OpenAI's models, through [Codex](https://developers.openai.com/codex/cli) and your ChatGPT plan. Install it and sign in with your ChatGPT account:

```bash
npm install -g @openai/codex
```

```bash
codex login
```

Claude Code and Codex are independent: with only Codex installed, name a Codex model for the run (`--model codex`, or the Model field in the app).

## Updating

```bash
ebook-processor update
```

This stops the app if it is running, fetches the latest `main` into the git checkout the tool was installed from, installs it, and starts the app again on the same port and data folder, in the terminal where you gave the command. With `--no-serve` it updates and stops there.

- The checkout must be on `main` with no uncommitted changes to tracked files; otherwise nothing is touched and the message says what to do.
- The app is not stopped while it is processing a book. Wait for the book, or give `--force`; the run continues afterwards with its Continue button.
- The running app is found through a small record it keeps in `~/.ebook-processor/servers/` while it runs. An app started by a version from before this command existed has no record: stop it with Ctrl-C first.

The first time, the command itself has to arrive by hand: stop the app, then

```bash
git -C path/to/ebook-processor pull
```

```bash
python3 -m pip install -e path/to/ebook-processor
```

## Use it from the command line

```bash
ebook-processor path/to/Book.epub
```

That processes the book with the default options and prints where the files are. Progress is shown while it runs. Useful options:

| Option | What it does | Default |
| --- | --- | --- |
| `-o FOLDER` | Where the results go | `Book-processed` beside the EPUB |
| `--density short\|medium\|long` | Target section length: 300 to 1,000, 600 to 2,000 or 1,500 to 4,000 words. A checkpoint follows each section | `medium` |
| `--section-words MIN-MAX` | The same, in your own numbers | |
| `--max-questions N` | Most questions per checkpoint, 1 to 4 | 3 |
| `--kind auto\|expository\|narrative\|technical` | The kind of book. `auto` decides section by section | `auto` |
| `--content prose\|text\|figures` | What Claude sees of tables, code, formulas and pictures (below) | `text` |
| `--rounds N` | How often a question may be checked. 0 switches checking off | 3 |
| `--model`, `--checker-model`, `--effort` | The model that drafts, the model that checks, and the reasoning effort. `ebook-processor models` lists the models your logins can use. See "Choosing the models" below | Claude Code's defaults |
| `--jobs N` | Calls to Claude at the same time | 3 |
| `--epubcheck` | Also run EPUBCheck on both books (needs Java; downloads EPUBCheck once) | off |

`ebook-processor process --help` lists them all.

**If a run stops** (your plan's usage limit, a closed laptop, Ctrl-C), give the same command again. It continues where it stopped; nothing already done is repeated.

**To take questions out** after reading the report, without using Claude again:

```bash
ebook-processor rebuild path/to/Book-processed --remove c0004-p2 c0011-p1
```

`--restore` puts them back. Every output file is written again.

## Use it as a web app, and read in it

```bash
ebook-processor serve
```

It opens `http://localhost:8765/` in your browser. Drop an EPUB on the page (or type the path of a file on the computer), choose the options and press **Process book**. Under **Advanced**, the two model fields list the models your Claude login can use, as Claude Code reports them when the app starts, and Codex's models when Codex is installed and signed in. The page shows the run's progress. When it is finished you can download each file and go through the questions: untick the ones you do not want and press **Rebuild files**. The app listens on your own computer only, runs one book at a time and keeps its runs in `~/ebook-processor-runs` (`--data` chooses another folder).

The app opens on your **library**: every book with how far you have read and how many questions you recalled on the first try, and the book you were last reading on top. **Add book** processes a new EPUB, or takes a `Book.prepared.epub` made elsewhere as it is.

### Settings

Every page has a **Settings** button at the top right. It opens one window with a tab per part of the app, on the tab of the page you are on:

| Tab | What it holds |
| --- | --- |
| General | Colours (as the system, light, dark) for every page; the version and where your books are kept |
| Add a book | What the Add a book form starts with. The form keeps its options, so one book can still differ. Books already processed are not affected |
| Reader | Typeface, size, line spacing, column width, and the view a new book opens in |
| Notebook | Whether **Open** shows a passage beside the notebook or in the full reader, which side the reader is on, and whether it scrolls or turns pages |
| Practice | The numbers of the schedule (see Practice below) |

A change applies at once, also in other open tabs of the app. Settings are kept in `reader-settings.json` and `srs-settings.json` in the data folder.

### The reader

**Read** opens a book in the app's own reader. It has three views over one position, so you can change between them at any point:

| View | How it works |
| --- | --- |
| Page | Whole pages, two side by side on a wide window. Turn with the arrow keys, the space bar, or a click near the edge of the page. |
| Scroll | One chapter as a long column. Each checkpoint is a bar across the text; what follows it stays hidden until you have been through it. |
| RSVP | One word at a time (or two or three) at a pace you set, aligned on a focus letter, with longer holds at punctuation. Paused, it shows the paragraph around the word; click a word to move there. It stops at pictures, tables and listings and shows them. |

At the end of each section the reader stops for its **checkpoint**, one question at a time:

1. The question is shown with the answer hidden. Recall it. You may type your recall; it is kept with your grade.
2. **Reveal answer** puts your words beside the reference answer. Mark yourself **Correct** or **Incorrect**.
3. After Incorrect you can **reread the section** in any view and come back to the same question with the answer hidden, or continue.
4. **Skip question** and **Skip checkpoint** grade nothing.

Only your **first** grade for a question is kept: a Correct after a reread does not replace an earlier Incorrect. Jumping through the contents, the search or the map never sets off a checkpoint; reading through a section again does. Closing the window in the middle of a question or a reread brings you back to the same place.

Also in the reader: contents by chapter and section, search, and footnotes that open in place. How the text is set (serif or grotesk, size, line spacing, column width) is in **Settings**, on the Reader tab.

The strip at the bottom is the book: one block per section, with a square for each question (blue recalled, black missed, hollow not yet met). The book's page shows the same map, your time and pace per view, and lets you choose which cards go into the Anki deck and the text file: all, only those you missed on the first try, or only those you have met. Cards are tagged `recall::correct`, `recall::missed` or `recall::unseen`.

### Highlights, notes and the notebook

In the page and scroll views, select text and press **h** to highlight it, or **n** to highlight it and write a note. With nothing selected, **n** writes a note on the paragraph under the pointer; a paragraph with a note has a small blue square at its start, and clicking the square opens the note. Click a highlight to edit its note or remove it. Highlights that touch become one.

In RSVP, **h** highlights the sentence being shown without stopping, and **n** highlights it, pauses and opens a note. Paused, you can select words in the paragraph shown.

Notes are Markdown. Besides notes on a highlight or a paragraph, there is one note per section, per chapter and for the book. When a checkpoint ends, the reader offers a note on the section you just finished; reading on passes it by. The **Notes** button lists the chapter's highlights and notes.

A highlight is a run of words, in the same positions the three views share, so it shows in all of them and in the supporting passage of a Practice card. It stays in place when the book is processed again, because processing never changes the text.

Each book has a **notebook**, opened from its page: the book's chapters and sections as an outline and, under each section, your note on it, your highlights and notes in reading order, and the answers you gave at its checkpoint (the question, what you typed, the reference answer, and whether you recalled it). Notes can be written and edited there too. **Export Markdown** downloads the notebook as one file, with or without the checkpoint answers; sections where you marked and wrote nothing are left out of the file.

**Open** on a note, a highlight, a section or a checkpoint answer shows its place in the book **beside the notebook**: the reader takes one side of the window, opens at the passage and lights its paragraph for a moment. For a checkpoint answer, that is the paragraph the answer comes from. This needs a window at least 1,100 pixels wide; in a narrower one, or with Ctrl or Cmd held, the full reader opens instead.

The reader beside the notebook is for looking things up. It does not move your place in the book, count reading time or stop at checkpoints, and the whole text is open, also what you have not read yet. In it you can:

- switch between **Scroll** and **Pages**, open the contents, and search the book;
- highlight and write notes as in the reader; the notebook shows them at once, and what you change in the notebook shows in the text;
- click a highlight or the square of a paragraph note to bring its entry into view in the notebook;
- press **Continue here** to make the place shown your place in the book, or **Full reader** to open the reader there;
- move it to the other side, drag the line between the two to change its width, or close it.

Highlights are drawn with the browser's CSS Custom Highlight API (Chrome and Edge 105, Safari 17.2, Firefox 140, or later). In an older browser, highlights are kept and listed but not drawn in the text. They live in `notes.json` in the book's folder.

### Practice (spaced repetition)

A question becomes a **card** when you meet it at its checkpoint. From then on it comes back in **Practice**, in the library for every book together or on a book's page for that book alone, on a schedule that follows the default scheduler of [Mochi](https://mochi.cards/docs/reviewing):

- There are two answers, **Remembered** and **Forgot**. Each button says when the card would come back.
- Remembered multiplies the card's interval. An overdue card is credited with the time it actually waited.
- Forgot shortens the interval without resetting it and sends the card to the **re-review** queue, after the cards that are due. Remembered there, it keeps the shortened interval. Forgotten there again, it drops to one day.
- A card **joins the schedule the first time you remember it**, at its checkpoint or in Practice, and is due for its first review the same day. Until then it is a **new** card: missed or skipped at its checkpoint, it stays new.
- Due dates are varied slightly, so that cards learned together do not always come back together.
- Meeting a card early, by rereading its section, changes its schedule only if it was due.

A session shows the most overdue cards first (cards due the same day in random order), then re-reviews, then new cards, then the first reviews of cards learned that day. Questions of sections you have not read never appear. You can type your recall before revealing the answer, open the supporting passage from the book, and jump to reread the section.

The schedule's numbers are in **Settings**, on the Practice tab. Mochi documents the rules above but not its numbers, so the defaults are this tool's:

| Setting | Default | Where the value comes from |
| --- | --- | --- |
| Remembered multiplies by | 1.8 | This tool's choice. Mochi does not publish its default |
| Forgot multiplies by | 0.5 | Reported by two Mochi users as its default; not in Mochi's documentation |
| Starting interval | 1 day | This tool's choice |
| Longest interval | none | Mochi has the option; off unless set |
| New cards per day | no limit | Mochi has the option; off unless set |
| First review the same day | on | Mochi's rule since 2020; off makes it the day after |
| Vary due dates slightly | on | Mochi's rule (on by default). The amount, about 5% of the interval, is this tool's |

Two things Mochi's sources leave open are also this tool's choice: the formula for an overdue card (the longer of its interval and the days waited, times the multiplier), and that an early answer moves nothing. Mochi's alternative scheduler, FSRS, is not included.

The Anki deck is still there for practising in Anki instead; the two do not share a schedule.

Reading state lives in `reading.json` in the book's folder. It follows the saved-state format of the Picco firmware (`position`, `grades`, `active_checkpoint`), with members of its own (prefixed `x_`) for every attempt with what you typed, time and pace. Cards and their review history are in `srs.json` beside it.

## How it works

1. **Read.** The EPUB is read into numbered paragraphs (`d03-p011` is block 11 of the third document). Nothing is sent anywhere but to the models you chose, through your own Claude Code or Codex.
2. **Draft.** The book goes to Claude in parts of about 10,000 words, several parts at a time. For each part Claude returns the sections, what it left out as front or back matter, and the questions with reference answers and the paragraphs that support each answer. A script checks every reply (every paragraph in exactly one section or excluded range, length limits, citations inside the section) and sends it back with a list of the problems when it fails.
3. **Check.** A separate Claude session, which sees only the section's text and the questions, judges each question on five points: the answer says nothing its section does not say; the answer is clear enough to grade yourself against; the question can be answered from its section alone; the question does not give its answer away; the question can be understood weeks later without the page. It also judges each section's break, and another call reviews what was left out of the sections.
4. **Revise.** A question that fails goes back to the drafter with the checker's finding, and then to the checker again, up to three checks. A question that still fails is **left out** of every output and listed in the report with the reason. The run always delivers a book.
5. **Build.** Scripts write the two books, the JSON, the deck and the text file, and check the firmware-format book against the original (13 structure checks: the text is unchanged word for word, every marker sits between two words, the data file fits the firmware's limits, and so on).

The rules Claude follows are in `ebook_processor/instructions/drafter.md` and `checker.md`. Read them to see exactly what a good section, question and answer are taken to be; change them to change the questions.

### Questions that stand alone

Every question is written to make sense on a flashcard weeks later, with only the book's title and chapter beside it. It names its subject ("What two weaknesses of the miasma theory does Snow point out?") instead of pointing at the page ("What two weaknesses does the author point out here?"). The same questions therefore serve both the checkpoints in the book and the deck.

### Kinds of book

- **Exposition** (essays, science, history): claims and their reasons, causes and effects, distinctions, steps of a method.
- **Narrative** (novels, stories, memoir): what happened and why it matters, a character's decision and motive, what changed. Answers never reveal what happens later in the book.
- **Technical** (textbooks, manuals): what a concept means, why a method works, how the parts of a listing, table or formula relate. Never "write out the listing".

The rules were written for English books.

### Tables, code, formulas and pictures (`--content`)

| Value | What Claude sees | When to use it |
| --- | --- | --- |
| `prose` | A one-line placeholder for each (`[Table: 4 rows x 3 columns; caption "..."]`). Questions never rest on them | Books where they are decoration |
| `text` | The cells of a table, the lines of a listing, the text of a formula. Pictures by caption and description only | The default |
| `figures` | As `text`, and Claude also looks at each picture | Books whose diagrams carry the content. Slower and uses more of your plan |

### Choosing the models

A model setting names a tool and a model:

| Setting | Meaning |
| --- | --- |
| (none) or `claude` | Claude Code's own default model |
| `opus`, `sonnet`, a full Claude model name | That model, through Claude Code |
| `codex` | Codex's own default model |
| `codex:gpt-6.1-sol` | That model, through Codex. A name that starts with `gpt-` goes to Codex without the prefix too |

The drafting model also revises. The checking model is the same one unless you name another, so the two can come from different tools:

```bash
ebook-processor Book.epub --model codex:gpt-6.1-sol --checker-model opus
```

A tool is only needed when a setting names it. The reasoning effort is one setting for both models; the app offers the levels both accept.

Codex's models are asked of Codex itself when the app starts: first of its app server, which is what Codex's own apps ask, then of its catalogue (`codex debug models`). When neither answers, the app says so in its terminal and in Settings, and `ebook-processor models` prints what each replied. The Model fields then still offer Codex's default and **Another Codex model**, where a name can be typed; that entry is always there, for a model too new to be listed.

Codex is a coding agent and cannot be run with no tools at all, so a call to it is kept as close to a plain question as its options allow: this tool's instructions replace Codex's own, the sandbox is read-only, the working folder is an empty temporary one, your Codex settings, rules and project `AGENTS.md` files are left out, and the session is not kept. One thing cannot be switched off: a personal `AGENTS.md` in your Codex home folder (`~/.codex/AGENTS.md`) is still shown to the model. Codex reports no price, so the report's list-price figure covers the Claude Code calls only.

## Things to know

- **Usage.** A run reads the book about twice (once to draft, once to check) and writes a few hundred words per section. The report gives the number of calls and tokens, and what they would have cost at API list prices, as a measure of size; a Claude plan is not billed per run.
- **Card identities.** Importing a rebuilt deck of the same run updates the cards and keeps your review history. Processing the same book again from scratch writes new questions, so it makes new cards: delete the old deck first if you do not want both.
- **Very long books in the firmware format.** The firmware's data file holds at most 512 sections and 1,024 questions. A very long book at a short section length can exceed that. The book with visible checkpoints and the deck are still written, and the report says so; a longer section length fixes it.
- **Page breaks.** The visible checkpoints use standard CSS page breaks written into the chapter files. The original's own styles, fonts and images are untouched. The book's title gets the suffix " (checkpoints)" so that the two editions can be told apart in a library (`--title-suffix ''` switches that off).
- **DRM.** A book with DRM cannot be read. Use a DRM-free EPUB.

## For developers

```bash
python3 -m pip install -e '.[test]' && python3 -m pytest -q
```

The tests never call Claude: `tests/scripted.py` is a stand-in that answers from the task message alone.

| Path | Contents |
| --- | --- |
| `ebook_processor/prep/` | The EPUB engine, ported from `tools/prep` of eink-learner-aid: reading and numbering (`epub.py`, `words.py`), byte-exact rewriting (`epubzip.py`, `xmltree.py`), the draft's checks (`draft.py`), the firmware-format build (`build.py`) and its validator (`validate.py`). `python3 -m ebook_processor.prep` runs `extract`, `build` and `validate` on their own |
| `ebook_processor/pipeline.py` | The run: parts, drafting, checking, revising, the saved state |
| `ebook_processor/prompts.py`, `instructions/` | What is sent to Claude, and the reply shapes |
| `ebook_processor/llm.py` | Claude Code without a terminal (`claude -p`, with no tools, no settings and no session history), Codex the same way (`codex exec`), and the router that sends each call to the tool its model setting names. A backend that calls an API directly would go here |
| `ebook_processor/visible.py` | The book with visible checkpoints |
| `ebook_processor/exports.py` | `questions.json`, the Anki deck, the text file |
| `ebook_processor/outputs.py` | Writes every output from the saved state, and the report |
| `ebook_processor/notes.py` | Highlights and notes, the notebook made from them, and its Markdown export |
| `ebook_processor/srs.py` | The spaced-repetition scheduler and a book's cards |
| `ebook_processor/reading.py` | A prepared EPUB as the reader needs it (clean chapter HTML with word positions, sections, search) and the saved reading state |
| `ebook_processor/update.py` | `ebook-processor update`: find and stop the running app, fast-forward the checkout, install, start again |
| `ebook_processor/web/` | The local web app: a standard-library HTTP server, the library page (`index.html`), the reader (`reader.html`, which is also the reader beside the notebook when opened with `?pane=1` in a frame) and the settings window both share (`settings.js`) |
| `tests/data/` | A small made-up book with its expected results, broken prepared books the validator must reject, and Thomas Paine's *Essays* (Standard Ebooks, CC0) |

The firmware format (`rsvp/checkpoints.json`, version 1, and the `rsvp-<section>-start` and `-end` markers) is the one eink-learner-aid defines; its names are kept so that books from this tool load on that firmware. The two repositories share no code at run time.

## Licence

MIT. The EPUB engine was ported from eink-learner-aid, a fork of CrossPoint Reader (MIT).
