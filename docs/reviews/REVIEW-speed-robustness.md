# Review: speed, resource use and robustness

Reviewed: the whole repository at commit `4557303` (branch `claude/dreamy-allen-6un3tt`, the same as `main`), on 2026-11-22. No code was changed. Every number below was measured on one machine: Linux, 4 cores, 16 GB, Python 3.13.18, files already in the system's cache, headless Chromium from Playwright 1.63. Times on Windows, on a slow disk or with a virus scanner will be higher. The scripts are named in section 3.

The test data was made up: Thomas Paine's *Essays* from `tests/data` (52,786 words, 140 sections, 140 questions) processed once with the stand-in model, given 165 highlights, 46 answers and 46 cards, and copied to make libraries of 50 and 500 entries; and notes folders of 1,000, 3,000, 6,000 and 10,000 notes of about 120 words with two or three links each.

A second review (`REVIEW-structure.md`) covers structure and duplication. Where the two meet, this file says so and stays short.

## 1. Summary

The code is careful where it matters most for data: the JSON stores and the notes are written through a temporary file, a note changed on disk is not written over, paths from requests are checked, HTML from books is rebuilt from a fixed list of tags, and requests from other sites are refused. Start-up is quick (0.22 s to import, 0.27 s until the app answers). With one or two books and a few hundred notes every request takes a few milliseconds.

The weak points appear with size and with two things happening at once. The app works from whole parsed books where a small summary would do, so some pages open every book of the library. The one lock is held across model calls and across the rebuilding of a book's files, so the whole app stands still for seconds. Several lists are drawn or computed in full where only a part is shown. Two pages can lose data when the same book is open twice or when a question is finished at the wrong moment. Limits on size exist for uploads and web pages, but not for what a file unpacks to.

The changes I would make first, in this order:

1. Do not hold `Jobs.lock` across a model call or across the rebuilding of a book's files (C1). Today "Save and check" stops every other request for as long as the model takes.
2. Keep a small manifest for each book and stop opening every book for Practice, Notes and Process; limit the cache of open books (R1). Today 50 books cost 21 to 43 seconds and 0.65 to 0.9 GB on the first visit.
3. Make the graph's layout safe: limit the speed of a point, start spread out, stop drawing when nothing moves (P1). Today a note with about 120 links freezes the tab for good.
4. Merge the reader's attempts on the server, and count cards by attempt, not by position in a list (C2). Today a second tab of the same book can remove answers and stop new cards.
5. Replace the "is there a word here" test in the EPUB engine by a cheap one (R2). One hour of work; every opening of a book and every rebuild becomes about twice as fast.
6. Check the `Host` header, limit what a file or a web answer may unpack to, and refuse a negative `Content-Length` (I1, L1).
7. Answer the library listing and the notes folder's "has anything changed" from file stamps (R4, R5).
8. Draw the Process view in parts, and change one row when an item is chosen (P2).

Findings by size: 20 small, 7 medium, 1 large.

## 2. Findings

Each finding says: where, what happens, what I suggest, what it would gain, the size of the work (small: under an hour; medium: a few hours; large: a day or more), the risk and the tests that protect the change, and whether I verified it.

### 2.1 Server requests

#### R1. Practice, the Notes front page and the Process view open every book, and every opened book stays in memory

- **Where:** `web/server.py:1376-1391` (`Jobs.book`, the cache `Jobs.books` at line 156), `:234` (`practice` asks for each book's manifest), `:821-833` and `:808` (`file_books`, `file_book`), `:937` (`process_view`), `:972` (`process_waiting`, called by `detail` for the book page), `reading.py:65-93` (`ReadingBook.manifest`).
- **What happens:** These pages need only a book's outline: chapters, sections, questions. They get it by parsing the whole EPUB. One book of 52,786 words takes 366 to 404 ms to open and holds 12.6 MB. Nothing is ever removed from `Jobs.books`. With 50 books, the first `GET /api/practice` took 21.1 s and the process grew from 59 MB to 688 MB. The first `GET /api/vault` (which also writes each book's file) took 24 to 43 s and reached 734 to 975 MB. With 500 books this would need about 3.5 minutes and more than 6 GB. The parse runs inside `Jobs.lock` (line 1383), so other requests wait for each book in turn. After the first time, the manifest is still worked out again on every call: `practice()` took 117 ms for 50 books, 75 ms with the manifest kept (`try_manifest_memo.py`).
- **Suggestion:** (1) Write the manifest to a file in the book's folder the first time the book is opened, with the stamp (time and size) of the prepared EPUB it was made from, the book's `source_sha256`, and for each question the place of its passage (what `Jobs.sources` works out). Read that file, and keep it in memory by stamp (41 KB per book), in `manifest`, `practice`, `_place`, `_accept`, `process_view`, `process_waiting`, `file_book`, `reading_state` and `save_reading`. I checked that `notes.notebook` uses only `book.manifest()` (`notes.py:173`), so `process.items` and `bookfile.text` need no parsed book either. (2) Keep only the last three or four parsed books in `Jobs.books`. The reader, a new highlight and a question on a highlight are the only callers that need the text. (3) Parse outside the lock: look in the cache under the lock, parse without it, store under it.
- **Gain:** The first visit to Practice, Notes or Process drops from tens of seconds to well under a second for 50 books. Memory stays near 60 MB plus the few books in use. A library of 500 entries becomes possible.
- **Size:** medium.
- **Risk and tests:** A manifest that is older than its book. The stamp check covers a rebuild, which writes the prepared file again. `tests/test_reading.py`, `test_visible_and_web.py`, `test_notes.py`, `test_highlight_questions.py`, `test_srs.py`, `test_vault_web.py` and the browser tests exercise all of these paths. Add one test that Practice for two books opens no book.
- **Status:** verified (`measure_library.py`, `measure_vault.py`, `measure_book.py`).

#### R2. The EPUB engine walks the text of a book two to three times; all but once only to ask "is there a word here"

- **Where:** `prep/epub.py:366-375` (`_has_own_text`), `:451` (`_segment`), `prep/words.py:401-407` (`tokenize`).
- **What happens:** `tokenize` walks a text letter by letter in Python and builds the list of its words. `_has_own_text` and `_segment` call it only to see whether the list is empty, and `placeholder_for` does the same for a caption (lines 369, 373, 451 and 397). In a profile of one book, `_segment` took 1.49 s of 2.70 s. I replaced `tokenize` in these four places, in my own process, by one regular expression that looks for a single character that is neither a separator nor a removed character (`SEPARATORS`, `REMOVED`). For 16 books (Paine, the fixture, the prepared fixture and the 13 negative fixtures) the words and blocks were identical, and opening Paine went from 371 ms to 166 ms.
- **Suggestion:** Add `has_word(text)` to `words.py` with that expression, and use it in those four places. Keep `tokenize` where its words are used (`epub.py:314`, `build.py:260`).
- **Gain:** About 55% off every parse of a book. A book is parsed when it is first read, when a run starts, on every look at a file in the Add form, on every opening of the question editor, and five times in every rebuild (see R3).
- **Size:** small.
- **Risk and tests:** The new test must agree with `tokenize` for every text. A fresh `tokenize` has no note mark and no hidden text in play, so it returns a word exactly when one such character exists. `tests/prep/test_words.py` (52 tests), `test_extract_build.py` and the expected results of the fixture protect it. Add a test that compares the two functions on the texts of the test books.
- **Status:** verified (`try_fast_segment.py`).

