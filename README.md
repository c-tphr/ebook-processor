# ebook-processor

Give it an EPUB. It divides the book into sections, writes recall questions for the end of each section, has every question checked against the text by a second, independent Claude session, and gives you:

- the book with **checkpoints**: at the end of each section you stop and recall what you just read, before going on;
- the questions on their own, as **structured data**, an **Anki deck** and a **text file** for other spaced-repetition apps.

It runs on your own computer, from the command line or as a local web app, and uses your Claude plan through Claude Code. No API key is needed.

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
| `--model`, `--checker-model`, `--effort` | Passed to Claude Code | its own defaults |
| `--jobs N` | Calls to Claude at the same time | 3 |
| `--epubcheck` | Also run EPUBCheck on both books (needs Java; downloads EPUBCheck once) | off |

`ebook-processor process --help` lists them all.

**If a run stops** (your plan's usage limit, a closed laptop, Ctrl-C), give the same command again. It continues where it stopped; nothing already done is repeated.

**To take questions out** after reading the report, without using Claude again:

```bash
ebook-processor rebuild path/to/Book-processed --remove c0004-p2 c0011-p1
```

`--restore` puts them back. Every output file is written again.

## Use it as a web app

```bash
ebook-processor serve
```

It opens `http://localhost:8765/` in your browser. Drop an EPUB on the page (or type the path of a file on the computer), choose the options and press **Process book**. The page shows the run's progress. When it is finished you can download each file and go through the questions: untick the ones you do not want and press **Rebuild files**. The app listens on your own computer only, runs one book at a time and keeps its runs in `~/ebook-processor-runs` (`--data` chooses another folder).

## How it works

1. **Read.** The EPUB is read into numbered paragraphs (`d03-p011` is block 11 of the third document). Nothing is sent anywhere but to Claude through your own Claude Code.
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
| `ebook_processor/llm.py` | Claude Code without a terminal (`claude -p`), with no tools, no settings and no session history. A backend that calls the Anthropic API directly would go here |
| `ebook_processor/visible.py` | The book with visible checkpoints |
| `ebook_processor/exports.py` | `questions.json`, the Anki deck, the text file |
| `ebook_processor/outputs.py` | Writes every output from the saved state, and the report |
| `ebook_processor/web/` | The local web app: a standard-library HTTP server and one page |
| `tests/data/` | A small made-up book with its expected results, broken prepared books the validator must reject, and Thomas Paine's *Essays* (Standard Ebooks, CC0) |

The firmware format (`rsvp/checkpoints.json`, version 1, and the `rsvp-<section>-start` and `-end` markers) is the one eink-learner-aid defines; its names are kept so that books from this tool load on that firmware. The two repositories share no code at run time.

## Licence

MIT. The EPUB engine was ported from eink-learner-aid, a fork of CrossPoint Reader (MIT).
