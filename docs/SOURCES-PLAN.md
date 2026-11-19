# Other sources than EPUB: plan

This is the working plan for taking in articles and documents as well as EPUB books. It was agreed with the project owner in a three-round interview on 2026-11-19 and is written for whoever builds it, so that the work can start from this file alone. It holds what was decided, what was tried before writing it, the design, the order of work in two steps, and what is left for later.

**Status: plan only. Nothing is built. Building starts when the owner says go.**

## 1. What it is for

Today the app takes one kind of file, an EPUB. Much of what is worth reading closely is not a book: an essay on the web, a paper someone sent as a Word file, your own long notes in Markdown. This work lets all of these go through the same path as a book: sections with checkpoints, the reader, highlights and notes, the notes folder, Practice, and the files for other reading apps.

In scope: a web address, a saved web page, an HTML file, a Markdown file, a plain text file, a Word file (`.docx`). Out of scope for now: PDF (the owner set it aside as more complex).

## 2. Decisions

Every row is the owner's answer. Do not reopen these without asking.

| Subject | Decision |
| --- | --- |
| How a web article comes in | Two ways: paste its web address (the app fetches the page and finds the article), and add a page saved from the browser. Pasting the text itself was not chosen |
| Pages behind a login or paywall | The app cannot fetch these. The saved page is the way in for now. Later, a browser extension (ours, or a popular one that already saves pages) may send the page to the app. Build the way a page comes in so that this needs no rework |
| The library | One library. Each entry is labelled by what it is, and the list can be narrowed by kind |
| Short pieces | Processed like a book: sections and checkpoints, scaled to the length. A short essay may get one checkpoint. The estimate says how many questions before the run |
| Converters | Libraries installed with the tool. Pandoc stays in reserve for Word files, to be added only if the library converts them poorly |
| Pictures | Text and pictures are kept. Adverts and decoration are left out |
| A page that changes later | The copy in the app stays as it was. The entry shows the address and the day it was fetched, with "Open the original". A newer version is a second entry |
| Notes folder | Articles and documents get their own folder, `Articles`, beside `Books`. Their files carry the address, author and date as properties. Citing works as for a book: `[[Title#^h3f9a]]` |
| Plain text | Both short pieces and whole books. For a book, the app finds the chapter headings and shows what it found before the run |
| How many at once | One at a time |
| Delivery | Two steps, each usable by itself and each going to `main` on the owner's word. Step 1: the common ground, Markdown, plain text and HTML files. Step 2: web addresses, saved pages and Word files |

## 3. Choices made by the builder

These are technical, or small enough not to ask about. The owner can overrule any of them.

| Subject | Choice | Why |
| --- | --- | --- |
| The approach | Every source is turned into clean HTML and then into an EPUB **at the door**. From there on the app sees an ordinary book | The whole pipeline, the reader, the notes and every export read an EPUB (section 4). One converter in front changes about ten places; teaching every stage a second format would change hundreds |
| Two kinds | `book` and `article`. A report in Word or an essay in Markdown counts as an article. The form guesses the kind and the owner can change it | The owner chose two folders, `Books` and `Articles`. A third kind ("document") would need a third folder and adds nothing to how the piece is handled |
| The guess | From the web, or a saved page: article. A file: book when it has three or more chapters and 20,000 words or more, otherwise article. An EPUB: book | Simple, and shown before the run so it can be corrected |
| Title and author | Shown in the form before the run and can be changed there | A text file has neither. A web page often has a poor title |
| Chapters | A file is cut into chapters at its top-level headings only when it is long (5,000 words or more) and has two or more such headings. Otherwise it is one document, and the sections inside it are found by the model as now | Chapters give the reader's contents list, the "try one chapter" run and a per-chapter steer. A short piece needs none of them |
| Too short | Fewer than 150 words is refused with a plain message | Below that the existing estimate already counts a document as a title page and plans no question |
| Section length | The owner's usual setting applies, and can be changed per run as now. No separate setting for articles | At the default (600 to 2,000 words) a 1,500-word essay gets one checkpoint at its end, which is what was asked for |
| Fetching a page | Python's own `urllib`, asking for the page as a browser does, one request per page, 20 seconds, 10 MB at most | No new dependency. A site that refuses gets a clear message that points to "save the page and add the file" |
| The same piece twice | The form says "You added this on 3 March" and still lets you add it | The owner wants a newer version as a second entry |
| The original | Kept beside the made EPUB in the run's folder | So that a better converter can be run on it later without fetching again |
| Dependencies | Step 1: `markdown`, `lxml`, `charset-normalizer`. Step 2: `trafilatura`, `mammoth` | See section 4 for sizes |

