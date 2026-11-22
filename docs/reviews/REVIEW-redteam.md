# Review of the two reviews (red team)

Written on 2026-11-22, on branch `claude/dreamy-allen-6un3tt`. The code is commit `4557303`, the same code both reviews looked at. This report tests `REVIEW-structure.md` (32 findings) and `REVIEW-speed-robustness.md` (28 findings): is each finding true, is it the whole truth, and what did both miss.

No code was changed. No model was called and no web address was fetched. Everything I ran used a scratch app with the tests' stand-in model and `EBOOK_PROCESSOR_HOME` in my scratch folder. My scripts are in `/tmp/claude-0/-home-user/bfc4b135-fb74-5bc2-b3e1-8e9739bd7af0/scratchpad/review-redteam/scripts/` and what they printed is in `.../review-redteam/out/`. The measurements were made on Linux with Python 3.13.18, Node 22 and headless Chromium.

Findings are named with their review: `speed/C1`, `structure/C1`.

## 1. Summary

### Counts by verdict

| Verdict | Speed review (28) | Structure review (32) |
| --- | --- | --- |
| Confirmed | 15 | 26 |
| Confirmed with corrections | 10 | 4 |
| Understated | 2 | 1 |
| Overstated | 0 | 1 |
| Wrong | 0 | 0 |
| Could not check | 1 | 0 |

No finding of either review is wrong as a whole. Both reviews can be acted on. The corrections below matter most for the changes that touch locking and stored data.

### Statements that are wrong or overstated

- **structure/Q1** (overstated in part). The two "ask for one question" functions are near copies. The three "check" functions are not: `assist.check` has its own task text, schema and criteria, and `pipeline.read_check` has rules the single checks do not have. A shared `verdict()` saves about four lines in each.
- **speed/C1**, the design. The proposed order "book lock, then `Jobs.lock`" is the opposite of what `delete_question` and `restart_question` do today (`Jobs.lock`, then `_edit`). Built as written, it can deadlock.
- **speed/C2**, the risk note. "Start a question over" does not remove attempts; it removes the grade and the card. The alternative "(or the `at` of the last one)" would lose answers after a merge.
- **speed/P1**, the evidence. The Python port of the layout cannot detect the hang (its test is never true in Python) and prints "the layout settled" for the case that hangs. The numbers quoted are samples at turn 80 or 160, not the largest values. The hang itself is real: I ran the page's own code in Node.
- **speed/R1**. "The reader, a new highlight and a question on a highlight are the only callers that need the text" is not so: every Practice card needs the text of its book for the passage (`Jobs.passage`). The proposed test "Practice for two books opens no book" would fail.
- **speed/L1**, the gain. With the proposed limits (64 MB for one member, 1 GB for a book) a hostile EPUB can still hold the app for about 35 seconds per member and about nine minutes per book.
- **speed/T1**, the Java flags. I measured 4.9 s plain and 4.3 s with the flags, not 7.2 s and 4.4 s.
- **structure/T3**. "The tests skip themselves where Java is missing" is true for the tests that use `real_epubcheck`. Three command-line tests fail instead.
- **structure/S1**. The notes-folder part does not use the rest "only through a few methods", and three other parts call into it.
- **Section 4 of the speed review**, "No `GET` does harm". Mostly true. But opening the question editor is a `GET` that writes `state.json` and can undo a "Rebuild files" (verified), and an address of the app's own page makes a model call with no click (verified).

### The most important things both reviews missed

All eight are verified by a script unless marked.

1. **Taking out a question you have answered makes the next answer lose its card** (one tab, ordinary use). Same root as speed/C2, a much more common trigger.
2. **The reader stops saving, silently, once the reading state is larger than 64 KiB.** The page saves with `keepalive: true`, and browsers refuse such a request above 64 KiB. A long book, or typed answers, gets there. The reader is told nothing.
3. **`ebook-processor update` stops the running app before it checks the checkout.** With uncommitted changes, or with no network, the app is stopped and not started again, while the message and the README say nothing was touched.
4. **A third entry of the same long name in one second never gets an id**: the loop runs for ever with `Jobs.lock` held, and the whole app stands still.
5. **A text file of 40 KB can freeze the app** (one heading line with a long run of spaces): a regular expression takes time that grows with the square of the run, and nothing else runs meanwhile. The same pattern is in `vault.py` and `bookfile.py`.
6. **Ctrl-C on the command line does nothing until the model calls that are running have ended.** This is the command-line twin of speed/C4.
7. **The reader stamps answers in UTC, the schedule counts in local days.** In the evening in the Americas a card learned at a checkpoint is not "due the same day", against the README.
8. **Opening the address `/#/notes/ask?q=...` asks the model with no button pressed** (a reload, Back, or a link on any web site).

Also: `tests/prep/fixture/generate.py --check` fails today, and the file it names for the other fixtures (`prepared.py`) does not exist; three tests fail without Java and the README does not say that the tests need Java.

### Overall judgement

**The speed review** is strong where it measured: the numbers I re-ran agree (opening a book 322 ms and 153 ms with the cheap word test; the two-tab loss; the accepted `Host` header). Its findings are real. It is weakest in three places. (1) Two of its scripts do not show what the text says they show (`graph_layout_port.py`, `race_rebuild.py`); the text is right in both cases, but by luck of a second source of evidence. (2) Its fixes for locking and stored data are sketched too lightly: C1 as written can deadlock, and C2's merge does not fit how the page changes an attempt in place. (3) "Risk: none" appears where there is some. Its sizes are a step too small wherever a lock or a stored file is touched (C1, R1, R5, C5). It found the two-tab trigger of the lost cards and stopped there; the same fault has a one-tab trigger.

**The structure review** can be trusted almost as written: its counts and line numbers are right nearly everywhere (I found one line number off by one and one count of 88 that is 91). Its weakness is that it only read. It lists `generate.py --check` as "run by nothing" and did not run it (it fails). It lists the id loop as a harmless repeat and did not see that the loop cannot end. It treats repeated code as a cost of upkeep and does not ask whether the repeated code is correct. Its sizes are fair.

## 2. Verdicts

Each entry: the verdict; what I read or ran; whether lines and numbers are right; whether the suggestion works and what it costs; whether size and risk are fair. Confirmed findings are short.

### 2.1 The speed review

#### speed/R1. Practice, Notes and Process open every book