#### R3. The question editor and the review open the whole run for every request; a rebuild parses the book five times

- **Where:** `runner.py:113-129` (`_open_run`), `:168-178` (`change_run`), `:181` (`review_data`), `web/server.py:397-409`, `outputs.py:62-138`.
- **What happens:** `GET /api/jobs/<id>/book-questions/<qid>` (opening the editor), `GET .../sections/<sid>/paragraphs` and `GET .../review` each read `state.json`, read and hash `source.epub` and parse the book: 329 to 501 ms, 336 to 356 ms and 341 to 454 ms. The first two also write `state.json` again (219 KB) although nothing changed: `change_run` writes it whatever `build` is (line 175). Saving an edit took 3.2 s, and a plain rebuild 2.5 to 3.0 s. In one rebuild the original book is parsed twice (`_open_run`, `validate_bytes`), the prepared book twice (`build_bytes` verify, `validate_bytes`) and the visible book once: 1.75 s of 2.48 s. `DEFERRED.md` says "about two seconds" for 50,000 words; here it is three.
- **Suggestion:** Keep the opened run (the book and the original bytes) per folder in a cache of one or two entries, by the stamp of `work/source.epub`, which never changes after a run. Do not write `state.json` when `build` is false and `change` returned without changing it (or give reads their own function). Hand the parsed original and the parsed prepared book to `validate_bytes` in place of bytes.
- **Gain:** The editor opens in a few milliseconds. With R2 a rebuild falls from about 3 s to about 1.2 s.
- **Size:** medium.
- **Risk and tests:** A parsed book handed on must not have been changed by the step before. `tests/test_editing.py`, `test_pipeline.py` (rebuild) and `tests/prep/test_validate.py` protect it.
- **Status:** verified (`measure_edit.py`, `profile_rebuild.py`, `rebuild_parses.py`).

#### R4. The library listing reads four files for every book, under the lock, on every poll

- **Where:** `web/server.py:1451-1453` (`listing`), `:184-194` (`summary`), `:207-214` (`cards`); the page polls at `index.html:692`.
- **What happens:** For each finished book `summary` reads and parses `reading.json` twice, `srs.json` and `notes.json`: 200 file reads for 50 books, 2,000 for 500. `GET /api/jobs` took 42 to 64 ms for 50 entries and 384 to 433 ms for 500 (443 KB of JSON), with `Jobs.lock` held for the whole time. While a run is going the library page asks every 2.5 s. During that, a reader's save of its place took 414 to 460 ms (median of two runs) in place of 2.8 ms.
- **Suggestion:** Keep each book's summary in memory with the stamps of the three files and the day, and work it out again only when a stamp changes. Three `stat` calls replace four reads and parses. Hold the lock only to copy the list of jobs.
- **Gain:** About 50 ms becomes about 2 ms for 50 entries; the lock is no longer held for 0.4 s at 500.
- **Size:** small.
- **Risk and tests:** A summary that is one write old. Every write of these files goes through `write_json`, which changes the stamp. `tests/test_visible_and_web.py`, `test_srs.py` and `tests/browser/test_app.py` read these numbers after changes.
- **Status:** verified (`measure_library.py`, `measure_listing_lock.py`).

#### R5. The notes folder is walked in full on every save, every highlight and every four-second look

- **Where:** `vault.py:697-720` (`refresh`), `:661-666` (`_files`), `:745` (`get`), `:1203-1222` (`save`), `:1371` (`put_book`); `web/static/notes.js:635` (the look every 4 s); `web/server.py:743-757` (`notes_do` calls `file_book`).
- **What happens:** `refresh(force=True)` lists every file and asks for its stamp. For 10,000 notes this took 173 to 268 ms; 65% of it is `pathlib` (`relative_to`, comparing `Path` objects). The same walk with `os.scandir` and plain strings took 37 to 43 ms. The walk runs for every save of a note (285 ms in all at 10,000 notes, 24 ms at 1,000), every highlight made in the reader (234 to 251 ms at 10,000, 41 ms at 1,000, 4.7 ms with no notes folder), and, at most once a second, for every other request, among them the look at an open note every 4 s (221 ms). After a save, `_settle` (23 ms) and the back-links (47 ms) are made again for all notes. `_joined` is made again for every graph, neighbourhood and "revisit" (73 ms).
- **Suggestion:** Walk with `os.scandir` and keep paths as strings. For `get` of one note and for the token, ask for the stamp of that one file. In `save`, `set_props` and `add_to`, check the one file's stamp just before writing, in place of the full walk; this also shortens the time in which a change by another program can be missed, from the length of the walk to almost nothing. Keep `_joined` as `_back` is kept, until the index changes.
- **Gain:** At 10,000 notes a save falls from about 285 ms to about 80 ms, a highlight from about 240 ms to about 50 ms, the idle look from 221 ms to under 1 ms.
- **Size:** small.
- **Risk and tests:** The order of files (sorted, hidden folders left out, `.md` in any letter case) must stay the same. `tests/test_vault.py` (24 tests) and `test_vault_web.py` cover naming, case and outside changes.
- **Status:** verified (`measure_vault.py`, `compare_walk.py`). This is the measurement asked for under "A large notes folder" in `DEFERRED.md`: 1,000 notes are read in 0.55 s and 10,000 in 6.4 s (120 MB), so "a few thousand in under a second" holds up to about 1,800.

#### R6. Search works out a snippet for every note that matches, and shows sixty

- **Where:** `vault.py:1430`.
- **What happens:** For each match, `_plain_text(note.body)` runs four regular expressions over the whole note, before the list is sorted and cut to 60. A search for a common word took 75 to 98 ms in 1,000 notes and 748 to 873 ms in 10,000 (946 to 1,188 ms as a request). 70% of the time is `_plain_text`. The page asks again 220 ms after each keystroke.
- **Suggestion:** Collect `(rank, note)` first, sort, cut to `limit`, then make the snippets.
- **Gain:** About 0.8 s becomes about 0.1 s at 10,000 notes.
- **Size:** small.
- **Risk and tests:** None in behaviour. `tests/test_vault.py` covers search results and their order.
- **Status:** verified (`measure_vault.py`, with a profile).

#### R7. Small repeats inside one request

- **Where:** `web/server.py:858-863` (`_place`), `:1238-1245` (`vault_search`), `:1019-1029` (`vault_related`), `:910-922` (`vault_home`).
- **What happens:** For each of up to 60 passages found, `_place` works out the book's manifest and reads its `notes.json` again: 184 of 294 ms in a search for a word that is in the books. `vault_home` builds the whole Process view (`process_view()`, 82% of its 135 to 160 ms for 50 books) to show one number.
- **Suggestion:** In one request, read each book's manifest and notes once (a dictionary by book). For the front page, count waiting items without building the rows.
- **Gain:** About 100 ms off these requests at 50 books. R1 removes most of the manifest cost by itself.
- **Size:** small.
- **Risk and tests:** `tests/test_vault_web.py`.
- **Status:** verified (profiles in `measure_vault.py`).

### 2.2 The pages

#### P1. The graph freezes the tab when one note has many links, starts slowly with thousands of notes, and never stops drawing