## 4. What was tried before writing this

Small trials in a scratch folder, not in the repository.

- **The approach works.** Markdown and a web page were converted, cleaned, packed into an EPUB by about forty lines of code, and read by the app's own loader (`prep.epub.load_book_bytes`). The loader found the right blocks: headings, paragraphs, list items, quotes, tables, code, pictures. `steer.estimate` gave one section and one to three questions for a 209-word piece.
- **`markdown` 3.11** (pure Python, 116 KB) with the `extra`, `sane_lists` and `smarty` extensions gives well-formed XHTML with tables, code, footnotes and pictures. It does not read the properties block at the top of a file; `vault.split` and `vault.parse_props` already do.
- **`trafilatura` 2.3** found the article in a page with a menu, an advert, a "related" box, comments and a footer, and left all five out. It also gave title, author, date and site name. It lost the caption under a picture and the raised style of a footnote mark; both are acceptable. Its HTML output is the one to use (`output_format="html"`, with pictures, tables, links and formatting turned on).
- **`mammoth` 1.13** (pure Python, 55 KB) reads headings, lists, tables, links and footnotes from a Word file. It needs a style map for the rest: with one, Word's `Title`, `Quote`, `Intense Quote` and `Block Text` styles came out as a title and quotations. Good enough to start without Pandoc.
- **Size.** The wheels of the three libraries are under 1 MB together, as the owner was told. With what they depend on (`lxml`, `dateparser`, `regex`, `tzdata` and others, almost all for `trafilatura`) they take about 35 MB on disk. That is the same as Pandoc's download. It is still the better choice: `trafilatura` is needed for web pages whatever reads Word files, and Pandoc cannot find the article in a page.
- **Updating brings the libraries.** `ebook-processor update` and `beta` run `pip install -e`, so a user who updates gets the new dependencies without doing anything.
- **Not tried: real web sites.** This session's network rules allow package indexes and GitHub only, so no public page could be fetched. Step 2 is tested against a local server here, and needs either a few sites allowed in this environment or a try on the owner's computer through `ebook-processor beta` (section 9).

## 5. Where the code assumes an EPUB today

- `web/server.py`: `_safe_name` adds `.epub` to any name; `look()` and `Jobs.create()` call `load_book_bytes` on the uploaded bytes; the job's `file` is the EPUB's name and every output name is built from it with `outputs.stem_of`.
- `web/static/index.html`: the two file inputs accept `.epub` only; the drop zone says "Drop an EPUB here"; uploads are sent as `application/epub+zip`.
- `cli.py`: `process` and `estimate` call `load_book` on the path; `runner.default_out_dir` strips `.epub`.
- `pipeline.py`: `Run` loads the EPUB and copies it to the work folder as `source.epub`. **This stays as it is.**
- Notes: `vault.py` puts a book's file in `Books/` and tests `kind == "book"` in 18 places; `server.py` 3, `assist.py` 2, `notes.js` 7, `index.html` 3.
- Wording: "book" in the add form, the library, the run page and the Process view.

Everything else (drafting, checking, the reader, highlights, Practice, the Picco format, the visible-checkpoints EPUB, Anki) reads the EPUB or the state made from it and does not change.