- **Verdict:** confirmed with corrections.
- **Evidence:** read `server.py:1376-1391`, `:216-293`, `:801-833`, `:926-980`, `reading.py:65-93`, `notes.py:169-173`, `process.py:42-45`, `bookfile.py:77-81`. The lines are right. `notes.notebook`, `process.items` and `bookfile.text` do use only `book.manifest()`. I did not re-measure the 21 s and 688 MB; they agree with `out/library50.txt` of the speed review.
- **Corrections:**
  1. Practice needs a book's text. `practice()` calls `self.passage(job_id, pid)` for the card it shows (`server.py:288`), and `passage()` reads `block.words` of the parsed book (`:309-326`). With a cache of three or four parsed books and cards from many books in one queue (due cards are mixed across books, `:257-268`), Practice would parse a book for almost every card. The fix needs one more step: take the passage text from `questions.json` (it holds it) and keep each block's word range in the manifest file for the highlight marks.
  2. `notes.set_note` at a place also needs the book today (`notes.py:107`, the chapter's word count). The manifest has that number; the code must be changed to use it.
  3. What makes a kept manifest stale. An edit and a rebuild write the prepared EPUB again, so the stamp covers them. A question from a highlight and a change of kind (book or article) are not in `ReadingBook.manifest()`; they are added per request (`server.py:348`, `:187`), so they stay fresh as long as only `book.manifest()` is kept. Three things the review does not name: (a) the places of the passages come from `questions.json`, which `build_outputs` writes about a second after the prepared EPUB (`outputs.py:91` and `:121`); a request between the two would store old places under the new stamp, so the stamp must cover both files or the file must be written under the book's lock. (b) The file needs a format number: `update` can bring a version whose manifest has another shape. (c) `Jobs.book` compares `st_mtime_ns` only (`:1382`); on a file system with whole-second stamps two saves in one second give the same stamp. Suspected, not produced.
  4. The proposed test "Practice for two books opens no book" would fail because of point 1.
- **Size and risk:** medium is low. Three modules take a `ReadingBook` today and would take a manifest. Medium to large; risk medium.

#### speed/R2. The word test in the EPUB engine

- **Verdict:** confirmed.
- **Evidence:** read `prep/words.py:262-285` and `:401-407`: a fresh `tokenize` returns a word exactly when one character is neither in `SEPARATORS` nor in `REMOVED`. I compared the two for every Unicode code point and for 200,000 random texts made of separators, removed characters, dashes, combining marks and controls: no difference (`r2_equiv.py`). I re-ran `try_fast_segment.py`: 16 books identical, 322 ms and 153 ms. The four lines (369, 373, 397, 451) are right.
- **Notes:** `words.py:410` already has `is_separator_only(text)`, which is the same test the other way round and is used only by its own test. Make that one fast and use it; no new name is needed. Do not write the test with `str.strip()` or `\S`: six characters that Python calls white space (U+000B, U+001C to U+001F, U+0085) are words to the engine. A lone surrogate makes `tokenize` raise and the expression answer "word"; an XML parser cannot produce one.
- **Size and risk:** small, low. Fair.

#### speed/R3. The editor and the review open the whole run; a rebuild parses five times

- **Verdict:** confirmed with corrections.
- **Evidence:** read `runner.py:113-129`, `:168-178`, `server.py:397-409`. `change_run` writes `state.json` whatever `build` is (line 175). The times agree with `out/edit.txt`; not re-measured.
- **Correction (understated):** the write by a `GET` is more than wasted work. `Jobs.rebuild` runs outside the lock (speed/C5). Opening the editor of another question within 0.3 s after "Rebuild files" began wrote the old state back in 7 of 10 tries: `state.json` then says the question is kept while the files were built without it (`lost_removal.py`). A `GET` needs no header, so a page of another site can send it.
- **Suggestion:** works. Give reads their own function first; it is the smallest part and closes this window. Handing the parsed original to `validate_bytes` is safe (same bytes, hash checked). Handing over the parsed prepared book means the validator trusts the builder's own parse; say so in a comment.
- **Size and risk:** medium, fair.

#### speed/R4. The library listing reads four files for every book

- **Verdict:** confirmed. `summary` (`server.py:184-194`) loads `reading.json`, then `cards()` loads `srs.json` and `reading.json` again, then `notes.json`; `listing` holds the lock (`:1452`). Not re-measured. A cached summary must still let `cards()` take in new answers when `reading.json` changes; the review's stamp key does that. Small, fair.

#### speed/R5. The notes folder is walked in full

- **Verdict:** confirmed. Read `vault.py:661-666`, `:697-720`, `:1203-1222`, `:1371-1395`. Not re-measured.
- **Cost not stated:** `save` and `put_book` use the full walk also to see other changes made outside (a file added with the same title, a book's file renamed). With a check of one file only, such a change is seen up to a second later. Low. Size: small is low for three methods plus a kept `_joined`; small to medium.

#### speed/R6. Search makes a snippet for every match

- **Verdict:** confirmed. `vault.py:1430` builds the snippet before the sort and the cut at `:1434`. The `titles` list uses the uncut list and needs no snippet, so the change keeps behaviour.

#### speed/R7. Small repeats inside one request

- **Verdict:** confirmed. `_place` (`server.py:858-863`) works out the manifest and loads `notes.json` for each passage; `vault_home` (`:918`) calls `process_view()` for one number.

#### speed/P1. The graph freezes, starts slowly and never rests

- **Verdict:** confirmed with corrections.
- **Evidence:** read `index.html:1177-1249`. I took `step()` and the start positions from the page unchanged and ran them in Node on the real answer of `GET /api/vault/graph` for folders of 300 notes (`graph_step.js`, `out/graph_hub.txt`):

  | Links to one note | Largest coordinate | Result |
  | --- | --- | --- |
  | 90 | 931 | every turn ended |
  | 100 | 6.6 × 10^7 | every turn ended |
  | 115 | 1.5 × 10^17 | every turn ended |
  | 120 | 2.3 × 10^18 | the loop stops ending at turn 41 |
  | 150 | 6.1 × 10^18 | the loop stops ending at turn 28 |

  The limit is 190 × 2^53, about 1.7 × 10^18. The hang comes inside the 120 turns the page runs before its first picture (`index.html:1281`).
- **Corrections:** `graph_layout_port.py` does not prove this. In Python `math.floor` gives an exact integer, so its test `gx + 1 == gx` is never true; it prints "the layout settled" for 120 and 150 links. The numbers in the review (270,000; 4 × 10^17; 9 × 10^26) are what the port printed at turn 80 or 160, after the peak. By the review's own number for 120 links the loop would end (4 × 10^17 / 190 is below 2^53). The Chromium run (`measure_hub.py`) is the review's real evidence.
- **Suggestion:** works. With a limit of 40 pixels on one move and nothing else, the same data gave a largest coordinate of 653 (120 links) and 708 (150 links) (`graph_step_clamped.js`). The limit of 8 ms per frame does not help against the hang, because the 120 first turns are not inside a frame; the limit on a move does. I did not re-measure frame rates or the processor use at rest.
- **Size and risk:** medium, fair.

#### speed/P2. The Process view draws every item again on a click

- **Verdict:** confirmed. `choose()` (`index.html:1358`) calls `draw()`, which builds every row (`:1372-1392`). Not re-measured.

#### speed/P3. One failed poll stops the page

- **Verdict:** confirmed. `refresh` (`index.html:660`) and `poll` (`:1628`) return on an error before the timer is set (`:692`, `:1635`). The lost focus I did not re-run.

#### speed/P4. Pages are sent in full without compression

- **Verdict:** confirmed. `send_page` (`server.py:1929-1939`). The review itself calls the gain small; I agree.

#### speed/C1. The one lock is held across model calls and rebuilds

- **Verdict:** confirmed with corrections. The finding is right; the fix as designed is not safe.
- **Evidence:** read `server.py:358-377`, `:419-439`, `:456-485`, `runner.py:168-178`, `editing.py:203-279`. The lines are right. The 2.9 s wait agrees with `out/edit.txt`.
- **Can a book lock and `Jobs.lock` be taken in opposite orders?** Yes, in the design as written. The review says: in `_edit` hold the book's lock, "and take `Jobs.lock` only for the short update of `job` at the end" (book lock first, then `Jobs.lock`). Today `delete_question` (`:457-459`) and `restart_question` (`:465-473`) take `Jobs.lock` and then call `_edit` (`Jobs.lock` first, then the book lock). One thread in "Save" and one in "Delete" of the same book can then wait for each other for ever. The order must be fixed for all callers: for example always the book lock first, and `delete_question` and `restart_question` changed to follow it.
- **What else assumes the one lock:**
  1. `Jobs.book` parses the prepared EPUB under `Jobs.lock` (`:1383-1390`). Today no edit can be writing that file at the same time, because the edit holds the same lock. With a book lock, a reader's request can open the file while `build_outputs` writes it in place (`outputs.py:91`). So the writes through a temporary file of speed/C5 must come first, or `book()` must take the book lock.
  2. The flag `exports_stale`. `_edit` and `rebuild` clear it at the end (`:375`, `:1901`). `_accept` sets it under `Jobs.lock` (`:631-639`). If a question from a highlight is accepted while the files are being built, the build has already read `notes.json`, and the flag is cleared afterwards: the exports stay old and nothing says so. This race exists today for `Jobs.rebuild`; a book lock in `_edit` adds it there. Clear the flag only when it was not set again (a counter).
  3. `editing.check` changes the state (`q["verdict"]`, `q["checks"]`, `editing.py:219-223`) inside the function that `change_run` runs. To call the model outside the lock it must be cut in two: build the task, and apply the verdict to a state read again. `rewrite` and `choose_passage` only read, so they are easy.
- **Size and risk:** medium is low: medium to large, risk medium. Add a test that "Delete" and "Save" of one book at the same time both finish.

#### speed/C2. Two reader tabs of one book

- **Verdict:** understated.
- **Evidence:** read `reader.html:298-324`, `reading.py:274-353`, `srs.py:184-208`, `server.py:1397-1405`, `:441-454`. I replayed the story on my own data (`two_tabs_rt.py`): after tab A saves, 0 of tab B's 3 attempts are left, the 3 grades are kept, the place and the reading time are A's; two answers given afterwards get no cards (`seen` is 3, the list has 2 entries). The story holds step by step. One detail is wider than the review says: tab A does not have to be closed. Any key or click in tab A makes it save within five seconds (`reader.html:318-323`).
- **What the review does not say:**
  1. **The second half does not need two tabs.** `clean_state` drops every attempt whose question is no longer known (`reading.py:295`; `known` is built at `server.py:1399-1401`). Take out an answered question with "Rebuild files", delete a question you added and answered, or remove an accepted question of a highlight you answered: the stored list becomes shorter than `seen`, and the next answer at a checkpoint gets a grade and no card, for good. Verified (`removed_question.py`); see missed finding M1. The cut to the last 5,000 attempts (`reading.py:294`) has the same effect.
  2. **"Start a question over" does not remove attempts.** `_forget_answers` removes the grade and the card and keeps the attempts ("What was answered stays as history", `server.py:441-454`). The review's risk note says the opposite. A merge of attempts needs no special case for it. What it must not do is take the old attempts of a restarted question in again as new answers.
  3. **An attempt is changed in place.** "I was right" sets `grade` and `overruled` on the attempt that exists (`reader.html:771-776`); the key `(id, at)` stays. A union by `(id, at)` must take the newer copy of the same key, or the overruling is lost. Today, when the answer was taken in before the reader pressed "I was right" (another tab showed the library in between), the grade says correct and the card stays a new card with a miss (verified, `out/overrule.txt`); the README promises the opposite.
  4. **"In time order" breaks counting by position.** A merged attempt with an earlier time is put in before entries already counted. The review's alternative "(or the `at` of the last one)" would skip it. Either keep a set of the `(id, at)` taken in, or keep the stored list append-only (new attempts are added at the end in the order they arrive) and leave `take_in` as it is.
  5. The page sends its whole state on every save. That is also the cause of missed finding M2. A page that sends only what is new since its last save (new attempts, the place, the time added) removes both problems.
- **Size and risk:** medium, fair for the larger design; risk medium, because it changes what `reading.json` and `srs.json` mean. Tests to add: the two-tab case, the removed question, the overruled answer.

#### speed/C3. The reader page has the race the notebook had

- **Verdict:** confirmed. Read `reader.html:1353-1361` and `:1085-1100`: with nothing pending the second answer goes to `asking` and its `questions` are not used. I did not re-run `race_reader_init.py`. The suggested two lines work; add `drawFoot()` after them, or the strip at the bottom shows the new question only after the next move. Small.

#### speed/C4. Stopping the app during a run

- **Verdict:** confirmed with corrections.
- **Evidence:** read `server.py:2445-2452`, `pipeline.py:510`, `llm.py:240-262`. Not re-run.
- **Corrections:** setting the events in `Jobs.cancels` and `Jobs.asks` does not reach the calls made for an edit, a check or the model's help in the notes folder: their backends have no stop event (`server.py:385`, `:1039`). One stop event for the whole app, given to every backend, covers all. Suspected, not run: after `update --force` during a run, the old process is still working (its pool threads go on with the calls of their unit), the new app shows "Press Continue", and pressing it gives two processes that write the same `state.json`. The command line has the same fault in another form: missed finding M6.
- **Size and risk:** small, fair.

#### speed/C5. "Rebuild files" and stale downloads run outside the lock

- **Verdict:** understated.
- **Evidence:** read `server.py:1889-1903`, `:2372-2374`, `outputs.py:73-137`. The lines are right; `exports.write_anki` (`outputs.py:126`) also writes in place.
- **The lost change is real.** The review could not produce it, because its two scripts test the other direction: they rebuild with `removed: []` and ask whether the edit was kept. I took a question out with "Rebuild files" and opened the editor of another question 0 to 0.3 s later: the removal was undone in `state.json` in 7 of 7 tries in that window (`lost_removal.py`). An edit is not needed; opening the editor is enough (speed/R3).
- **A script that says the opposite of the text:** `race_rebuild.py` printed "rounds with a broken or short answer: 6 of 6". Its test compares the size of each download with the size before the rebuild, and a rebuild changes the size. The files were whole; the review's sentence is right and the script's line is wrong.
- **Suggestion:** works, and the temporary-file writes are needed before speed/C1. Size: small to medium (seven places and two routes).

#### speed/C6. Replacing a file on Windows; no flush to disk

- **Verdict:** could not check (no Windows machine here). By reading: `write_json` (`pipeline.py:73-85`) has no `fsync`; `notebook()` and `reading_state()` read outside the lock (`server.py:701`, `:1395`). The suggestion does no harm.

#### speed/S1. The tools are asked before the app listens

- **Verdict:** confirmed (the order, by reading `server.py:2393-2413`). Not timed; the real tools may reach the web.

#### speed/M1. Three digits that begin with 5 make an error "temporary"

- **Verdict:** confirmed with corrections.
- **Evidence:** `llm.py:329`; I ran `_classify` on the two messages (`out/classify.txt`).
- **Correction:** the line below has the same fault and is tested first. `_LOGIN_RE` (`llm.py:330`) has a bare `401`. "Prompt is too long: 240100 tokens" is reported as "Claude Code is not signed in ... sign in with /login". Use `\b401\b`, as the Codex expression at `:513` does.

#### speed/M2. A request the reader waits for uses the limits of a run

- **Verdict:** confirmed by reading (`llm.py:360-362`, `:531-533`, `server.py:379-387`, `:1033-1043`, `:574-575`). Not measured.

#### speed/M3. One long document is drafted one call at a time

- **Verdict:** confirmed by reading (`pipeline.py:604-628`, `:718-757`). The gain is the review's estimate. Large, and rightly last.

#### speed/L1. Nothing limits what a file unpacks to; a negative `Content-Length`

- **Verdict:** confirmed with corrections.
- **Evidence:** read `epubzip.py:138-155`, `fetch.py:83-95`, `from_docx.py:43-62`, `server.py:1957-1964`. `int("-1")` passes the test at `:1962` and `rfile.read(-1)` reads to the end. I did not re-run the bombs; `out/hostile.txt` shows 222 s.
- **Correction:** the time of the EPUB bomb is not spent unpacking; it is the word pass over 400 MB of text, about 0.55 s per MB. With a limit of 64 MB for one member that is still 35 s, and with 1 GB for a book about nine minutes. A chapter is a few hundred KB in a real book. Give text members (XHTML, the package file) a much lower limit than pictures, for example 8 MB, and refuse on the size the archive's directory states before unpacking; the limited unpacking then catches a directory that lies.
- **Size and risk:** small, fair.

#### speed/L2. Held files and the timeline's day

- **Verdict:** confirmed by reading (`server.py:1614`, `:1660-1676`, `:1197-1236`).

#### speed/I1. The `Host` header is not checked

- **Verdict:** confirmed with corrections.
- **Evidence:** `PUT /api/settings` and `GET /api/jobs` with `Host: evil.example:8765` were both answered 200 (`probes.py`). `dispatch` (`server.py:1986-1999`) reads no `Host`. The attack in a browser was not carried out by either of us.
- **Corrections to the fix:** compare the host name only, not the port. A port check adds nothing against this attack (the attacker uses the app's port anyway) and it breaks an SSH tunnel or a container that maps another port. With `--host 0.0.0.0` the `Host` is never `0.0.0.0`; the check must then be off, and the help text must say so. `update` calls the app at `127.0.0.1` (`update.py:132-137`), which passes.
- **Size and risk:** small, fair.

#### speed/I2. Pictures of a web page are fetched one by one

- **Verdict:** confirmed. Read `pack.py:60-98` (line 74 is right), `fetch.py:39-52`, `:75-107`. `is_private("100.64.1.1")` is false here; `is_global` is false for it, so the suggested test works. Times not re-run.

#### speed/I3. A deeply nested chapter ends in "Unexpected error"

- **Verdict:** confirmed with corrections. The catch in `load_book_bytes` covers EPUBs only. A Markdown file with a list nested 600 deep also ends in `RecursionError` and a 500 (`md_probe.py`). Catch it in `sources.convert` too, or once in the two routes.

#### speed/I4. One file that cannot be read breaks the library

- **Verdict:** confirmed by reading (`server.py:163-172`: `job.get` on a list, `job["status"]` on an object without it; `:192-193`: `h["note"]`).

#### speed/T1. Twenty-one tests are 58% of the time

- **Verdict:** confirmed with corrections.
- **Evidence:** `out/pytest-nonbrowser.txt`: 856 passed, 1 skipped, 219.7 s. I did not run the whole suite again.
- **Corrections:** the Java flags did not give the gain stated. Re-running `epubcheck_flags.py` here gave 4.89 s plain and 4.26 s with the flags (the review: 7.2 s and 4.4 s). Three of the 21 tests do not skip without Java; they fail (see structure/T3). Reports kept by the hash of the file would be kept by the app's own `run_epubcheck`, so the cache is a change to the app, not only to the tests.

### 2.2 The structure review

#### structure/S1. `Jobs` does about twelve jobs

- **Verdict:** confirmed with corrections.
- **Evidence:** counted with `ast`: lines 143-1903 (1,761 lines), 118 methods. The section comments are as described.
- **Corrections:** the notes-folder part needs more of the rest than the six methods named: `self.jobs` itself (`file_books`, `timeline`, `process_view` walk it), `ui_settings`, `type_of`, `prepared`, `lock`, `filed`, the notes' cards in `srs`, and the backend helpers. And the rest calls into it: `notes_do` and `set_type` call `file_book`, `delete` calls `_book_left`, `detail` calls `process_waiting`, `practice` and `notes_practice` call `vault`. The parts form a ring, so each call back must go through the thin class. The split is sound; "start with `notes_folder.py`" is still the right start.
- **Order:** do the locking changes of the speed review (C5, C1, R1) before this split. The review's own risk note says the split must not change which lock is held where; changing the locks afterwards would touch every moved method a second time, in five files.
- **Size and risk:** large, medium. Fair.

#### structure/S2. `Handler.route`

- **Verdict:** confirmed. 361 lines (2015-2375), 92 cases (counted). The repeats and the private calls are as listed.
- **The note that was passed on:** `PUT /api/practice/settings` does write `srs-settings.json` in the route, outside `Jobs` and its lock (`server.py:2123-2126`). I looked for harm and found none: `write_json` uses its own temporary file for each call, so two saves at once cannot clash; a reader of the file sees the old or the new file whole; no other code reads, changes and writes this file. It is a matter of order, not a fault. `Jobs.save_srs_settings` under the lock is still the right place, so that every write is found in one class.
- **Size and risk:** medium, low. Fair.

#### structure/S3. "Make a backend and check its tools" six times

- **Verdict:** confirmed. Read the four places in `server.py`, `runner.py:62-65`, `cli.py:271-272`. This is also the place for speed/M2, as the speed review says, and for one stop event for the app (speed/C4).

#### structure/S4. "Where can this passage be opened" in five places

- **Verdict:** confirmed. The five loops and the two key names (`book_id` in two, `id` in three) are as described.

#### structure/S5. Small leftovers in `server.py`

- **Verdict:** confirmed with corrections.
- **Evidence:** `vault/tags` has no caller in any page, script or test (searched). The unused parameters are unused. `vault._taken` is called at line 1340, not 1341.
- **Correction:** the id loop (`server.py:1428-1430` and `:1751-1753`) is listed as a repeat. It is also a fault: with a name of 40 characters or more the loop cannot make more than one other id, and then never ends, with `Jobs.lock` held. Verified; see missed finding M4. `Jobs._new_id(stem)` is the right place to fix it.

#### structure/Q1. Asking and checking are each written three times

- **Verdict:** overstated in part.
- **Evidence:** read `highlight_questions.py:282-327`, `assist.py:297-343`, `editing.py:203-263`, `pipeline.py:766-801` and `:896-940`.
- **What holds:** `hq.propose` and `assist.propose` are near copies (the loop, both error texts). They differ in what they return (`hq` adds `form`) and in the instructions. `editing.rewrite` has the same loop with another feedback text ("PROBLEMS", a list). One `ask_with_retry` can serve all three if the feedback text is an argument. The six literal texts are where the review says.
- **What does not hold:** "all three build the task, call the checker, read the criteria" is true only in outline.
  - `editing.check` and `hq.check` do share the call (same task builder, schema and instructions). They differ on purpose in two things: `editing.check` writes into the run's state and raises when there is no verdict (the reader pressed a button and sees the error); `hq.check` returns "not passed" (it runs in the background for "accept without asking", where the question is then held back with a reason).
  - `assist.check` is another check: its own task text, its own schema (a flat object, no `prompts` list), its own instructions and four other criteria (`assist.py:53`). Only the last three lines are the same.
  - `pipeline.read_check` refuses a criterion that is not true or false, checks the citations, and wants a citation for a pass. The single checks read anything that is not `true` as false. One `verdict()` for all four would either loosen the run's check or tighten the others.
- **Order with speed/C1:** C1 must cut `editing.check` into "build the task" and "apply the verdict". Do that cut first; the shared helper then falls out of it.
- **Size and risk:** medium, medium. Fair for the asking half; the checking half gains little.

#### structure/Q2. One field has two names

- **Verdict:** confirmed (I checked `forms.js:47`, `runner.py:191`, `editing.py:104`, `hq.py:192`, `:206`, `:316`, `reader.html:1044` and the four tests of `job["kind"]`; not all ten places).

#### structure/Q3. `editing.py` repeats rules of `pipeline.py`

- **Verdict:** confirmed (`editing.py:105`, `:166-174`, `:245-246`; `pipeline.py:56`).

#### structure/Q4. The sixth criterion has no wording

- **Verdict:** confirmed by reading (`outputs.py:32-38`, `:273`, `index.html:366-372`, `:1894`, `steer.py:193`). Like the review, I did not build a report to see the raw name.

#### structure/Q5. Three rules live in Python and JavaScript

- **Verdict:** confirmed. `forms.py:197` has the case, `forms.js:53-56` does not. The page uses `plain(q).answer` in the notebook (`index.html:808`, `:861`) and on a note's page (`notes.js:492`), so the doubled word shows there.

#### structure/N1. `vault.py` is three layers

- **Verdict:** confirmed (`from_markdown.py:13` and `bookfile.py` import `vault`).

#### structure/N2. Links and patterns stated many times

- **Verdict:** confirmed (checked `LINK_RE`, `WIKI`, the block id and heading patterns, `vault._words`). One more reason to keep the heading pattern once: its three copies (`vault.py:350`, `bookfile.py:66`, `from_text.py:20`) all have the slow form of missed finding M5.

#### structure/N3. `put_book` takes a function where a text would do

- **Verdict:** confirmed. `bookfile.text` never reads `name`. The callers to change are 2 in the server and 15 in the tests (`put_book`: `test_vault.py` 11 and `test_assist.py` 1, which the review does not name; `bookfile.text`: `test_bookfile.py` 3). One test uses the name inside its text (`test_vault.py:363`) and needs another way to make two texts differ.

#### structure/P1. Five copies of the element builder and the API call

- **Verdict:** confirmed. I read the ten functions; the table is right. The shared `api` is also where a common answer to a failed request belongs (speed/P3, missed finding M2).

#### structure/P2. The script of `index.html` holds thirteen views

- **Verdict:** confirmed. Thirteen `show*` functions at the lines given. I count 91 `style:` strings, not 88.

#### structure/P3. The two pages carry the same base styles

- **Verdict:** confirmed (`index.html:9-12` and `reader.html:8-11` are the same lines).

#### structure/P4. A highlight's questions are drawn twice

- **Verdict:** confirmed (I read the reader's side in full, `reader.html:1024-1150`, and the notebook's side in outline). The shared part should carry the notebook's fixed way of loading (speed/C3).

#### structure/P5. Lists that the page and the server must both know

- **Verdict:** confirmed. `editor/src/index.js:362` has no `article`. I built the editor in a copy (`npm run build` with the modules already there): the result is byte for byte the committed `editor.js`. So the gap is in the source, and the committed file is not stale.

#### structure/P6. `Notes.mount` is one function of 475 lines

- **Verdict:** confirmed (lines 300-774).

#### structure/C1. `pipeline.py` is also the tool box

- **Verdict:** confirmed. The five loaders and the imports are as listed (`exports.py:16` also takes `now` from `pipeline`). No test replaces `write_json`, so moving it is safe. This is the natural first step for speed/C5, C6 and R4, which all change how files are written and read.

#### structure/C2. Two imports that point the wrong way

- **Verdict:** confirmed by reading. Whether a cycle hides behind an import inside a function, neither of us tried.

#### structure/C3. Private names used from outside

- **Verdict:** confirmed (checked `pipeline.py:41`, `assist.py:294` and `:314`, `server.py:260`).

#### structure/C4. Dead code and unused parameters

- **Verdict:** confirmed with corrections. The names I checked are unused as said. The list is not complete: `words.is_separator_only` (`prep/words.py:410`) is used only by its own test and is not listed. It is the function speed/R2 needs.

#### structure/C5. Small repeats inside single modules

- **Verdict:** confirmed for the ones I read (`cli.py:324-325`, `:334`, `:424`; `prompts.py`, `highlight_questions.py:268`). I did not read `visible.py`.

#### structure/T1. The app fixture is written eleven times

- **Verdict:** confirmed (eleven `app` fixtures and the browser one; thirteen sleeps of 0.05 s).

#### structure/T2. Test files import from each other

- **Verdict:** confirmed (15 files import from `tests.test_pipeline`, 4 import `PREPARED`).

#### structure/T3. Twenty-one tests take 60% of the time, and they can reach the web

- **Verdict:** confirmed with corrections.
- **The note that was passed on, settled:** yes. `real_epubcheck` calls `run_epubcheck(path)` (`tests/prep/prepkit.py:55-59`), whose default is `download=True`; `ensure_epubcheck` then fetches the release from github.com when the jar is not in the cache (`prep/epubcheck.py:104-131`). The guard in `tests/conftest.py` replaces `sources.fetch.check` only. The CI workflow keeps `pip`'s cache and not this one, so each of the four test jobs downloads EPUBCheck on every push. When the download fails, the tests skip and the run is green without them. On this machine the jar is in the cache, so nothing was fetched.
- **Corrections:**
  1. Not all of these tests skip without Java. The `test_cli_*` tests run `python -m ebook_processor.prep validate` in another process (`prepkit.py:103`), where neither the stand-in nor the guard applies, and "an unchecked file is not a pass". With Java taken off the path, three tests fail: `test_cli_passes_on_the_fixture_and_writes_json`, `test_cli_work_dir_supplies_the_draft`, `test_cli_timestamp_from_source_date_epoch`. The README says `pip install -e '.[test]' && pytest -q` and does not name Java.
  2. So `real_epubcheck(download=False)` is not enough. The command needs a switch as well (an environment variable read by `run_epubcheck`, or `--no-epubcheck` for `prep validate`), and the docstring of `conftest.py` should say what the guard covers.
- **Size and risk:** small, low. Fair.

#### structure/T4. What has no test of its own

- **Verdict:** confirmed. No Python test names `/cancel`, `vault/cite`, `vault/tags` or `GET vault/projects` (searched).

#### structure/T5. Three things the checks do not check

- **Verdict:** understated.
- **Evidence:** I ran `python3 tests/prep/fixture/generate.py --check`. It fails today: "2 of 4 files are stale". `tests/data/books/fixture.expected.json` differs from what the generator writes, and `tests/data/books/README.md` is missing. The difference is only in three path texts (`tools/prep/...` in the committed file, `tests/prep/...` from the generator; neither path exists). `fixture.epub` is identical. The generator's text also names `tests/prep/fixture/prepared.py` for the prepared book and the 13 broken books; that file is not in the repository, so those fixtures cannot be made again here.
- **Consequence for the suggestion:** "add a test that runs the generator's check" would fail on its first run. Settle the path texts first (structure/D2), then add the test. For the editor: the committed file is in step today (structure/P5).

#### structure/D1. The README's module table leaves out seven modules

- **Verdict:** confirmed.

#### structure/D2. The engine's docstrings name files that are not here

- **Verdict:** confirmed (I looked for `prep/README.md`, `docs/PLAN.md`, `docs/spikes/` and the test named in `entities.py:9`).

#### structure/D3. Docstrings that no longer describe the code

- **Verdict:** confirmed. The version change works because `ebook_processor/__init__.py` holds the version as a plain text.

## 3. Where the two reviews meet

**The same finding twice.**

- structure/T3 and speed/T1 (the EPUBCheck tests). They agree; the numbers differ a little (129 of 210 s; 128 of 220 s). Do one change: the marker, no download in tests, and the switch for the command (see structure/T3).
- structure/S3 and speed/M2 (one function that makes a backend; shorter limits for a request the reader waits for). Do S3 first; M2 and the app-wide stop event of speed/C4 are then one place each.

**One prepares the other.**

- structure/C1 (a module `store.py` with `write_json` and one loader) before speed/C5 (write every output through a temporary file), speed/C6 (retry and flush) and speed/R4 (summaries kept by stamp). All four change how files are written and read; with the module they are one place.
- speed/R3 (reads of a run get their own function and do not write) before speed/C1. It is the same cut as "read under the book's lock", and it closes the window in which a `GET` undoes a rebuild.
- speed/C5 (writes through a temporary file, `Jobs.rebuild` under a lock) before speed/C1 (a lock per book). Without C5, C1 lets the reader open a half-written book.
- speed/C1's cut of `editing.check` before structure/Q1's shared helper.
- structure/P1 (one `api` for all pages) before speed/P3 (try again after a failed poll) and before a notice for a failed save (M2).
- speed/C3 (two lines in the reader) now; structure/P4 (one shared card for a highlight's questions) later, built on the notebook's fixed way.

**They pull against each other.**

- structure/S1 (split `Jobs` into five files) against speed/C1, C5 and R1 (change the locks, the cache of books and the manifest inside `Jobs`). Do the speed changes first, in the one file, with the lock rules written down at the top. Then split. The other order moves about 1,700 lines and then changes the locking in five files.
- structure/P2 (split the script of `index.html` by view) against speed/P1 (the graph) and speed/P2 (the Process view). Both rewrite the inside of a view. Fix the two views first (the graph hang is a fault a reader meets), then move them; or move exactly these two views first. Not both at once on two branches: the lines are 150 to 400 characters long and will not merge.
- structure/C3, C4 and D2 leave open whether `prep/` stays "as ported". speed/R2 and speed/L1 change `prep/words.py`, `prep/epub.py` and `prep/epubzip.py`. The owner's decision on `prep/` should come before R2 and L1, or these are the first edits that make it differ.
- structure/S5 would add `Jobs._questions_doc()` for the two places that parse `questions.json`; speed/R1 replaces one of them (`sources`) by the kept manifest. Do R1 first.
- structure/N1 (move the properties reader out of `vault.py`) and speed/R5 (rewrite the walk in `vault.py`) touch different parts of one file. Either order; not at the same time.

**Suggested order of the first steps:** the small faults a reader can meet (M1 to M5 below, speed/M1, I1, L1's negative length, C3, structure/Q4, Q5, P5); then structure/C1 with speed/C5 and C6; speed/R3; speed/C1; speed/C2 together with M1 and M2; speed/R1 and R2; then the splits (structure/S2, S1, P1, P2).

## 4. What both reviews missed

Each finding: where, what happens, evidence, suggestion, size, risk and tests, and whether it is verified.

### M1. Taking out a question you have answered makes later answers lose their cards

- **Where:** `reading.py:294-301` (`clean_state` keeps only attempts whose id is known), `server.py:1399-1403` (`known`), `srs.py:193` and `:207` (`take_in` counts by position).
- **What happens:** `srs.json` remembers how many attempts it has taken in. When a question leaves the book, the reader's next save drops that question's attempts from `reading.json`, so the list becomes shorter than the count. The next answer at a checkpoint is stored with its grade, and gets no card: it never comes to Practice. One answer is lost for each attempt that was dropped. Three ordinary actions do it: untick an answered question and press "Rebuild files"; delete a question you added and answered; remove an accepted question of a highlight that you answered at its checkpoint.
- **Evidence:** `removed_question.py`. Three answers, three cards, `seen` 3. The first question is taken out. The next answer (c0004-p1, Correct) has a grade and no card; Practice counts 2 cards. One more answer gets its card; c0004-p1 never does.
- **Suggestion:** in `take_in`, remember the attempts taken in by `(id, at)`, not by number. Or keep the stored list append-only and never drop an attempt for an id that is gone (filter when reading, not when writing). Do it with speed/C2.
- **Size:** small alone, medium with C2. **Risk:** low to medium (`srs.json` gains a member; old files must still load). **Tests:** `tests/test_srs.py`, `tests/test_reading.py`; add this case.
- **Verified.**

### M2. The reader's saves fail without a word once the state is larger than 64 KiB

- **Where:** `web/static/reader.html:298-304` (`save`: `fetch` with `keepalive: true`, the whole state as the body, `.catch(() => {})`). `notes.js:86` knows the limit and switches `keepalive` off above 60,000 characters; the reader does not.
- **What happens:** a browser refuses a `keepalive` request with a body above 64 KiB. Every save of the reader is such a request, and the body is the whole reading state: every attempt with what was typed (up to 4,000 characters each), every grade, every preview stamp. An answered question costs about 200 bytes without typed text and more with it. A book of 350 questions passes the limit near its end; a reader who types recalls passes it after 100 to 200 questions. From then on no save arrives: not the place, not the time, not a grade. The error is swallowed. The reader sees nothing until the page is opened again at the old place. A failed save for any other reason (the app was stopped) is swallowed the same way.
- **Evidence:** `big_state.py`, in Chromium. With a state of 45 KB, a change of view was saved. With 89 KB (82 typed answers of 1,000 characters), the page held `rsvp`, the app still held `scroll` after 2.5 s, and the page showed no message. I did not test Firefox or WebKit; the limit is in the Fetch standard.
- **Suggestion:** use `keepalive` only for the save on `pagehide`, and only when the body is small. Better: send only what is new since the last save (see speed/C2, point 5). Show a line when a save has failed twice ("Your place is not being saved") and try again.
- **Size:** small for the first two; medium with the smaller body. **Risk:** low. **Tests:** `tests/browser/test_reading.py`; add a state above 64 KiB.
- **Verified** in Chromium.

### M3. `ebook-processor update` stops the app and then finds that it cannot update

- **Where:** `update.py:263-275`: the running apps are stopped (`:269-273`) before `pull()` (`:275`) checks for uncommitted changes, for a branch that cannot be left, and before it reaches the remote.
- **What happens:** with an uncommitted change in the checkout, or with no network, the command stops the app, prints the error and ends. The app is not started again. The message says "nothing was changed" and the README says "otherwise nothing is touched".
- **Evidence:** `update_dirty.py`, with a scratch checkout and a scratch app. Uncommitted change: "Stopping the app ... Fetching ...", then the error; the port no longer answers; nothing was started. Remote not reachable: the same.
- **Suggestion:** do the checks and the `git fetch` first; stop the app only when a fast-forward is ready. If a later step fails, start the app again as it was.
- **Size:** small. **Risk:** low. **Tests:** `tests/test_update.py`; add "a refused update leaves the app running".
- **Verified.**

### M4. The loop that makes a new entry's id cannot end for a long name

- **Where:** `server.py:1428-1430` (`import_prepared`) and `:1751-1753` (`create`): `while job_id in self.jobs: job_id = job_id[:55] + "x"`, inside `with self.lock`.
- **What happens:** an id is the date, the time to the second, and up to 40 characters of the name: 56 characters at most. For a full-length name the loop replaces the last character by `x` and can make only one other id. The third entry of the same name in one second, or the second when the 40th character is already `x`, loops for ever with `Jobs.lock` held. Every request that needs the lock waits; the app must be killed.
- **Evidence:** `job_id_loop.py`: two entries were added, the third call was still running after 8 s, and another thread could not take `Jobs.lock`.
- **How likely:** low. It needs a long title (articles have them) and three adds in one second: a script, or a button pressed three times while the first request is on its way. The harm is a frozen app.
- **Suggestion:** one `_new_id(stem)` (structure/S5) that shortens the name and adds a counter (`-2`, `-3`), or a few random characters.
- **Size:** small. **Risk:** low; the id must still match `JOB_ID_RE`. **Tests:** `tests/test_visible_and_web.py`; add three imports of one name with a fixed clock.
- **Verified.**

### M5. Small hostile text files that freeze or stall the app

- **Where:** `sources/from_text.py:20` (`MARK`: `^(#{1,6})\s+(.+?)\s*#*$`); the same form in `vault.py:350` (`HEADING_RE`) and `bookfile.py:66`. Also `python-markdown` (`sources/from_markdown.py:59-60`) and the HTML parser (`sources/clean.py:57`, called four times for one file: `facts`, `is_page`, `signs`, `clean`).
- **What happens:**
  1. *A text file.* A first line `# a`, a long run of spaces, then `b`. The expression tries the rest of the run again from every place: the time grows with the square of the run. 20,000 spaces: 3.8 s for one match; 40,000 (a file of 42 KB): 13.7 s for `from_text.read`. One match is one call that does not let other threads run, so the whole app stands still for that time, not only this request: in my test a loop that should run twice a second ran twice in 7.8 s. 200,000 spaces would take minutes; a text page of 10 MB from a web address (the limit of `fetch`) would not end. The same pattern reads every line of every note in the notes folder.
  2. *A Markdown file* of 14 KB that is 10,000 `[` characters takes 16.7 s (4.4 s for 5,000). A list nested 600 deep ends in `RecursionError` and a 500 (see speed/I3).
  3. *An HTML file* with one element that has 40,000 attributes (343 KB) takes 12 s for one parse and 80,000 take 61 s; the file is parsed four times.
  4. Two small ones: `<meta charset="undefined">` in an HTML file gives `500 Unexpected error: UnicodeError('undefined encoding')` (`sources/chars.py:24` catches `LookupError` and `UnicodeDecodeError` only). A note title of 120 characters that are not Latin (emoji, Chinese) passes the limit of 120 characters and fails the file system's 255 bytes: `500 OSError: File name too long`.
- **Evidence:** `text_regex.py`, `md_probe.py`, `html_stage.py`, `probes.py` and their output files.
- **How it could be reached:** as for speed/L1: the reader adds a hostile file, or an address whose page is one. In practice low; the harm is an app that stands still.
- **Suggestion:** write the heading pattern without the lazy group: match `^(#{1,6})[ \t]+(.*)$` and strip the closing marks and spaces from the group in Python. Keep it in one place (structure/N2). Give `convert` a limit on the length of one line of a text file. Parse HTML once and hand the tree on. Catch `UnicodeError` in `decode`. Count a title's bytes.
- **Size:** small. **Risk:** low; `tests/sources/test_convert.py` and `tests/test_vault.py` cover headings. **Tests to add:** a heading line with 50,000 spaces must be read in under a second.
- **Verified** (the 10 MB case is worked out from the measured growth).

### M6. Ctrl-C on the command line waits for the model calls that are running

- **Where:** `pipeline.py:498-520` (`parallel`: `with ThreadPoolExecutor(...)`), `cli.py:404-407` (`except KeyboardInterrupt: cancel.set()`).
- **What happens:** Ctrl-C raises in the main thread, inside the `with` block. Leaving the block waits for the calls that are running. Only after that does `cmd_process` set the stop event that would have ended them. The model processes do not get the signal themselves: each is in a process group of its own (`llm.py:245-250`). So "Stopped." comes when the slowest running call has ended: seconds to minutes with a real model, and up to the 30-minute limit when a call hangs. Nothing is printed meanwhile.
- **Evidence:** `cli_ctrl_c_child.py` with a stand-in whose calls take 8 s and stop at once when the event is set: Ctrl-C after 2.0 s, "Stopped." after 8.3 s.
- **Suggestion:** in `cmd_process`, set `cancel` from a handler for the signal; or catch `BaseException` inside `parallel`, set `self.cancel`, and raise again.
- **Size:** small. **Risk:** low. **Tests:** `tests/test_recovery.py`; add this case with a stand-in program.
- **Verified.** speed/C4 has the same fault for `serve`.

### M7. Answers are stamped in UTC, the schedule counts in local days

- **Where:** `web/static/reader.html:247` (`stamp = () => new Date().toISOString().slice(0, 19)`), `srs.py:198-201` (`take_in` takes the day from the stamp), `server.py:198` (`today = date.today`, local) and `:1366-1367` (a Practice answer is stamped in local time, with a comment that says why).
- **What happens:** west of UTC in the evening, the stamp of a checkpoint answer is already tomorrow. The card becomes "due tomorrow, last answered tomorrow". Today Practice does not offer it; the README says a card "is due for its first review the same day". East of UTC in the morning the stamp is yesterday, and the card is at once an overdue card.
- **Evidence:** `utc_day.py`: the computer's day is 22 November, the answer is stamped `2026-11-23T01:30:00`. The card: due 23 November. Practice on the 22nd: nothing offered. On the 23rd it comes as a "first review of a card learned today".
- **Suggestion:** stamp in local time in the page, as `practice_answer` does; or let the server set the day when it takes the answer in.
- **Size:** small. **Risk:** low to medium: old stamps stay as they are; the notebook shows these times. **Tests:** `tests/test_srs.py`.
- **Verified.**

### M8. The address `/#/notes/ask?q=...` asks the model without a click

- **Where:** `web/static/index.html:1158` (`if (box.value.trim()) ask(); else box.focus();`), and `:1134`, where the question is written into the address after each ask.
- **What happens:** the Ask page sends its question as soon as it opens with a `q` in the address. A reload of the page, Back, or a link on any web site to `http://localhost:8765/#/notes/ask?q=...` makes a model call on the reader's plan, with the notes and highlights that match the question. The README says the model is asked "only when you press its button".
- **Evidence:** `ask_link.py` in Chromium, with a stand-in that counts: opening the address made one call, reloading made a second; no button was pressed.
- **Suggestion:** fill the box from the address and wait for the button. The link "Ask" on the Notes page, which passes the search words, can carry a mark that lives only in memory.
- **Size:** small. **Risk:** low. **Tests:** `tests/browser/test_knowledge.py`.
- **Verified.**

### M9. Smaller points

- **A `GET` from another site.** `GET /api/vault` makes the notes folder and writes a file for every book (verified in `probes.py`: the folder and one file were made by a request without the app's header). `GET /api/jobs/<id>/book-questions/<qid>` writes `state.json` (speed/R3). With speed/R1 unfixed, one such request also costs tens of seconds under the lock. Suggestion: make the folder on a `POST` from the Notes page.
- **A rebuild that fails half way.** `change_run` writes `state.json` before it builds (`runner.py:175-177`). If the build raises (a full disk, or `ValueError` from `build_outputs` at `outputs.py:76`, which `_edit` does not catch), the state is changed and the files are not, and the page gets a 500. And when the firmware-format book cannot be built (`outputs.py:84-89`, for example a question added to a book that is at the limit of 1,024), the prepared EPUB is deleted and the book can no longer be read in the app. Suspected from reading; not run.
- **Path checks and malformed requests** (section 4 of the speed review): I tried 12 paths against the download, picture, page and static routes and 22 malformed requests against other routes (`probes.py`, `out/vault_probes.txt`). All were refused with a 400 or 404, except the two 500s named under M5. The claim holds.
- **"Every store of a book is read, changed and written under `Jobs.lock` in one step":** it holds for `reading.json`, `notes.json`, `srs.json`, `process.json` and `job.json` in every place I read. The exceptions are `state.json` (speed/C5) and `srs-settings.json` (structure/S2), which the reviews name.

## 5. What I did not check, and why

- **Windows and macOS.** speed/C6 stays unchecked. M2 was tested in Chromium only.
- **The large measurements of the speed review** (50 and 500 books, 10,000 notes, frame rates, the bombs of speed/L1, the pictures of speed/I2, Ctrl-C during `serve`). I compared the text with the output files in `review-speed/out/` and re-ran only what a verdict needed: `try_fast_segment.py`, `graph_layout_port.py`, `epubcheck_flags.py`. Re-running `epubcheck_flags.py` wrote `review-speed/out/ec.json` again (the script writes there); I wrote nothing else into the other reviewers' folders. I read `review-speed/lib50-seed` and copied it into my own folder for one script.
- **The whole test suite.** I ran single files and the EPUBCheck tests without Java. I relied on the speed review's run for the total.
- **The browser scripts of the speed review** (`race_reader_init.py`, `measure_hub.py`, `measure_pages.py`) were not re-run. speed/C3 is confirmed from the code; the graph hang from the page's code in Node.
- **`prep/` logic.** I read `words.py`, the parts of `epub.py` that speed/R2 touches and `epubzip.py`. I did not review `build.py`, `validate.py`, `draft.py`, `checkpoints.py` or `matter.py`.
- **`index.html` lines 974-1514.** I read the Notes front page, Ask, the graph and the first half of the Process view line by line, and Revisit, the timeline, a note's page and the trash not at all.
- **Unused CSS.** A search by word found no class or id in either style sheet that no markup and no script names. That cannot prove that a short name (`m`, `in`, `one`) is used as a class; a browser's coverage tool would.
- **`forms.js` grading** (how a typed answer is matched), `steer.py`, `prompts.py`, the instruction files, `visible.py`, `exports.py`, `content.py` and `settings.js` were not read.
- **`from_docx.py` with a hostile Word file** and `trafilatura` with a hostile page: not tried. A `.mhtml` with 2,000 nested parts was refused cleanly; I made no other hostile `.mhtml`.
- **Real tools and the web:** nothing that needs Claude Code, Codex or a real address was run.
- **The README** was compared with the code for the update command, the tests, Practice, the limits of articles (150 words; three chapters and 20,000 words) and the model's help. The rest was not compared line by line.