- **Where:** `web/static/index.html:1177` (where points start), `:1198-1217` (`step`), `:1219-1241` (`draw`), `:1249` (`loop`).
- **What happens:** Three things.
  1. *A hang.* A link pulls two points together with a force that grows with their distance, and nothing limits a point's speed. A point with many links is pulled by all of them at once and jumps past its rest place, further each turn. I ran the page's arithmetic again in Python (`graph_layout_port.py`): with 100 links to one note the largest coordinate reaches 270,000; with 120 it reaches 4 × 10^17; with 150, 9 × 10^26. In JavaScript, when a coordinate divided by 190 is above 2^53, the loop `for (let i = gx - 1; i <= gx + 1; i++)` never ends, because adding 1 no longer changes the number. In Chromium a folder of 300 notes in which 120 link to one note froze the tab (no answer in 120 s); with 100 it stayed alive. A book cited by 120 notes, or an index note with 120 links, is an ordinary thing in a folder of some size.
  2. *A slow start.* All points start inside a circle of 260 pixels, whatever their number. The grid that keeps the layout quick has cells of 190 pixels, so at first every point is compared with almost every other. With 3,000 notes the page drew nothing for 2.9 s, then ran at 12 frames a second for 3 s. With 6,000: 7.2 s, then 5 frames a second for 7 s. With 10,000: 15.5 s, then 2 to 3 frames a second for 17 s.
  3. *No rest.* `loop` draws the whole picture for every frame also when the layout has settled and nothing moves. With 1,000 notes an idle graph page used 57% of one core. With 10,000 it never rose above 18 frames a second. `draw` also sorts all points for every frame (`busy`).
- **Suggestion:** Limit how far a point moves in one turn (for example 40 pixels) and divide the pull of a link by the square root of the links at its ends. Start the points in a circle whose radius grows with the square root of their number. Stop the turns of one frame after about 8 ms. Ask for the next frame only while the layout is warm or something changed (hover, drag, zoom, search, filter). Work out `busy` in `choose`, once.
- **Gain:** No hang. An idle graph page uses no processor time. A few thousand notes open in about a second.
- **Size:** medium.
- **Risk and tests:** The picture will look a little different. `tests/browser/test_knowledge.py::test_the_graph_a_neighbourhood_revisit_and_the_timeline` checks counts and filters with a handful of points; it waits a fixed 1.5 s "for the layout to settle", which could then wait for the settled state itself. Add a test with one note that 150 others link to.
- **Status:** verified (`measure_hub.py`, `graph_layout_port.py`, `measure_graph.py`, `measure_pages.py`). Headless Chromium draws without a graphics card, so frames will be quicker on a real screen; the hang does not depend on that. `DEFERRED.md` lists "The graph with many notes" as not measured: these are the numbers. The hang is not listed there.

#### P2. The Process view draws every waiting item, and draws them all again when one is chosen