## 6. Design

### 6.1 One door: the `sources` package

A new package `ebook_processor/sources/`. Nothing outside it knows about formats.

```
sources/__init__.py     convert(), kinds, limits, SourceError
sources/clean.py        any HTML -> the small set of XHTML the app reads
sources/pack.py         clean XHTML + pictures + facts -> EPUB bytes
sources/split.py        one long document -> chapters, at its headings
sources/from_markdown.py
sources/from_text.py
sources/from_html.py    an HTML file taken whole (step 1); the article in a page (step 2)
sources/fetch.py        one web request with limits: pictures (step 1), pages (step 2)
sources/from_web.py     step 2: an address -> the page -> from_html
sources/from_docx.py    step 2
sources/saved.py        step 2: .mhtml, and pages saved as one file
```

The one function the rest of the app calls:

```python
def convert(name: str, data: bytes, *, url: str | None = None, folder: Path | None = None,
            split: str = "auto", whole: bool | None = None,
            title: str | None = None, author: str | None = None) -> Made
```

- `name` decides the format by its ending: `.md .markdown`, `.txt`, `.html .htm .xhtml`, and in step 2 `.docx`, `.mhtml .mht`. An unknown ending is refused with the list of what is accepted. `.epub` never comes here.
- `url` is the address the bytes came from, when there is one. **This is the entry for a browser extension later:** it will send a page's HTML and its address, which is exactly a saved page with `url` set.
- `folder` is where the file lies on this computer, when the app was given a path instead of an upload. Pictures beside the file are read from it.
- `split`: `auto` or `none`. `whole`: for HTML, take the whole file or only the article (step 2).
- `Made` holds: `epub` (bytes), `title`, `author`, `type` (the guess, `book` or `article`), `origin` (a dict: `format`, `url`, `site`, `published`, `fetched`, `original` file name, `sha256` of the original), `chapters` found (titles and word counts, and how they were found), `pictures` (kept and missing counts) and `warnings` (plain sentences for the form).

Rules for every converter:

- **The same input gives the same EPUB, byte for byte.** Fixed dates inside the zip, an identifier made from the hash of the content, no clock. The CLI depends on this to carry on a stopped run.
- Each converter returns HTML and facts. Only `clean.py` decides what markup survives, and only `pack.py` writes an EPUB.
- Errors are a `SourceError` with a sentence the owner can act on.

### 6.2 Clean HTML (`clean.py`)

Parsed with `lxml.html`, which mends broken markup. Then one walk over the tree:

- Removed with their content: `script style noscript template nav header footer aside form button iframe object embed svg canvas video audio`, comments, and elements hidden with the `hidden` attribute. (`aside` is kept when it is a footnote.)
- Kept: the tags the reader already shows (`reading.py` holds the list): headings, `p`, `blockquote`, lists, `pre` and `code`, `em strong i b sup sub`, `a`, `img`, `figure` and `figcaption`, tables, `br`, `hr`, definition lists.
- Everything else is unwrapped: the tag goes, its text stays.
- Attributes: only `href` on links, `src` and `alt` on pictures, `colspan` and `rowspan` on cells, `start` on numbered lists, and `id` where a link in the document points to it.
- Links: `http`, `https` and `mailto` are kept. A link into the document itself is kept. `javascript:` and others lose the link and keep the words.
- Footnotes from `markdown` and `mammoth` are rewritten into the form the reader already knows (`epub:type="noteref"` and an `aside` with `epub:type="footnote"`), so the reader shows them as it does a book's. Check `is_noteref` and `is_footnote_container` in `prep/epub.py` for the exact marks.
- Characters XML does not allow are dropped. The output is XHTML that the app's strict parser (`prep/xmltree.py`) accepts; a test feeds it awkward input (unclosed tags, `&nbsp;`, control characters, a stray `<`).
- When the text does not start with a heading, the title is put in front as an `h1`. Nothing else is added to the text: author, site and date live in the EPUB's facts and the job, not in a paragraph the model might ask about.

### 6.3 Chapters (`split.py`)

Input: the clean body. The level to cut at is the highest heading level that appears twice or more. The body is cut there when it holds 5,000 words or more; each piece becomes one document of the EPUB, titled by its heading, and what stands before the first heading becomes a document of its own when it has text. `split="none"` gives one document. The contents list (`nav`) names every document.

### 6.4 The EPUB (`pack.py`)

An EPUB 3 with: `mimetype`, `META-INF/container.xml`, `OEBPS/content.opf`, `OEBPS/nav.xhtml`, `OEBPS/d001.xhtml` and on, `OEBPS/images/…`. Facts in the package file: title, author (`dc:creator`), language (from the source when it says, else `en`), date published (`dc:date`), address (`dc:source`), site (`dc:publisher`). No document is marked as front or back matter. `tests/prep/bookmaker.py` shows the smallest working form. The made EPUB must pass the project's own validator and, in the tests that already use it, EPUBCheck.

### 6.5 Each format

**Markdown.** The properties block at the top, when there is one, gives `title`, `author` or `authors`, `date`, `url` or `source`, `lang`; it is read with `vault.split` and `vault.parse_props` and is not part of the text. The body goes through `markdown` (`extra`, `sane_lists`, `smarty`). HTML written inside the Markdown passes through and is cleaned like any other. Wiki links (`[[Note]]`) become their words. The title is the property, else the first `h1`, else the file name.

**Plain text.**
- Reading the bytes: a byte-order mark decides; else UTF-8 when the bytes are valid UTF-8; else `charset-normalizer` guesses.
- A Project Gutenberg file loses its licence text: only what lies between the `*** START OF …` and `*** END OF …` lines is taken.
- Paragraphs: blocks separated by an empty line, with the lines inside a block joined. When the file has no empty lines, each line is a paragraph. Lines indented by four spaces or more in a block of their own are kept as they are (`pre`).
- Headings, tried in this order and shown to the owner: lines that say `Chapter 4`, `CHAPTER IV`, `Part Two`, `Book III` (with or without a title after them); a short line (60 characters at most) in capitals standing alone between empty lines; a line of Roman or plain numerals standing alone; Markdown marks (`# Title`, or a line underlined with `===`). A rule counts only when it finds two headings or more.
- The form says what was found: "24 chapters, found by lines like 'CHAPTER IV'", with the list, and a choice "Treat it as one piece". When nothing was found: "No chapter headings were found. It will be read as one piece."
- The title is the first non-empty line when it is short (100 characters at most) and followed by an empty line, else the file name. For a Gutenberg file, the `Title:` and `Author:` lines of its header.

**HTML file, step 1.** The whole body, cleaned (6.2). Title from `og:title`, `<title>`, the first `h1`; author from `meta name="author"`; address from `link rel="canonical"` or `og:url`.