- **Where:** `web/static/index.html:1358` (`choose` calls `draw`), `:1372-1392` (`draw`), `:1341`; `web/server.py:926-964`.
- **What happens:** `GET /api/process` returns every item of every book in all three states: 4.0 MB for 8,300 items (50 books with 165 highlights each). The page makes a row for each waiting item: 46,213 elements, 2.3 s until drawn. Choosing one item calls `draw()`, which builds all rows again: 1.9 to 2.3 s for one click. Items wait until they are processed or set aside, so this list grows with every highlight ever made. With 2,000 items a click would cost about half a second.
- **Suggestion:** On a choice, change `aria-current` on the two rows concerned and leave the list alone. Draw the first 200 rows and add more when the reader scrolls near the end (or draw one book's group when it is opened). Send only the state that is shown, with the three counts.
- **Gain:** A click answers at once at any size; the first draw and the download stay small.
- **Size:** medium.
- **Risk and tests:** "Set aside all" and the order "oldest first" work on the whole list, so the data must stay complete in memory or be asked for. `tests/browser/test_knowledge.py::test_processing_a_highlight_into_a_note_a_source_or_aside` covers the view with a few items.
- **Status:** verified (`measure_pages.py`).

#### P3. One failed poll stops the page from updating; each poll takes the keyboard's place away

- **Where:** `web/static/index.html:660` and `:692` (library), `:1628` and `:1635` (book page).
- **What happens:** When the request for the list or the run fails once, the page shows the error and returns before it sets the next timer. The run goes on, and the page shows its last state until it is reloaded. A request can fail for a moment: the app being started again by `update`, or the computer waking from sleep. Also, each poll of the library replaces every row, so a row that had the keyboard's focus loses it every 2.5 s (measured: focus on a row was on `BODY` after the next poll).
- **Suggestion:** On an error, keep what is drawn, show the message in a line of its own, and try again after 5 s. Replace only the rows whose data changed, or put the focus back by job id.
- **Gain:** A run page that recovers by itself; a library that can be used with the keyboard during a run.
- **Size:** small.
- **Risk and tests:** `tests/browser/test_app.py`, `test_steer.py`.
- **Status:** the stop on error is read from the code, not produced; the lost focus is verified (`measure_library_page.py`).

#### P4. Pages and scripts are sent in full on every load, without compression

- **Where:** `web/server.py:1929-1939` (`send_page`, `Cache-Control: no-store`).
- **What happens:** The library page and its three scripts are 296 KB on every load (84 KB with gzip); the editor is 367 KB (120 KB) on every page load that opens a note. The reader and the library are separate documents, so going from one to the other loads everything again. On the same computer this costs a few milliseconds, and the browser cannot keep the compiled script. The comment at `server.py:2405` gives the reason for `no-store`: a page must come from the same version as the server.
- **Suggestion:** Send an `ETag` made when the app starts (a hash of each file) with `Cache-Control: no-cache`, and answer `304` when it matches. The page still always matches the running server. Book pictures already have `max-age=3600`, which is right.
- **Gain:** Small on localhost. Worth doing only together with other work on `send_page`.
- **Size:** small.
- **Risk and tests:** `tests/test_visible_and_web.py` reads the headers of pages.
- **Status:** verified (headers and sizes in `measure_pages.py`).

### 2.3 Concurrency

#### C1. The one lock is held across model calls and across the rebuilding of a book's files

- **Where:** `web/server.py:358-377` (`_edit`), `:476-479` (`check_question`), `:481-485` (`rewrite_question`), `:425-427` (`add_question` with the model's choice of passage), `:379-387` (`_models`).
- **What happens:** `_edit` takes `Jobs.lock` and calls `change_run`, which runs the caller's function and then builds every file. For "Save and check", "Rewrite" and "I let the model pick the paragraphs" that function calls the model. With a stand-in model that takes 3 s, `GET /api/jobs` sent during the check was answered after 2.9 s. Saving any edit holds the lock for the 3.2 s of the rebuild. Everything that needs the lock waits: the library, the reader's saves of its place, highlights, Practice. A real call takes 2 to 10 s by the README's own numbers. Its limit is 30 minutes, with three more tries (M2), and the backend made by `_models` has no stop event, so nothing can end it.
- **Suggestion:** In `_edit`, hold a lock per book (a dictionary of locks by job id) for the read, change and write of the run, and take `Jobs.lock` only for the short update of `job` at the end. For the three calls with a model: read what the call needs under the book's lock, call the model with no lock, then apply the result under the book's lock after checking that the question is still as it was.
- **Gain:** The app stays usable while a question is checked or a book's files are written.
- **Size:** medium.
- **Risk and tests:** Two edits of one book must still follow one another; the per-book lock keeps that. `Jobs.rebuild` should take the same lock (C5). `tests/test_editing.py::test_the_app_edits_adds_checks_and_rewrites` and `tests/browser/test_editing.py` cover the paths. Add a test that a second request is answered while a slow check runs.
- **Status:** verified (`measure_edit.py`). The model's help in the notes folder (`assist.py:120-133`, `:177-200`) and questions on highlights (`server.py:556-620`) already call the model outside any lock.

#### C2. Two reader tabs of one book: the older tab removes the newer tab's answers, and later answers make no cards

- **Where:** `web/static/reader.html:298-304` (`save` sends the whole state), `:324` (a save when the page is left), `reading.py:294` (`clean_state` takes the attempts as sent), `:331-352` (`merge_state` keeps first grades only), `srs.py:193` and `:207` (`take_in`).
- **What happens:** Every save sends the page's whole state, and the server keeps it, except that first grades are never lost. I replayed two tabs through the API (`two_tabs.py`). Tab A is opened and left. Tab B answers five questions with typed answers and reads on for 30 minutes. Then tab A is closed, and sends its own state as it leaves. After that: the five typed answers are gone (46 attempts, not 51), the place is back at chapter 5 (B was at 7), and the 30 minutes are gone. The five grades are kept. Then it gets worse: `take_in` remembers how many attempts it has seen (`seen` = 51) and takes in `attempts[seen:]`. The list now has 46 entries, so the next answers at checkpoints (entries 47 and 48) are never taken in. In the test, two questions answered afterwards got no cards. This lasts until the list is longer than 51 again, and the answers in between are lost to Practice for good.
- **Suggestion:** On the server, merge attempts as grades are merged: the union of old and new by `(id, at)`, in time order. Keep the larger of old and new for reading seconds and words, and the later `last_read`. In `take_in`, remember which attempts were taken in by `(id, at)` (or the `at` of the last one), not by position. Optionally give the state a number that grows with each save, so that a page can see it is behind and load again.
- **Gain:** No lost answers or reading time with two tabs, and cards that cannot fall silent.
- **Size:** medium.
- **Risk and tests:** "Start a question over" removes attempts on purpose (`x_restarted`); the merge must respect it as `merge_state` does for grades. `tests/test_reading.py` (14 tests) and `test_srs.py` (11 tests) cover merging and `take_in`. Add the two-tab case to both.
- **Status:** verified (`two_tabs.py`).

#### C3. The reader page still has the race that was fixed in the notebook

- **Where:** `web/static/reader.html:1353` and `:1361`.
- **What happens:** When it opens, the reader asks for the highlights (`GET .../notes`, which carries the questions made from them) together with the book and the place, and then, in a second request, for what is being written (`GET .../questions`). If nothing is pending, the answer is kept in `asking` and its list of questions is not used. A question that was finished between the two requests is in the first answer not yet, and is dropped from the second. I held the first answer back in Chromium while a question was made: the app held one question, the page showed none, and stayed so until the next action on a question. The time between the two requests is the time the book takes to open, 0.4 s or more for a first opening.
- **Suggestion:** Always take the questions from the second answer: in the `else` branch set `NB.questions = state.questions` and call `syncPrompts()`. Or give `GET .../notes` the same `asking` member that the notebook got, read under the same lock.
- **Gain:** A question finished while the reader loads is shown.
- **Size:** small.
- **Risk and tests:** `tests/browser/test_questions.py` has the test for the notebook's race; the same shape of test fits here.
- **Status:** verified (`race_reader_init.py`). I looked for the pattern in the other pages and in `Jobs`: the notebook, `questions_state`, `propose`, `question_do` and `notes_do` read and write under one lock each. I found no third place.

#### C4. Stopping the app during a run: it does not stop, and model processes are left running

- **Where:** `web/server.py:2445-2452` (`serve`), `pipeline.py:510` (the pool of a run), `llm.py:245-250` (each model process gets its own process group).
- **What happens:** I started the real app with a stand-in `claude` that takes two minutes for a call, added a book and sent Ctrl-C. The app printed "Stopping." and was still there 15 s later. The threads of the run's pool are ordinary threads, and Python waits for them before it exits; they wait for the model process, up to its 30-minute limit. When I then killed the app, the stand-in was still running: it is in a process group of its own, so neither Ctrl-C nor the death of the app ends it. A real `claude` or `codex` would go on and use the plan for an answer nobody reads. The same holds for the shutdown that `update` asks for.
- **Suggestion:** In `serve`, after `serve_forever` returns (and on Ctrl-C), set every stop event in `Jobs.cancels` and in `Jobs.asks`, then wait a second or two for the runs to end. `run_process` already asks its `halt` twice a second and ends the process tree. The runs then save themselves as "Stopped", which is truer than "The app was closed during this run".
- **Gain:** Ctrl-C stops the app within a second, and no model process is left behind.
- **Size:** small.
- **Risk and tests:** `tests/test_recovery.py` covers the giving up of a running program; `tests/test_update.py` covers stopping the app. Add the case "stop while a call hangs" at the level of `serve`.
- **Status:** verified (`stop_during_run.py`).

#### C5. "Rebuild files" and stale downloads run outside the lock, and the files are written in place

- **Where:** `web/server.py:1889-1903` (`Jobs.rebuild`, the call at line 1896 holds no lock), `:2372-2374` (a download rebuilds when the exports are stale), `outputs.py:73`, `:91`, `:104`, `:123`, `:137` (`write_bytes`, `write_text`).
- **What happens:** After a question from a highlight is accepted, the next download of the deck, the text file or `questions.json` rebuilds every file first. Three downloads asked for together each started a rebuild: three rebuilds, 10.5 s in place of 3.1 s, all writing the same files at the same time. The EPUBs, the text file and the report are written directly, not through a temporary file, so for a moment each file is empty or half written. A download that reads then, or the reader asking for the book, gets a broken file. A rebuild that changes which questions are left out, started less than about 0.4 s before an edit of the same book, loses that change: both read `state.json`, and the edit writes last.
- **Suggestion:** Take the per-book lock of C1 in `Jobs.rebuild` and in the download route; inside it, look at `exports_stale` again, so that only the first download rebuilds. Write every output through a temporary file and `os.replace`, as `write_json` does.
- **Gain:** One rebuild for several downloads; no half-written file can be served or left behind by a crash.
- **Size:** small.
- **Risk and tests:** `tests/test_highlight_questions.py::test_the_exports_hold_the_questions_from_highlights_and_are_refreshed_when_they_change`, `tests/test_pipeline.py` (rebuild).
- **Status:** the three rebuilds are verified (`stale_downloads.py`). I saw no broken file in six rounds of three downloads, and I could not produce the lost change in six tries: both are read from the code (`race_rebuild.py`, `race_rebuild2.py`).

#### C6. Replacing a file on Windows while something has it open; no flush to disk for the run's state

- **Where:** `pipeline.py:73-85` (`write_json`); readers outside the lock at `web/server.py:701`, `:1395`, `:1465`.
- **What happens:** On Windows `os.replace` fails with `PermissionError` when another handle has the target open without the right to delete it. Python's own `open` is such a handle, and so are virus scanners and search indexers that open a file just after it was written. Some reads of `reading.json` and `notes.json` happen outside `Jobs.lock`. A failed save in the reader is tried again by the next save. A failed save of `state.json` in a run ends the run with "Unexpected error" (it can be continued). Separately, `write_json` does not flush the file to disk before the rename, so a power cut can leave an empty `state.json`, which `load_state` then refuses.
- **Suggestion:** In `write_json`, try `os.replace` up to five times with a pause of 20 ms on `PermissionError`. For `state.json` call `os.fsync` before the rename (a few milliseconds beside a model call).
- **Gain:** Fewer rare failures on Windows; a run that survives a power cut.
- **Size:** small.
- **Risk and tests:** None in behaviour.
- **Status:** suspected, not verified: I had no Windows machine. To confirm, run a loop on Windows that saves the reading state while another thread reads it, and count the errors. The tests pass on Windows in CI, so it is rare at best.

### 2.4 Start-up

#### S1. The tools are asked one after another before the app listens

- **Where:** `web/server.py:2393-2413` (`make_server`), `llm.py:371-383`, `:385-419`, `:547-564`, `:566-680`.
- **What happens:** Importing `ebook_processor.cli` takes 0.22 s (plain Python: 0.03 s); `jsonschema` is the largest part at about 0.06 s. `lxml.etree` is loaded (7 ms). `trafilatura` (0.21 s), `mammoth` (0.13 s), `markdown` and `genanki` are loaded only when used, which is right. With stand-in tools that answer at once, the app answered 0.27 s after it was started. With the real tools, `make_server` first runs `claude --version`, `codex --version`, `codex login status`, then asks Claude Code for its models (limit 45 s) and Codex for its models (limit 30 s, and twice 30 s more for the second source), each after the other, and only then opens the port. The browser is opened 0.5 s after that.
- **Suggestion:** Open the port first. Ask the tools in a background thread (the two tools side by side), and let `GET /api/info` say "still asking" until the answers are there; the Add form is the only place that needs them.
- **Gain:** The library is on screen at once, however slow the tools are.
- **Size:** medium (the server's part is small; the Add form and Settings must learn to wait for the answer).
- **Risk and tests:** The page must cope with `info` that is not complete yet. `tests/test_visible_and_web.py`, `test_codex.py`.
- **Status:** the order is read from the code. I did not time the real tools: asking Claude Code for its models may reach the web, which this session must not do. `claude --version` itself took 0.01 s here.

### 2.5 The model pipeline

#### M1. Any three digits that begin with 5 make an error "temporary"

- **Where:** `llm.py:329` (`_TRANSIENT_RE` contains `5\d\d` with nothing around it), used at `:501`.
- **What happens:** The expression is meant for HTTP status 500 to 599. It matches inside any number. `_classify("Prompt is too long: 215500 tokens > 200000 maximum")` and `_classify("Invalid model name: claude-opus-4-5-20251101")` both come back as temporary. Such a call is then made four times with waits of 15, 60 and 180 s: 255 s and three wasted calls before the reader is told. The Codex expression (`:514`) has `\b5\d\d\b`, which is right.
- **Suggestion:** Use `\b5\d\d\b` here too, or rely on `status in (429, 500, 502, 503, 529)`, which is already checked.
- **Gain:** A wrong model name or a part that is too long is reported at once.
- **Size:** small.
- **Risk and tests:** `tests/test_recovery.py` checks which messages are tried again.
- **Status:** verified for the rule (`classify_errors.py`). The two messages are my own wording of likely errors; I did not see real ones.

#### M2. A request the reader waits for uses the limits of a whole run

- **Where:** `llm.py:360-362` and `:531-533` (1,800 s for a call, 3 more tries, waits of 15, 60 and 180 s), `web/server.py:379-387`, `:1033-1043`, `:574-575`.
- **What happens:** "Save and check", "Rewrite", "Find connections", "Ask" and "Make a question" go through the same backend as a run. A tool that hangs keeps the request open for 30 minutes, four times. The page has no way to stop it. Before each such request the app also starts `claude --version` and sends one request to the service's address to see whether it answers (limit 8 s): `check_available` builds a new connection watch every time.
- **Suggestion:** Give `default_backend` a profile for requests of this kind: 120 s for a call, one more try after 5 s. Keep the answer of `check_available` for a minute.
- **Gain:** A hung tool is reported after two minutes, not two hours. Each request starts a little sooner.
- **Size:** small.
- **Risk and tests:** `tests/test_recovery.py`, `test_codex.py`. `REVIEW-structure.md` (S3) proposes one function for "make a backend and check it"; that is the place for both changes.
- **Status:** read from the code. Not measured: it needs the real tools and the web.

#### M3. A book that is one long document is drafted one call at a time, and no check starts before all drafting is done

- **Where:** `pipeline.py:604-628` (`long`), `:628-629` (`draft` then `assemble`), `:718-757` (`check_loop`).
- **What happens:** Parts of different documents run side by side, three at a time. One long document is drafted window after window, because each window starts where the model ended the one before. Checking starts only after every part is drafted. For a book of several chapters this is fine: all three places are busy. For a text that is one document (a long text file with no headings, an EPUB with one file) one place works and two wait, through the whole drafting.
- **Suggestion:** Give sections their ids as each part is drafted, and send finished parts to the check while later windows are drafted.
- **Gain:** For a single document of 60,000 words, the checks (about two of twelve minutes by my estimate) would run during the drafting. Nothing for ordinary books.
- **Size:** large.
- **Risk and tests:** Section ids are given in `assemble` in reading order, and a trial chapter is carried over there; both would have to move. `tests/test_pipeline.py` (31 tests) covers the order of ids and the continuing of a stopped run.
- **Status:** read from the code; the gain is an estimate. I would not do this before the other findings.

What I found in good order in the pipeline is in section 4.

### 2.6 Memory and limits

#### L1. Nothing limits what a file or a web answer unpacks to; a negative `Content-Length` lifts the upload limit

- **Where:** `prep/epubzip.py:138-152` (`Archive.read`, `zlib.decompress` at line 148), `sources/from_docx.py:43` and `:62`, `sources/fetch.py:83-91`, `web/server.py:1957-1964` (`body`).
- **What happens:**
  1. *EPUB.* A member is unpacked in full before its size is looked at. An EPUB of 405 KB whose one chapter unpacks to 400 MB of spaces kept `POST /api/look` busy for 222 s at full processor use and took about 0.4 GB. Python runs one thread at a time, so the whole app was slow for those minutes.
  2. *Word.* A `.docx` of 298 KB whose text unpacks to 300 MB had not finished after more than six minutes, when I stopped it. The limit of 50 MB (`sources/__init__.py:192`) is on the packed file.
  3. *Web.* `fetch.get` reads at most 10 MB, then unpacks gzip in full, and only then compares with the limit. An answer of 0.5 MB that unpacks to 500 MB was unpacked (4.4 s) before it was refused. Pictures go the same way with a limit of 5 MB packed.
  4. *Uploads.* `body()` refuses a length above 300 MB. A length of `-1` passes the test, and `rfile.read(-1)` reads until the sender stops: the server took 400 MB and its memory rose to about 900 MB.
- **Suggestion:** In `Archive.read`, unpack with `zlib.decompressobj(-15).decompress(raw, limit + 1)` and refuse a member above, say, 64 MB, and a book whose members together pass 1 GB. For `.docx`, look at the sizes in the zip's directory before giving the file to `mammoth`, and refuse above the same limit. In `fetch.get`, unpack with a `decompressobj` and `max_length = limit + 1`. In `body()`, refuse a length below zero.
- **Gain:** A small hostile file can no longer hold the app for minutes or fill the memory.
- **Size:** small.
- **Risk and tests:** A real book with one very large member (a picture book). 64 MB for one member of text or one picture is generous; the firmware could not show such a book in any case. `tests/prep/test_extract_build.py`, `tests/sources/test_convert.py` and `test_sources_web.py` (which has a size test for fetching) protect the paths.
- **How it could be reached:** Cases 1 and 2 need the reader to add a hostile file themselves. Case 3 needs the reader to add a web address whose page, or any picture named in it, is hostile; pictures are fetched from whatever host the page names. Case 4 needs a request with the app's own header, so a page of another site cannot send it (but see I1). In practice: low, and the harm is a slow or stopped app, not lost data.
- **Status:** verified (`hostile.py`; cases 1 to 3 with the argument `bombs`). Entity expansion is not a problem: the XML reader refused a 5 GB "billion laughs" chapter in 0.4 s (expat 2.9.0 limits it).

#### L2. Two smaller limits

- **Where:** `web/server.py:1614` and `:1660-1676` (`held`), `:1197-1236` (`timeline`).
- **What happens:** A file looked at in the Add form is held for "Process": up to four, each up to 50 MB plus its fetched pictures (up to 40 MB). The 30 minutes after which one is dropped are only looked at when the form is used again, so a file looked at and left stays in memory until the app is stopped. The timeline shows 30 days, but a day has no limit: in a folder whose files all carry the same date (a folder copied, restored or cloned) the first day held all 10,000 notes, 8 MB of JSON.
- **Suggestion:** Drop held files that are too old whenever any request touches `held`, or with a timer. Cut each list of a day to, say, 200 with a count of the rest.
- **Gain:** Up to a few hundred megabytes given back; a timeline that always opens quickly.
- **Size:** small.
- **Risk and tests:** `tests/sources/test_sources_web.py`, `tests/browser/test_knowledge.py`.
- **Status:** the timeline's size is measured (`measure_vault.py`, an effect of my made-up folder, in which every file was written today); the held files are read from the code.

Uploads of 300 MB are held in memory several times over (the request, the parsed archive, the copy for the run). For one user on their own computer I see no need to change that.

### 2.7 Input handling

#### I1. The `Host` header is not checked, so a page of another site can become "the same site"

- **Where:** `web/server.py:1986-1999` (`dispatch`), `:63` (`CSRF_HEADER`).
- **What happens:** The app refuses every request that changes something unless it carries `X-Ebook-Processor: 1`. A page of another site cannot add that header to a request to `localhost`. This is sound. The gap is DNS rebinding: a hostile page at `evil.example:8765` makes its own name point to `127.0.0.1`. The browser then treats the app as the page's own site, and the page may send any header and read every answer. The app accepts it: `PUT /api/settings` with `Host: evil.example:8765` and the header was answered `200`. From there the page could read every book, highlight and note; add any `.txt`, `.md`, `.html`, `.docx` or `.epub` file by its path (`POST /api/jobs/local`) and read its text back as chapters; point the notes folder at any folder (`PUT /api/settings`) and read, change or move to `.trash` the `.md` files in it; and start runs on the reader's plan.
- **Suggestion:** In `dispatch`, refuse any request whose `Host` is not `localhost`, `127.0.0.1` or `[::1]` with the app's port, or the address given with `--host`. For requests that change something, also refuse an `Origin` header that names another site. In the help of `--host`, say that an address other than this computer opens the app, with no password, to everyone who can reach it.
- **Gain:** Closes the one way around the header check.
- **Size:** small.
- **Risk and tests:** A reader who opens the app by another name (a name in their hosts file, a proxy). An option to allow one more name covers that. `tests/test_visible_and_web.py` has the test for the header; the `Host` test belongs beside it.
- **How it could be reached:** The reader must visit a hostile page while the app runs, the attacker must run a DNS server that changes its answer, and the browser must allow a public page to reach a local address after the change. Recent versions of Chrome ask the user before a public page may reach a local address; other browsers do less, and I did not test any of them. The default port is fixed (8765), which helps an attacker. In practice: unlikely, but the harm would be large (private notes read, files changed), and the fix is ten lines.
- **Status:** verified that the request is accepted (`hostile.py`). What a page could do after that is read from the routes. The attack in a browser was not carried out.

#### I2. Pictures of a web page are fetched one by one, with no limit on how many are tried

- **Where:** `sources/pack.py:60-98` (line 74), `sources/fetch.py:39-52`, `:75-107`, `sources/__init__.py:129-162`.
- **What happens:** `MAX_PICTURES` (60) counts pictures that were kept. A picture that fails, is no picture, or is a counting pixel does not count, so all of them are tried. A page with 30 pictures that each answer after 0.2 s took 6.4 s to look at. A page with 200 pictures that are not there took 42 s. A host that does not answer costs 10 s for each picture. The 10 s are a limit for each read, not for the whole answer, so a host that sends one byte every few seconds can hold a request for hours. Two smaller points: `is_private` looks the host's address up, and `urllib` looks it up again for the request, so a host that changes its answer between the two is not caught; and addresses in `100.64.0.0/10` (used by some private networks, Tailscale among them) do not count as private (`is_private("100.64.1.1")` is false).
- **Suggestion:** Stop after 80 tries or 30 s in all, and say so in the warning the form already shows. Fetch four to six pictures side by side (a small thread pool; the order of names must stay as it is). Give one answer a time limit for the whole of it. Test `not address.is_global` in place of the list of kinds.
- **Gain:** A page with many pictures is ready in a sixth of the time; a bad page cannot hold the form for minutes.
- **Size:** small for the limits, medium with the fetching side by side.
- **Risk and tests:** The book made from a page must be the same bytes each time (`pack.py` says so); file names are given in the order of the page, which a pool must keep. `tests/sources/test_convert.py` and `test_sources_web.py` cover pictures, the private rule and the same-bytes rule.
- **How it could be reached:** The reader adds a page; the page names the pictures. A request to a local address through a changed DNS answer would reach a local service with a `GET` and show the answer only if it is a picture. In practice: low.
- **Status:** the times are verified (`measure_pictures.py`). The slow host and the DNS point are read from the code. `DEFERRED.md` says real web sites were not tried at all; that still holds.

#### I3. A deeply nested chapter ends in "Unexpected error" and a thousand lines in the terminal

- **Where:** `prep/words.py:289-355` (`element` calls itself for each child), `prep/xmltree.py:76-80`, `prep/epub.py:438-455`.
- **What happens:** A chapter whose elements are nested 3,000 deep (or 200,000) raises `RecursionError`. The request is answered with `500 Unexpected error: RecursionError(...)` and the traceback is printed. Nothing else breaks. The HTML converter is not affected: `lxml` cut a file nested 100,000 deep without trouble.
- **Suggestion:** In `load_book_bytes`, catch `RecursionError` and raise `EpubError("a chapter is nested too deeply to be read")`.
- **Gain:** A clear message in place of a fault.
- **Size:** small.
- **Risk and tests:** None. `tests/prep/test_extract_build.py` has the refusals of broken books.
- **Status:** verified (`hostile.py bombs`).

#### I4. One file that cannot be read breaks the whole library, or the start of the app

- **Where:** `web/server.py:163-172` (`Jobs.__init__`), `:184-194` (`summary`), `:1451-1453` (`listing`).
- **What happens:** A `job.json` that is not JSON is skipped. One that is JSON of the wrong shape is not: with `{"id": "..."}` alone the app does not start (`KeyError: 'status'`), with `[]` neither (`AttributeError`). A `notes.json` with a highlight that lacks a member makes every `GET /api/jobs` fail with `500 KeyError('note')`, so the library page shows an error for all books. The app writes these files itself and writes them whole, so this needs a file changed by hand, by a sync tool or by an older version.
- **Suggestion:** In `__init__`, skip a `job.json` that lacks `status`, `created` or `file`, and print its path. In `listing`, catch an error from `summary` for one job and show that entry as "cannot be read", with the reason.
- **Gain:** One bad entry costs one entry.
- **Size:** small.
- **Risk and tests:** `tests/test_visible_and_web.py`.
- **Status:** verified (`hostile.py`).

### 2.8 The tests

#### T1. Twenty-one tests are 58% of the time: each starts Java for EPUBCheck

- **Where:** `tests/prep/test_validate.py` (the tests with `real_epubcheck` and the command line tests), `tests/prep/test_extract_build.py`, `tests/prep/prepkit.py:55-59`, `prep/epubcheck.py:156-165`.
- **What happens:** The tests outside `tests/browser` took 220 s here (856 passed, 1 skipped). The 21 slowest took 128 s, 4.8 to 13.3 s each; every one of them runs the real EPUBCheck, some twice. All the other tests together took 92 s. The browser tests take about 1 s each in Chromium (8 tests of `test_reading.py` in 9.5 s; 14 of `test_knowledge.py` in 39.5 s).
- **Suggestion:** Mark these tests (`@pytest.mark.epubcheck`) so that `pytest -m "not epubcheck"` gives a quick run of about 90 s; CI runs all. Keep EPUBCheck's reports in its cache folder by the SHA-256 of the checked file and the EPUBCheck version: several of the 21 tests check the same unchanged fixture, and on a developer's machine every later run would cost nothing. Starting Java with `-XX:TieredStopAtLevel=1 -XX:+UseSerialGC -Xshare:auto` took one run from 7.2 s to 4.4 s.
- **Gain:** A quick loop of a minute and a half, and a full run about two minutes shorter after the first.
- **Size:** small.
- **Risk and tests:** A kept report for a changed file is impossible when the key is the file's hash.
- **Status:** verified (`--durations=40`, `epubcheck_flags.py`). `REVIEW-structure.md` (T3) has the same finding and adds that these tests can reach the web for the download; I agree with it.

I found the suite steady. The details are in section 4.

## 3. Measurements

All scripts are in `/tmp/claude-0/-home-user/bfc4b135-fb74-5bc2-b3e1-8e9739bd7af0/scratchpad/review-speed/scripts/`; what they printed is in `.../review-speed/out/`. That folder belongs to the review session and may be cleared; the scripts are short, and each begins with a line that says what it does. `common.py` starts a scratch app with the stand-in model. `build_library.py`, `clone_library.py` and `build_vault.py` make the data.

| What | Data | Result | Script |
| --- | --- | --- | --- |
| Import `ebook_processor.cli` / `web.server` | — | 0.22 s / 0.27 s (plain Python 0.03 s) | `python3 -X importtime` |
| Time until the app answers after `serve` | stand-in tools | 0.27 s | `stop_during_run.py` |
| A run with the stand-in model | Paine, 52,786 words | 4.9 s | `build_library.py` |
| Open one prepared book | Paine | 366–404 ms, 12.6 MB | `measure_book.py` |
| The same with the cheap word test | Paine | 166 ms, same blocks in 16 books | `try_fast_segment.py` |
| `GET /api/jobs` | 50 / 500 entries | 42–64 ms / 384–433 ms; 200 / 2,000 files read; 44 / 443 KB | `measure_library.py` |
| Reader's save while the library is listed | 500 entries | 414–460 ms median (alone: 2.8 ms) | `measure_listing_lock.py` |
| `GET /api/practice`, first time | 50 books | 21.1 s, 59 → 688 MB | `measure_library.py` |
| `GET /api/practice`, later / one answer | 50 books | 122–152 ms / 201 ms | `measure_library.py` |
| `GET /api/vault`, first time | 50 books, 1,000 / 10,000 notes | 24–26 s, 734 MB / 39–43 s, 975 MB | `measure_vault.py` |
| `GET /api/vault`, later | the same | 144–160 ms / 290–328 ms | `measure_vault.py` |
| `GET /api/process` | 50 books, 8,300 items | 175–186 ms, 4.0 MB | `measure_vault.py` |
| Chapter, notebook, manifest, book page | Paine, 165 highlights | 8 ms (355 KB) / 6 ms / 2.5 ms / 4–6 ms | `measure_library.py` |
| Open the question editor / review | Paine | 329–501 ms / 341–454 ms | `measure_edit.py` |
| Save an edit / rebuild | Paine | 3.2 s / 2.5–3.0 s, five parses (1.75 s) | `measure_edit.py`, `profile_rebuild.py`, `rebuild_parses.py` |
| Another request during a 3 s model check | — | answered after 2.9 s | `measure_edit.py` |
| Three downloads with stale exports | Paine | 3 rebuilds, 10.5 s (one: 3.1 s) | `stale_downloads.py` |
| First read of the notes folder | 1,000 / 10,000 notes | 0.55 s, 11 MB / 6.4 s, 120 MB | `measure_vault.py` |
| Walk of the folder, nothing changed | 10,000 notes | 173–268 ms; 37–43 ms with `os.scandir` | `measure_vault.py`, `compare_walk.py` |
| Save a note | 1,000 / 10,000 notes | 24–36 ms / 285–352 ms | `measure_vault.py` |
| Highlight in the reader | none / 1,000 / 10,000 notes | 4.7 / 41 / 234–251 ms | `build_library.py`, `measure_vault.py` |
| Search, a common word | 1,000 / 10,000 notes | 75–98 ms / 748–873 ms | `measure_vault.py` |
| Graph data, revisit, related | 10,000 notes | 147–191 ms (2.0 MB) / 84–101 ms / 86–124 ms | `measure_vault.py` |
| Graph page: time with no frame, then frames a second | 1,000 / 3,000 / 6,000 / 10,000 notes | 0.6 s, 60 / 2.9 s, 12 then 55 / 7.2 s, 5 then 30 / 15.5 s, 3 then 18 | `measure_graph.py` |
| Graph page left alone | 1,000 notes | 57% of one core | `measure_pages.py` |
| Graph page, one note with 100 / 120 links | 300 notes | alive / no answer in 120 s | `measure_hub.py`, `graph_layout_port.py` |
| Process view: draw / one click | 8,300 items | 2.3 s, 46,213 elements / 1.9–2.3 s | `measure_pages.py` |
| Library page while it polls | 50 / 500 entries | 55 / 313 ms of page work in 15 s; focus lost | `measure_library_page.py` |
| Pages as sent / with gzip | — | library 296 / 84 KB; editor 367 / 120 KB | `measure_pages.py` |
| Two tabs of one book | — | 5 typed answers, the place and 30 min lost; later answers get no cards | `two_tabs.py` |
| A question finished while the reader loads | — | app has 1, page shows 0 | `race_reader_init.py` |
| Ctrl-C during a run | stand-in that takes 2 min | app still there after 15 s; model process left running | `stop_during_run.py` |
| EPUB that unpacks to 400 MB | 405 KB file | 222 s, about 0.4 GB | `hostile.py bombs` |
| Web answer that unpacks to 500 MB | 0.5 MB | unpacked, then refused, 4.4 s | `hostile.py` |
| `Content-Length: -1` | 400 MB sent | all read, about 900 MB | `hostile.py` |
| Page with 30 slow / 200 missing pictures | 0.2 s each | 6.4 s / 42.3 s | `measure_pictures.py` |
| Look at an HTML file | 29 MB, 6 million words | 46.5 s | `hostile.py bombs` |
| Tests outside `tests/browser` | 856 tests | 220 s; 21 EPUBCheck tests 128 s | `pytest --durations=40` |
| One EPUBCheck run, plain / with quick-start flags | fixture | 7.2 s / 4.4 s | `epubcheck_flags.py` |
| An error message with a number such as 215500 | — | tried 4 times, 255 s of waiting | `classify_errors.py` |

## 4. Looked at and found in good order

- **Writing of data.** `write_json` (`pipeline.py:73`) and `Vault._write` (`vault.py:668`) write through a temporary file and `os.replace`. Every store of a book (`reading.json`, `notes.json`, `srs.json`, `process.json`, `job.json`) is read, changed and written under `Jobs.lock` in one step. A run saves its state after every call and continues from there; a stopped run repeats only the calls that were in flight.
- **The notes folder and other programs.** A save carries the stamp the page was given and is refused with the current text when the file changed (`vault.py:1203-1222`). A failed replace says "Is it open in another program?". Renaming rewrites links from a fresh read. A file that disappears during a walk is skipped.
- **Paths.** Job ids are matched against a pattern before any path is built. Downloads must be a plain name that begins with the book's stem and lies in the book's folder (`server.py:2364-2371`). Book pictures are served only when the path is an image in the book's own list (`reading.py:101-105`). `Vault._inside` and `restore` resolve and compare paths. Pictures beside a local file must lie under its folder. Upload names are cleaned. Reading a path the reader typed is by design.
- **HTML that reaches the page.** Chapters are built from a fixed list of tags and attributes with every text escaped (`reading.py:173-248`); links are kept only for `http`, `https` and `mailto`. The pages build their own elements with text nodes; `innerHTML` is used only for chapter HTML. Markdown links in notes accept `https?://` only. Book pictures are sent with `Content-Security-Policy: sandbox`, so an SVG cannot run as a page. Pages may be framed by the app alone.
- **Requests from other sites.** Every request that is not a `GET` needs the header, checked before the body is read. No `Access-Control` header is sent. No `GET` does harm (some write small things: the notes folder is made, exports are rebuilt). The gap is I1.
- **Fetching.** Only `http` and `https`, also after a redirect; redirects are checked one by one; sizes are limited as sent (10 MB, 5 MB); pictures are not fetched from this computer or the local network.
- **XML.** Entity expansion is refused by the parser. A chapter is parsed with expat, with no network access and no outside files.
- **Imports.** The heavy libraries are loaded only when used. `python3 -X importtime` shows no `trafilatura`, `mammoth`, `markdown`, `genanki` or `charset_normalizer` at start.
- **The pipeline.** Parts of different documents and the batches of the check and the revision run side by side (three by default); the excluded ranges are reviewed in the same round. A model call is given up within half a second of a stop, with everything it started. A lost connection is waited out without using up tries. A usage limit ends the run with its state saved.
- **Ordinary requests at ordinary size.** With the book in memory: manifest 2.5 ms, largest chapter (19,335 words) 8 ms, notebook with 165 highlights 6 ms, search in the book 2 ms, the reader's save 2.8 ms, a note 1.4 ms. The reader left open sends one save every 5 s and does no other work (4 ms of script in 11 s). A page turn costs 13 ms. The library page does nothing when no run is going.
- **Locks in the model's help.** "Find connections", "Ask" and questions on notes and on highlights read under a lock and call the model outside it.
- **The editor.** Inline formatting is worked out for the visible lines only (`editor/src/index.js:208`). The editor is loaded when a note is opened, not before.
- **The tests' steadiness.** `tests/browser/test_knowledge.py` passes with its 14 tests in the opposite order. Time limits in `tests/test_recovery.py` and `tests/sources/test_convert.py` are upper limits of 2 to 20 s for things that take well under a second. Fixed waits are few: 120 to 300 ms after a key in the browser helpers, 1.2 s of RSVP playing, twice 1.5 s for the graph to settle, 2 s in the test of the notebook's race (longer than the page's 1.5 s on purpose). Waits for a run are loops with a limit of 20 to 30 s. The tightest is `test_a_stop_ends_the_wait_without_saying_the_connection_is_back` (2 s after a timer of 0.2 s), which would need a machine ten times slower to fail.
- **CI.** `pip` is cached, a newer push replaces a running one, jobs have a limit of 20 minutes, and the matrix covers the oldest Python.

## 5. Not reviewed, or not measured

- **Windows and macOS.** Everything was measured on Linux. File operations cost more on Windows, most of all with a virus scanner; R4 and R5 will weigh more there. C6 is a suspicion for that reason.
- **Real tools and the web.** No model was called and no real address was fetched. The time the real tools need at start-up (S1) and before each request (M2), real error messages (M1), and real pages (I2) are not measured.
- **Firefox and WebKit.** The pages were measured in headless Chromium only, which draws the canvas without a graphics card. The graph's frames a second will be better on a real screen.
- **Typing in a very large note.** The editor's block decorations are built again from the whole document on each change (`editor/src/index.js:176-179`). I did not measure typing in a note of 100,000 characters or more.
- **`.mhtml` files and `trafilatura` with hostile input.** I read `saved.py` and `from_html.py` and tested size and nesting through the HTML path, but made no hostile `.mhtml`. The Word bomb was stopped after six minutes, so its end (an error, or memory used up) is unknown.
- **A very large real book.** The largest real book was Paine's *Essays* (53,000 words). Parsing time grows in line with length (0.37 s for 53,000 words; the whole look at an HTML file of 6 million words took 46 s), so a book of 500,000 words should take about 4 s to open, 2 s after R2. I did not make one.
- **The lost change in C5 and the stopped poll in P3** follow from the code and were not produced.
- **`pytest-xdist`.** Running the tests on several cores would cut the suite to about a third, most of all the EPUBCheck tests. It is not installed here and I was asked not to install anything, so I did not try whether the tests are free of shared state (the EPUBCheck cache and `EBOOK_PROCESSOR_HOME` are the places to look).
- **The whole browser suite** was not run, as agreed: two files only (`test_reading.py`, and `test_knowledge.py` in the opposite order).