**Web address, step 2.** `fetch.py` gets the page; `from_html` runs `trafilatura` on it with the address, and takes the article, title, author, date and site. Then:
- Refused, each with its own sentence: an address that is not `http` or `https`; no answer in 20 seconds; a status of 401, 403 or 429 ("the site refused; it may want a login. Save the page in your browser and add the file"); 404; a reply that is not HTML (a PDF gets "PDF files are not supported yet"); more than 10 MB.
- Found too little: under 150 words, or the page says it needs JavaScript, or the article ends in a paywall notice (the words found are far fewer than the page's `wordCount` or a `isAccessibleForFree: false` mark is present). The form says so and points to the saved page.
- The address kept is the canonical one the page names, without its fragment and tracking parameters (`utm_*`, `fbclid`, `gclid`).

**Saved page, step 2.** An `.html` file that looks like a web page (it has scripts, a menu, a canonical address or an `og:` mark) is treated as a page: the article is found as above. The form shows "Only the article" or "The whole file", set to the guess, and the owner can switch. Pages saved as one file are read too: `.mhtml` from Chrome and Edge (read with Python's `email` module: the first HTML part is the page, the other parts are its pictures) and HTML with pictures written into it as `data:` addresses (the SingleFile extension). Safari's `.webarchive` is left for later.

**Word file, step 2.** `mammoth` with a style map for Word's built-in `Title`, `Subtitle`, `Quote`, `Intense Quote`, `Block Text` and code styles. Pictures inside the file come out as `data:` addresses and are taken like any other. Title and author from the file's own properties (`docProps/core.xml`, read with `zipfile`), else the `Title` paragraph, else the file name. `mammoth`'s warnings about styles it did not know are counted, and the form says "Some formatting was not recognised and was kept as plain paragraphs" when there are any. An old `.doc` file is refused with "save it as .docx".

### 6.6 Pictures

One routine in `pack.py` for every format, given the clean body and a way to get bytes:

- `data:` addresses are decoded.
- `http` and `https` addresses are fetched (`fetch.py`), relative ones first joined to the page's address. For a lazy-loading page, `data-src` and the largest entry of `srcset` are tried when `src` is a placeholder.
- A relative path is read from `folder` when there is one and the path stays inside it. For an uploaded file there is no folder: the picture is missing.
- Kept only when the bytes are PNG, JPEG, GIF or WebP by their first bytes. SVG from outside is not kept.
- Limits: 60 pictures, 5 MB each, 40 MB together, 10 seconds each. Pictures smaller than 2 KB (tracking dots, spacers) are dropped.
- A missing picture leaves its description (`alt`) in the text as it does now for a book, and is counted. The form says: "3 pictures could not be found. To take pictures that lie beside the file, type the file's path instead of dropping it."
- A picture's address on the local network or this computer is not fetched (the name is resolved first and private ranges are refused). This rule is for pictures inside a document, which the owner did not choose one by one. The page address the owner types is not limited this way.

### 6.7 The server

- **Looking before the run.** `POST /api/look` and `/api/look/local` accept any supported file. For a file that is not an EPUB they convert it and answer with what they answer now (`title`, `words`, `chapters`, `suggested`) plus `type`, `author`, `origin`, `found` (how chapters were found), `pictures`, `warnings`, `seen_before` (date of an entry with the same address or the same original) and `held`, a token. Options (`split`, `whole`) are query fields; changing one looks again.
- **Held results.** `Jobs.held` keeps the last few conversions in memory (four entries, thirty minutes), each with its original bytes, so that a page is fetched once and not again when the owner presses Process or flips an option. `POST /api/look/url` with `{url}` fetches and holds (step 2). `POST /api/look/held` with `{held, split, whole}` converts the held original again.
- **Starting.** `POST /api/jobs/held` with `{held, settings, plan, type, title, author}` makes the job from a held result. When the token is no longer known (the app was restarted) it answers 410 and the form looks again. EPUB uploads keep today's route.
- **`Jobs.create`** gains the made result. It writes `input/<Title>.epub` (the made EPUB; `file` in the job, so every later name is built as now) and `input/original/<name as given>` (for an address: `page.html`).
- **The job** gains `type` (`book` or `article`; absent means book, so nothing stored today changes) and `origin`. The name `kind` is taken (`imported`). `summary()` passes both on.
- **Changing the kind afterwards:** `POST /api/jobs/<id>/type`. It moves the file in the notes folder from one folder to the other; links are by title, so they hold.
- `_safe_name` keeps the ending it is given when it is a supported one.

### 6.8 The pages

- **Add form** (`showAdd`): the drop zone reads "Drop a file here" with the accepted kinds under it; the file inputs accept the new endings; in step 2 a field "or paste a web address" with a Fetch button stands beside the path field. After the look, a panel shows: title and author (two fields that can be changed), kind (Book or Article), length, what was found ("One piece of 1,480 words" or the chapter list with the way it was found and "Treat it as one piece"), pictures kept and missing, warnings, and "You added this on …" when it applies. For a page: the site, and "Only the article / The whole file". The estimate line stands as now. "Try one chapter first" is offered only when there are chapters. The button reads "Process".
- **Library**: every row carries a small label, "Book" or "Article", and for an article where it came from (`example.org`, or "Markdown file"). Above the list, "All · Books · Articles", shown only when both kinds are there. The choice is remembered in the browser.
- **Run page and reader**: for an entry with an address, a line "From example.org · fetched 19 Nov 2026 · Open the original". Where a sentence names "the book" for an article in plain view (run page, delete question, add form, Process view headings, Notes filters), it says "article": one helper gives the noun for a job. Sentences deep in settings and help keep "book".
- **Import a prepared book** stays EPUB only.

### 6.9 The notes folder

- A new note kind `article`, in the folder `Articles/`. In `vault.py`: `ARTICLES = "Articles"`, `SOURCES = ("book", "article")`, a folder for each, and a note property `source` (true for both). The 33 tests of `kind == "book"` become tests of `source`, except where the word shown differs. A file in `Articles/` without a `kind` property is an article, as a file in `Books/` is a book.
- `bookfile.text` writes `kind: article` and, when known, `url`, `site`, `published`, `fetched`, beside `authors`. The body is as for a book; a piece with one document has no chapter heading.
- `Vault.put_book` takes the folder from the kind, and moves the file when the kind changes.
- Notes pages: the kind filters gain "Articles"; the Process view groups "Books and articles"; the graph draws an article as it draws a book; sources of a note, back-links and citing work unchanged because they go by id and block.
- `assist.py`: an article's passages count as source material exactly as a book's.

### 6.10 The command line

`ebook-processor process FILE` and `estimate FILE` accept the new endings, and in step 2 an address in place of a file. The made EPUB is written into the work folder and the run goes on as now. The default output folder is `<name>-processed` beside the file, or in the current folder for an address. For an address, the work folder remembers it (`origin.json`), and a second run with the same address carries on from the page already fetched. New options: `--one-piece` (no chapters), `--as book|article` (kept in the report for the app's import), `--whole-page`.

### 6.11 The model

The instructions are not changed for books, so nothing already made is redone. Whether they say "book" in a way that misleads on an essay is checked with real runs (section 7). If it does, one sentence is added for articles only.

## 7. Tests

- **Units, one file per module** under `tests/sources/`: for each format a small source written in the test, converted, loaded with `load_book_bytes`, and the blocks, title, author, chapters and pictures compared. Word files and `.mhtml` files are built in the test by a helper, as EPUBs are (`tests/prep/bookmaker.py`); no binary file is committed.
- **Cleaning**: broken markup, scripts, event attributes, `javascript:` links, hidden elements, forbidden characters, deep nesting, a 5 MB file (time limit).
- **Same in, same out**: every converter twice, bytes equal.
- **Text**: each heading rule, a file without empty lines, Windows line ends, Latin-1 and UTF-16 files, a Gutenberg header.
- **Fetching** against a server started in the test: a good page, a redirect, 403, 404, a timeout, a PDF, a page over the size limit, a JavaScript-only shell, a paywalled stub, pictures of each kind and over each limit, a picture on a private address.
- **Server**: look, held results and their expiry, create from held, the job's `type` and `origin`, the same piece twice, changing the kind, the article's file in `Articles/` with its properties.
- **Existing tests stay green unchanged**; that is the proof that books are untouched.
- **Browser** (Chromium, Firefox, WebKit in CI): add a Markdown essay through the form, see the panel, change the title, process it with the scripted model, read it, highlight, find the file under `Articles/`, cite it from an idea note, narrow the library to articles. In step 2: the same from an address served by the test's own server, and from a Word file.
- **Real model**, a few calls per step as before: a piece of about 300 words, one of 1,500 and one of 8,000 with headings. Check that the sections and questions fit the length and that the wording does not call an essay a book.
- **The full made EPUB** of each format through the project's validator, and through EPUBCheck where the tests already run it.

## 8. Order of work

### Step 1: common ground, Markdown, plain text, HTML files

1. `sources/clean.py` and `sources/pack.py` with their tests; add `lxml`, `markdown`, `charset-normalizer` to `pyproject.toml`.
2. `sources/split.py`.
3. `from_markdown.py`, `from_text.py`, `from_html.py` (whole file), `convert()`.
4. `fetch.py` and pictures: `data:`, beside the file, from the web; limits.
5. Server: look for any file, held results, create from held, `type` and `origin` on the job, originals kept.
6. Add form: accepted files, the panel, kind, title and author, "one piece".
7. Library label and filter; the origin line on the run page and in the reader; the noun helper.
8. Notes folder: `article` kind, `Articles/`, `source` in place of the `book` tests, properties, filters and wording; changing the kind.
9. Command line.
10. Browser tests, real-model calls, README ("What you can add", the module table, the developer section), `DEFERRED.md`.
11. Push to the development branch, wait for the nine CI jobs, report. To `main` on the owner's word.

### Step 2: web addresses, saved pages, Word files

1. Add `trafilatura` and `mammoth`. Check on CI that they install on the three systems and on Python 3.10 and 3.13.
2. `from_html.py`: finding the article; the guess "page or plain file"; "Only the article / The whole file".
3. `from_web.py` and page fetching in `fetch.py`: every refusal and "found too little" case with its sentence.
4. `saved.py`: `.mhtml`, pictures as `data:`.
5. `from_docx.py` with the style map and the file's properties.
6. Server: `look/url`; the same-address check. Add form: the address field. Command line: an address.
7. Tests as in section 7; real pages (section 9).
8. Decide on Pandoc: only if real Word files come out badly. Record the decision in `DEFERRED.md`.
9. README, `DEFERRED.md`, push, CI, report. To `main` on the owner's word.

## 9. To verify, and risks

| What | How |
| --- | --- |
| Real web sites | Not reachable from this session. Either a short list of sites is allowed in the environment for step 2, or the owner tries five or six of their own on their computer with `ebook-processor beta` before it goes to `main` |
| Sites that refuse a program | Expected for some. The message must be clear and the saved page must work for them; that pair is the test |
| A page saved by a real browser | `.mhtml` can be made by the test browser (Chromium) and is tested that way. A page saved from Firefox or Safari by hand is for the owner to try |
| Real Word files | The trial used one made by Pandoc. Files written in Word, Google Docs and Pages differ in their styles; the owner's own files decide whether Pandoc is needed |
| Short pieces through the model | The real calls of section 7. The risk is a single section with too many or no questions |
| `lxml` on every system | It has ready-made builds for Windows, macOS and Linux on Python 3.10 to 3.14; CI confirms on the nine jobs |
| Very long single documents | The pipeline already drafts a long document in parts (`plan_units`); a 100,000-word text file without headings is one test |

## 10. Left for later

| What | Note |
| --- | --- |
| PDF | Set aside by the owner |
| A browser extension, or taking pages from an existing one | The entry is ready (`convert(..., url=...)` with the page's HTML). What is missing: a route that another program may call, which needs its own key shown in Settings because the app refuses requests from other origins today |
| Pasting text | Not chosen |
| Several at once, a folder, a list of addresses | Not chosen for now |
| Fetching a page again to see what changed | The copy stays as it was; a new version is a new entry |
| Articles spread over several pages | Only the page at the address is taken |
| Safari `.webarchive`, `.rtf`, `.odt`, old `.doc` | `.webarchive` is small to add (Python reads the format). The others would come with Pandoc |
| Pandoc | In reserve for Word files |
| Newsletters, feeds, transcripts | Not asked for |
