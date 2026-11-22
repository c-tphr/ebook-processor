# Plan: what to do about the three reviews

Written on 2026-11-22. The code is commit `4557303` (the same on `main` and on `claude/dreamy-allen-6un3tt`). No code has been changed yet. This plan waits for the owner's word.

Three reviews stand beside this file:

- `REVIEW-structure.md`: 32 findings on structure, repeats, tests and documents.
- `REVIEW-speed-robustness.md`: 28 findings on speed, memory, locks, limits and hostile input.
- `REVIEW-redteam.md`: a verdict on each of those 60 findings, and 9 findings both had missed.

Findings are named with their review: `structure/S1`, `speed/C1`, `redteam/M1`. (The reviews reuse letters, so the bare id is not enough.)

Sizes mean: small, under an hour; medium, a few hours; large, a day or more.

## 1. The result in short

- No finding of the first two reviews is wrong as a whole. Of 60, 41 hold as written, 14 hold with corrections, 3 are worse than written, 1 is overstated in part, and 1 could not be checked (it needs Windows).
- The red team found 9 more. I checked each of them, and they hold. One that the red team marked "suspected" I produced: adding a question to a section that already holds 32 deletes the book's file, and the book can no longer be opened (item 1.3 below).
- I found one thing no review has: the heading pattern of the notes folder is much slower than the red team's number for the same pattern in text files. One heading line with 2,000 spaces takes 11 seconds, and nothing else runs meanwhile (item 1.5).
- The work falls into six stages. Stage 1 is 17 faults a reader can meet, almost all small. Stages 2 and 3 change how files are written and how the server locks; they need care and come in a fixed order. Stages 4 to 6 are the pages, the structure of the code, and the checks and documents.
- Four decisions are the owner's (section 4). Each has my recommendation, and none blocks stage 1.

## 2. How the findings were checked

I read all three reviews in full. Then I went to the code for every finding where a mistake would cost most, for every point on which the reviews disagree, and for every finding of the red team, since nobody had checked those.

**Run by me:**

| Finding | What I ran | Result |
| --- | --- | --- |
| `redteam/M5` (slow heading pattern) | `MARK` of `sources/from_text.py:20` and `HEADING_RE` of `vault.py:350` on a line `# a`, n spaces, `b` | `MARK`: 0.009 s at 1,000 spaces, 0.056 s at 2,000 (grows with the square). `HEADING_RE`: 1.5 s at 1,000, 11.0 s at 2,000 (grows with the cube). The red team measured `MARK` only and wrote "the same pattern" for the other two. |
| `redteam/M9`, second point (a failed build) | A scratch app; 32 questions added to one section that had one | The add that makes 33 is answered `404 this book has no file to read yet`. `state.json` holds the 33rd question. `fixture.prepared.epub` is gone. The reader answers 404 for the book. |
| `structure/T3` as corrected by the red team | Three `test_cli_*` tests of `tests/prep/test_validate.py` with no Java on the path | All three fail; they do not skip. |
| `structure/T5` as corrected | `python3 tests/prep/fixture/generate.py --check` | "2 of 4 files are stale". |

**Read by me in the code, and the review's account holds:**

- `speed/C1` and the red team's correction: `delete_question` and `restart_question` take `Jobs.lock` and then call `_edit` (`web/server.py:457-473`). A book lock taken inside `_edit` before `Jobs.lock` would be the opposite order.
- `speed/C2` and `redteam/M1`: `clean_state` keeps only attempts whose question is known (`reading.py:294-301`); `take_in` counts by position (`srs.py:193`, `:207`). A shorter list makes later answers invisible to Practice. `_forget_answers` keeps the attempts, as the red team says (`web/server.py:441-454`).
- `speed/R1` and its correction: `Jobs.passage` reads the words of the parsed book for every card (`web/server.py:296-328`).
- `speed/R3` and `speed/C5`: `change_run` writes `state.json` whatever was changed (`runner.py:175`); `Jobs.rebuild` calls `rebuild` with no lock held (`web/server.py:1896`).
- `speed/C3`, `speed/M1` (and the bare `401` beside it, `llm.py:329-330`), `speed/I1`, the negative `Content-Length` of `speed/L1` (`web/server.py:1957-1964`).
- `structure/Q1` and its correction: `editing.check` and `highlight_questions.check` make the same call; `assist.check` has its own task, schema and criteria.
- `structure/Q4`, `structure/Q5`.
- `redteam/M2` (`reader.html:301`, every save uses `keepalive`; `notes.js:86` knows the limit), `M3` (`update.py:269-275`), `M4` (`web/server.py:1428-1430`, `:1751-1753`), `M6` (`pipeline.py:510`, `cli.py:404`), `M7` (`reader.html:247`, `srs.py:198`), `M8` (`index.html:1158`).
- Three sentences of the README that the red team says are untrue today are there: "otherwise nothing is touched" (line 70), "each only when you press its button" (389), "due for its first review the same day" (406).
- The CI workflow has no step that keeps EPUBCheck, so each test job fetches it.

**Taken from the reviews without my own check:** the large measurements of the speed review (50 and 500 books, 10,000 notes, frame rates, the unpacking bombs, picture fetching) and the counts of the structure review. For these, two reviewers agree, or a script and its output are on disk and the red team compared them. `speed/P1` (the graph) I accept on the red team's run of the page's own code in Node; I read its output.

**Checked by nobody:** see section 7.

## 3. Where the reviews disagree, and what I decided

| Point | The two views | Decision |
| --- | --- | --- |
| The lock order in `speed/C1` | The speed review: book lock in `_edit`, `Jobs.lock` at the end. The red team: that can deadlock with `delete_question`. | The red team is right. One rule for the whole server: a book's lock is taken first, `Jobs.lock` second and only for short updates, never the other way. `delete_question` and `restart_question` are changed to follow it. A small guard makes a wrong order fail loudly in the tests (item 3.2). |
| What `speed/R1` may assume | The speed review: Practice needs only each book's outline. The red team: every card needs the book's words for its passage. | The red team is right. The plan keeps the outline as a file and adds one step: a card's passage comes from `questions.json`, and the book is parsed only when a highlight lies inside that passage (item 3.5). |
| How to merge attempts (`speed/C2`) | The speed review: union by `(id, at)`, in time order, and count by "the `at` of the last one". The red team: time order breaks counting; an attempt is changed in place by "I was right". | Cards remember which answers they took in by key, not by position. The stored list keeps its order and new entries go to the end. For one key, the copy marked "overruled" wins (item 1.1). |
| "Start a question over" (`speed/C2`) | The speed review: it removes attempts. The red team: it removes the grade and the card only. | The red team is right. The merge needs no special case, because old attempts stay "taken in" by their keys. |
| One `verdict()` for all checks (`structure/Q1`) | The structure review: yes, for four places. The red team: the four differ on purpose. | Share only what is the same: the retry loop of the three "ask" functions, and the one call that `editing.check` and `highlight_questions.check` both make. The run's check and the notes' check stay as they are (item 5.2). |
| Limits on unpacked size (`speed/L1`) | The speed review: 64 MB for a member. The red team: the time is in the word pass, so 64 MB of text still costs 35 s. | Two limits: 8 MB for a text member, 64 MB for any other; refuse first by the size the archive states, and unpack with a limit so that a false statement is caught (item 3.8). |
| The `Host` check (`speed/I1`) | The speed review: host and port. The red team: the port adds nothing and breaks a tunnel. | Compare the host name only. With `--host` set to another address the check is off and the help text says what that means (item 1.11). |
| Java flags for EPUBCheck (`speed/T1`) | 7.2 s to 4.4 s, or 4.9 s to 4.3 s. | Dropped. The gain is small and not agreed. The marker for these tests gives the quick loop (item 2.4). |
| Which comes first: split `Jobs` (`structure/S1`) or change its locks (`speed/C1`, `C5`, `R1`) | The red team: locks first. | Agreed. The split is the last item of stage 5. |
| Which comes first: split `index.html` (`structure/P2`) or fix the graph and the Process view | The red team: fix first, or move exactly these two views first. | Fix first (items 1.13 and 4.1). A view is moved to its own file only when it is being reworked (decision D2). |
| `prep/` "as ported" (`structure/C3`, `C4`, `D2`) against edits to `prep/` (`speed/R2`, `speed/L1`) | Open in both reviews. | The owner's decision D1. Stage 1 does not touch `prep/` except for one error message (item 1.5). |

## 4. Decisions for the owner

None of these blocks stage 1.

**Decided by the owner on 2026-11-22:** D1 yes (small edits to `prep/`, each listed in `prep/__init__.py`); D2 as recommended (`Jobs` is split after stage 3, a view moves to its own file when it is reworked); D3 yes (`ruff` in CI); D4 the library appears in under two seconds, so `speed/S1` is left out. All six stages are to be built in one go.

**D1. May `prep/` differ from the code it was ported from?** `speed/R2` makes every opening of a book about twice as fast and changes four lines of `prep/epub.py` and one function of `prep/words.py`. The limits of `speed/L1` change `prep/epubzip.py`. The output of the engine stays the same byte for byte (the reviews compared 16 books; the plan adds a test). *My recommendation:* yes, in small edits, each listed in a short table in `prep/__init__.py` (file, what, why), in the manner of `docs/BASE.md` of the firmware repository. Then the dead code of `structure/C4` in `prep/` stays (it is the port), and the wrong paths in its docstrings (`structure/D2`) are answered by one paragraph in `prep/__init__.py` that maps old paths to their places here.

**D2. How far to go with the two large splits?** `structure/S1` (the class `Jobs`, 1,761 lines) and `structure/P2` (the script of `index.html`, 1,600 lines). Both are sound and neither fixes a fault. *My recommendation:* do S1 after stage 3, one part at a time, beginning with the notes folder; do not do P2 as one piece of work, but move a view into its own file whenever that view is reworked (the graph and the Process view are the first two).

**D3. A linter in CI (`structure/T5`)?** The code carries `# noqa` marks for a tool that is never run. Adding `ruff` means one more development dependency, one CI step, and a first pass in which its remarks are settled. *My recommendation:* yes, with the few rules the marks already name.

**D4. Is the app slow to start with the real tools (`speed/S1`)?** The app asks Claude Code and Codex for their versions and models before it opens its port. Nobody could time this here, because it needs the real tools. If the library takes more than about two seconds to appear on your computer, say so and it joins stage 3 (medium). Otherwise it is left out.

## 5. The plan

Each item gets its own commit and its own test. A test is written first where the fault can be shown, so that it fails before the change and passes after. After each stage the whole suite is run here (Chromium for the browser tests), and CI runs the nine jobs.

### Stage 1. Faults a reader can meet

All small unless marked. They do not depend on each other, except that 1.2's second step needs 1.1.

**1.1 Answers that never reach Practice, and answers lost with two tabs** (`redteam/M1`, `speed/C2`; medium)

- *What happens today.* Take out, delete or discard a question you have answered: the next answer at a checkpoint gets a grade and no card, for good. Open one book in two tabs: the older tab removes the newer tab's typed answers, place and reading time.
- *Change 1, cards (`srs.py`).* `srs.json` gets a member `taken`: for each answer taken in, its key (`id` and `at`) and the grade it had. `take_in` takes in every attempt whose key is not there. The count `seen` is no longer used. An old file is brought over on its first load: the first `seen` attempts of the stored list count as taken. In the same step, answers that the fault has already swallowed are found and taken in: an attempt graded correct or incorrect, whose question has no card and was not started over. This rule is settled with a test for each case before it is kept.
- *Change 2, an answer overruled after it was taken in.* When an attempt's grade differs from the grade in `taken` and the attempt is marked "overruled", and the card is still new with that miss as its last entry, the miss is replaced by the right answer. A card that has been reviewed since is left alone.
- *Change 3, merging (`reading.merge_state`).* Attempts: the stored list keeps its order; an attempt with a new key goes to the end; for a key both have, the copy marked "overruled" wins. Reading seconds and words: the larger number for each mode. `last_read` and `x_furthest`: the later and the further. The place stays "as last sent": the tab that is being used is the one that saves.
- *Tests.* `tests/test_srs.py`: a removed question and the next answer; an old file with `seen`; the overruled answer. `tests/test_reading.py`: the two-tab story of the speed review, step by step.
- *Risk.* Medium: this changes what `srs.json` holds. Old files must load; the tests include one.

**1.2 The reader stops saving above 64 KiB, and says nothing** (`redteam/M2`)

- *Change.* `keepalive` is used only for the save when the page is left, and only when the body is under 60,000 characters (the rule `notes.js:86` already has). A save that fails is tried again; after two failures the foot of the page says "Your place is not being saved" until a save succeeds.
- *Second step, after 1.1 (small to medium).* The page sends only the attempts the app has not yet confirmed. The server's merge keeps the rest. The body then stays small for any book.
- *Tests.* `tests/browser/test_reading.py`: a state above 64 KiB is saved; a refused request shows the line and a later one clears it.

**1.3 A question too many deletes the book's file** (my check of `redteam/M9`; small to medium)

- *What happens today.* The firmware's file holds at most 32 questions for a checkpoint and 1,024 for a book. `editing.add` does not look at these limits. `change_run` writes the state first and builds second (`runner.py:175-177`); the build then fails, and `build_outputs` deletes the prepared book (`outputs.py:84-86`).
- *Change.* `change_run` builds the firmware book in memory before it writes anything. When it cannot be built, the change is refused with a plain sentence that names the limit, and nothing is written. `build_outputs` never deletes a prepared book that exists; a failed rebuild leaves the old file and says so. `_edit` answers a build error with 400, not 500.
- *Test.* `tests/test_editing.py`: the 33rd question of a section is refused, the state is unchanged, and the book still opens.

**1.4 A new entry's id loop that cannot end** (`redteam/M4`, `structure/S5`)

- *Change.* One `Jobs._new_id(stem)` for both places: the name is cut to make room and a counter is added (`-2`, `-3`), within the 40 characters `JOB_ID_RE` allows.
- *Test.* Three entries of one 40-character name with a fixed clock all get ids.

**1.5 Lines and files that stall the app** (`redteam/M5`, my measurement, `speed/I3`)

- *Change.* One function reads a heading line, with no lazy group: match `#` marks and the rest of the line, then strip closing marks and spaces in Python. It replaces the pattern in `sources/from_text.py:20`, the five uses of `HEADING_RE` in `vault.py` (lines 401, 405, 545, 589, 599) and the copy in `bookfile.py:66`.
- `sources/chars.py` treats any `UnicodeError` from a named encoding as "unknown encoding". A note's title is limited by its bytes, so a title of 120 non-Latin characters is refused with a sentence, not a 500. `RecursionError` in `load_book_bytes` and in `sources.convert` becomes "nested too deeply to be read".
- *Tests.* A heading line with 50,000 spaces is read in under half a second, in each of the three places. The same test feeds every line pattern of `vault.py`, `bookfile.py` and `from_text.py` a long run of spaces and of brackets, to catch a pattern nobody has looked at.
- *Not fixed here:* the Markdown library's own slowness on 10,000 `[` characters, and the HTML parser on 40,000 attributes. See section 6.

**1.6 `update` stops the app before it knows it can update** (`redteam/M3`)

- *Change.* The checks of the checkout and the fetch come first. The app is stopped only when there is something to bring in. If a later step fails, the app is started again as it was.
- *Test.* `tests/test_update.py`: a refused update leaves the app running.

**1.7 Ctrl-C** (`redteam/M6`, `speed/C4`)

- *Change, command line.* `Run.parallel` sets the run's stop event when it is interrupted, before it waits for its calls.
- *Change, the app.* When `serve` ends, every stop event in `Jobs.cancels` and `Jobs.asks` is set, and one stop event of the whole app, which `_models` and `_helper` give to their backends, ends the single calls too. The runs then save themselves as "Stopped".
- *Tests.* `tests/test_recovery.py`: a stand-in call of 8 s; the command ends within a second of the signal. The same for `serve`.

**1.8 Answers stamped in UTC, days counted in local time** (`redteam/M7`)

- *Change.* `stamp()` in `reader.html:247` gives local time, as `practice_answer` does. Old stamps stay as they are.
- *Test.* `tests/test_srs.py` with a fixed clock: an answer late in the evening is due the same day.

**1.9 The Ask page asks the model with no click** (`redteam/M8`)

- *Change.* A question in the address fills the box and waits for the button. The "Ask" link of the Notes page, which passes on the search words, sets a mark in memory that lets that one visit ask at once.
- *Test.* `tests/browser/test_knowledge.py`: opening and reloading the address makes no call.

**1.10 Numbers mistaken for error codes** (`speed/M1`)

- *Change.* `\b5\d\d\b` and `\b401\b` in `llm.py:329-330`, as the Codex patterns already have.
- *Test.* `tests/test_recovery.py`: "215500 tokens" is not tried again; "240100 tokens" is not a sign-in error.

**1.11 Requests the app should refuse** (`speed/I1`, `speed/L1` point 4)

- *Change.* `dispatch` refuses a `Host` that is not `localhost`, `127.0.0.1` or `[::1]` (any port), unless `--host` named another address. A request that changes something is also refused when its `Origin` names another site. A negative `Content-Length` is refused. The help of `--host` says that another address opens the app, with no password, to everyone who can reach it.
- *Tests.* `tests/test_visible_and_web.py`, beside the test of the header.

**1.12 The reader misses a question finished while it loads** (`speed/C3`)

- *Change.* In `reader.html:1361` the second answer's questions are always taken, then `syncPrompts()` and `drawFoot()`.
- *Test.* `tests/browser/test_questions.py`, in the shape of the notebook's test.

**1.13 The graph** (`speed/P1`; medium)

- *What happens today.* With about 100 links to one note the picture flies apart; with about 120 the tab hangs for good. With thousands of notes the page draws nothing for seconds, and it uses half a core when nothing moves.
- *Change.* A point moves at most 40 pixels in one turn (this alone ends the hang: the red team measured it). The pull of a link is divided by the square root of the links at its ends. Points start in a circle that grows with the square root of their number. The loop asks for a next frame only while the layout is warm or something changed. `busy` is worked out once.
- *Tests.* `tests/browser/test_knowledge.py`: a folder with 150 notes linking to one; the page answers and every coordinate stays within bounds. The fixed wait of 1.5 s in the existing test becomes a wait for the settled state.

**1.14 Opening the editor writes the run's state** (`speed/R3`, first part)

- *Change.* Reads of a run (one question, the paragraphs of a section, the review) get their own function in `runner.py`, which writes nothing. This also closes the window in which opening the editor undoes a "Rebuild files" (the red team produced that 7 times in 7).
- *Test.* `tests/test_editing.py`: `state.json` is unchanged after each of the three reads.

**1.15 Three wordings the reader sees** (`structure/Q4`, `Q5`, `P5`)

- The sixth criterion gets a plain wording, kept once in `prompts.py`, used by `outputs.py` and sent to the page with `GET /api/info`; the table in `index.html` goes. `prompts.py:429-430` loops over `criteria(settings)`; `steer.py:193` says "The criteria are unchanged".
- `forms.js` `plain()` gets the case `forms.py:197` has, so "True. True, because" is gone.
- The editor's `KINDS` gets `article`; `editor.js` is built again (the red team confirmed that a build of today's source gives today's file, so the diff will be this one word).
- *Tests.* `tests/test_forms.py` for the report's line; a browser test for `QForms.plain`.

**1.16 One bad file breaks the library or the start** (`speed/I4`)

- *Change.* `Jobs.__init__` skips a `job.json` of the wrong shape and prints its path. `listing` shows an entry whose files cannot be read as "cannot be read", with the reason.
- *Test.* `tests/test_visible_and_web.py`.

**1.17 One failed poll stops the page** (`speed/P3`, first half)

- *Change.* On an error the library and the run page keep what is drawn, show the message in a line of its own, and try again after 5 s.
- *Test.* `tests/browser/test_app.py`: one refused request, then the page goes on.

### Stage 2. Foundations for what follows

**2.1 One module for stored files** (`structure/C1`, with parts of `speed/C5` and `speed/C6`; medium)

- `ebook_processor/store.py`: `now()`, `write_json`, `load_json(path, version, empty, valid)`, and `write_bytes` and `write_text` that go through a temporary file and `os.replace`. `pipeline.py` keeps the old names as imports. The five loaders (`notes.py`, `process.py`, `assist.py`, `srs.py`, `reading.py`) use `load_json`, each with its own check. `vault.now` goes.
- `outputs.py` and `exports.write_anki` write every file through the new functions, so no half-written file can be served.
- `os.replace` is tried up to five times on `PermissionError`; the run's state is flushed to disk before the rename. This part (`speed/C6`) is not verified, because nobody had a Windows machine; it is cheap and does no harm.
- *Tests.* The existing suites of the five stores; one new test that a reader of an output during a rebuild never sees a short file.

**2.2 One way to make a backend** (`structure/S3`, `speed/M2`; small to medium)

- `runner.ready_backend(settings, roles, progress, cancel)` and `Jobs._backend(...)` replace the six copies. Requests the reader waits for get their own limits: 120 s for a call and one more try. The answer of `check_available` is kept for a minute.
- *Tests.* `tests/test_editing.py`, `test_highlight_questions.py`, `test_vault_web.py`, `test_codex.py`, `test_recovery.py`.

**2.3 Shared tools for the tests** (`structure/T1`, `T2`; medium)

- `tests/conftest.py`: a class `WebApp` (`call`, `wait`, `import_fixture`, `process_fixture`) with the fixtures `web_app` and `make_web_app`. `tests/paths.py` for `FIXTURE`, `PREPARED` and the other data paths. Shared fixtures move to the `conftest.py` of their folder; helpers to `tests/sources/makers.py`, `tests/prep/prepkit.py` and a new `tests/browser/helpers.py`. The files move one at a time.
- This comes before stage 3 because stage 3 adds many tests over HTTP.

**2.4 The EPUBCheck tests** (`structure/T3`, `speed/T1`; small)

- A marker `epubcheck` on the 21 tests, so that `pytest -m "not epubcheck"` is a loop of about 90 s. CI runs all.
- `real_epubcheck` does not download unless an environment variable allows it. The three `test_cli_*` tests skip, with the reason, when Java or EPUBCheck is missing. The docstring of `tests/conftest.py` says what its guard covers.
- The parametrised test over the broken books uses the real EPUBCheck only for the entries that expect it.
- CI keeps EPUBCheck between runs. The README says that the full tests need Java.

### Stage 3. The server: locks, kept data, limits

The order inside this stage is fixed: 3.1 before 3.2, and both before 3.5.

**3.1 Rebuilds under a lock of the book** (`speed/C5`; small to medium; needs 2.1)

- A lock for each book (a dictionary by id). `Jobs.rebuild` and the download that rebuilds stale exports take it, and look at `exports_stale` again inside it, so three downloads make one rebuild.
- `exports_stale` becomes a number that `_accept` raises. A rebuild remembers the number it started with and clears the flag only when it is still that number.
- *Tests.* Three downloads at once give one rebuild. A question accepted during a rebuild leaves the exports marked stale.

**3.2 No model call and no rebuild under `Jobs.lock`** (`speed/C1`; medium to large)

- *The rule,* written at the top of `Jobs`: a book's lock is taken first; `Jobs.lock` second, only for short reads and updates of the library; never the other way. A thread never holds two book locks.
- *The guard.* The book locks are a small class that knows, for each thread, whether that thread holds `Jobs.lock`. Asking for a book lock while holding `Jobs.lock` raises. It is always on; it costs one look at a thread-local value.
- `_edit` takes the book's lock for the read, change and write of the run, and `Jobs.lock` only to update `job`. `delete_question`, `restart_question` and `add_question` are reordered to follow the rule. Before the work starts, every `with self.lock` that reaches `_edit`, `rebuild` or `change_run` is listed and settled.
- `editing.check` is cut in two: build the task, and apply a verdict to a state that is read again. `check_question`, `rewrite_question` and the model's choice of passage read under the book's lock, call the model with no lock, and apply under the lock after checking that the question is as it was.
- *Tests.* A second request is answered while a slow check runs. "Delete" and "Save" of one book at the same time both finish. An edit made during a check is not overwritten by the verdict.

**3.3 The opened run is kept; a rebuild parses less** (`speed/R3`, second part; medium)

- The opened run (the parsed original and its bytes) is kept for the last two books, by the stamp of `work/source.epub`. `validate_bytes` can be given the parsed original and the parsed prepared book; a comment says that the validator then trusts the builder's parse.
- *Gain to confirm by measurement:* the editor opens in a few milliseconds; with 3.4 a rebuild falls from about 3 s to about 1.2 s.

**3.4 The cheap "is there a word" test** (`speed/R2`; small; needs D1)

- `words.is_separator_only` (it exists, and only its own test uses it) is written as one regular expression over `SEPARATORS` and `REMOVED`, and used in the four places of `prep/epub.py`. Not with `str.strip()` or `\S`: six characters differ.
- *Tests.* The two tests agree for every code point; the 16 test books give the same blocks.

**3.5 The outline of a book as a file; few parsed books in memory** (`speed/R1` with the red team's corrections; medium to large)

- *The outline is one more output of the build.* `build_outputs` writes `manifest.json` beside the other files: what `ReadingBook.manifest()` gives, each chapter's word count, and for each question the place of its passage. It is written with the other outputs under the book's lock, so it cannot be older than its book. It carries a format number. For an imported book, and for a folder made before this change, it is made the first time the book is opened.
- Practice, the Notes front page, the Process view, the book page, `_place`, `_accept`, `file_book`, `reading_state` and `save_reading` read this file (kept in memory by its stamp). `notes.set_note` takes the chapter's word count from it.
- *A card's passage* comes from `questions.json` and the places in the manifest. The book is parsed only when a highlight lies inside the passage, because only then are the words needed to mark it.
- `Jobs.books` keeps the last four parsed books. A book is parsed outside `Jobs.lock`: look under the lock, parse without it, store under it.
- *Tests.* Practice over two books with no highlight in a passage opens no book. A card with a highlight in its passage shows the mark. An edit, a question from a highlight and a change of kind each show at once.
- *Gain to confirm by measurement:* the first visit to Practice or Notes with 50 books falls from 21 to 43 s to under a second; memory stays near 60 MB plus four books.

**3.6 The library listing and small repeats** (`speed/R4`, `R7`; small)

- A book's summary is kept with the stamps of `reading.json`, `srs.json` and `notes.json` and the day. `listing` holds `Jobs.lock` only to copy the list. One request reads each book's manifest and notes once. The Notes front page counts waiting items without building the rows.

**3.7 The notes folder** (`speed/R5`, `R6`; small to medium)

- The walk uses `os.scandir` and plain strings, in the same order as today. `get` and the token ask for one file's stamp. `save`, `set_props` and `add_to` check the one file just before writing. `_joined` is kept until the index changes. Search makes snippets after the sort and the cut.
- *Cost, stated by the red team:* a file added outside the app with the same title is seen up to a second later.

**3.8 Limits** (`speed/L1`, `L2`, `I2`; small each; the first needs D1)

- An EPUB member is refused above 8 MB for text and 64 MB otherwise, first by the size the archive states, then by unpacking with a limit. A `.docx` is checked by its directory before `mammoth` sees it. A web answer is unpacked with a limit.
- Held files of the Add form are dropped by age on every request that touches them. A day of the timeline shows 200 entries and a count of the rest.
- Pictures of a page: at most 80 tries and 30 s in all, with the warning the form already shows. `is_private` tests `not address.is_global`.

### Stage 4. The pages

**4.1 The Process view** (`speed/P2`; medium). A choice changes `aria-current` on two rows. The first 200 rows are drawn and more are added near the end of the list. The request sends only the state that is shown, with the three counts. "Set aside all" and "oldest first" still work on the whole list.

**4.2 One element builder, one API call, one notice** (`structure/P1`; medium). A small `base.js`, loaded first by both pages, publishes `App.h`, `App.api` and `App.toast`. The shared `h` takes the union of the property names; the shared `api` always carries `status` and `data` on its error. The failed-save line of 1.2 and the retry of 1.17 move onto it.

**4.3 The library keeps the keyboard's place** (`speed/P3`, second half; small). A poll replaces only the rows whose data changed.

**4.4 When these parts are next worked on** (medium each): one shared card for a highlight's questions (`structure/P4`), one `base.css` (`structure/P3`), the side panels of `Notes.mount` as functions of their own (`structure/P6`). No fault depends on them, so they are done when the part is open for another reason.

**4.5 Views in files of their own** (`structure/P2`; decision D2). The graph and the Process view move first, since stages 1 and 4 rework them.

### Stage 5. The structure of the server and the modules

**5.1 Small cleanups** (small each; one commit for each finding):

- `structure/S4`: one `_openable(row, ...)` for the five places.
- `structure/S5`: the unused route `GET /api/vault/tags`, the two unused parameters, `void()`, the repeated small expressions. (The id loop is 1.4.)
- `structure/Q2`: `forms.as_prompt` and `forms.as_question`; `Jobs.imported(job)`. No stored member is renamed.
- `structure/Q3`: `pipeline.new_question`, `next_checkpoint`, `sort_questions`, and `kept(q)` in `editing.py`.
- `structure/N2`: `Vault.link_to(note)`; the patterns kept once; `Vault.words` public.
- `structure/N3`: `put_book` takes the text. 2 callers in the server and 15 in the tests follow.
- `structure/C2`: `authors` and `chapter_titles` out of `exports.py`; imports to the top of their files, one at a time.
- `structure/C3`, `C4`: private names that are shared lose the underscore; dead code outside `prep/` goes. Inside `prep/` this follows D1.
- `structure/C5`: the six small repeats. Texts sent to a model stay the same to the letter.

**5.2 Asking a model for one question, once** (`structure/Q1`, narrowed; medium). `ask_with_retry(...)` with the feedback text as an argument serves `highlight_questions.propose`, `assist.propose` and `editing.rewrite`. `prompts.feedback_block(problems)` holds the "PROBLEMS WITH YOUR EARLIER REPLY" line. The call that `editing.check` and `highlight_questions.check` share is one function; it falls out of the cut made in 3.2. The run's check and the notes' check are not merged.

**5.3 The properties reader out of `vault.py`** (`structure/N1`; small for the first step). `ebook_processor/frontmatter.py`; then `notetext.py` for the functions `assist.py` and `bookfile.py` also use. Not at the same time as 3.7, which edits the same file.

**5.4 The routes** (`structure/S2`; medium). Cases grouped by area under comment lines; each check of a body moved into its `Jobs` method; `upload()`, `local_file()` and `send_text()` on the handler; public methods where the handler calls private ones; `Jobs.save_srs_settings` under the lock (the red team found no harm in today's write, so this is for order).

**5.5 `Jobs` in parts** (`structure/S1`; large; decision D2). After stage 3, with the lock rule of 3.2 at the top of each part. One part at a time, the notes folder first. The red team's correction applies: the parts call each other in a ring, so each call back goes through the thin class.

### Stage 6. Checks and documents

**6.1 Tests that are missing** (`structure/T4`; small). The Stop button of a run over HTTP (`/cancel`, then `/continue`). `tests/test_settings.py` for `from_options` and `validate`. The regrouping of `test_visible_and_web.py` and of the command-line tests follows 2.3.

**6.2 Checks that are not run** (`structure/T5`; small each).

- `ruff` in CI (decision D3).
- The fixture generator: its three path texts are settled first, the committed files are made again, and then a test runs `generate.py --check`. The generator names a `prepared.py` that is not in this repository, so the prepared book and the 13 broken books cannot be made again here. Either that script is brought over from the firmware repository, or the generator's text says where it lives.
- A CI job with Node that builds the editor and fails when `editor.js` differs.

**6.3 Documents** (`structure/D1`, `D2`, `D3`; small to medium). The seven missing rows of the README's module table. The engine's docstrings as D1 decides. The stale docstrings in place. The version read from one place. The three README sentences that stage 1 makes true again need no edit; they are checked once after stage 1.

## 6. Left out, and why

| Item | Why |
| --- | --- |
| `speed/M3`: draft one long document in parallel | Large, the gain is the reviewer's estimate, and it helps only a text that is one document. The reviewer puts it last too. |
| `speed/P4`: `ETag` for pages | The gain on one computer is a few milliseconds. To be done only if `send_page` is changed for another reason. |
| `speed/S1`: open the port before the tools are asked | Decision D4. Not measured by anyone. |
| Java flags and a report cache for EPUBCheck (`speed/T1`) | The numbers are not agreed, and the cache would change the app, not only the tests. The marker of 2.4 is enough. |
| One `verdict()` for all four checks (`structure/Q1`) | The four differ on purpose (section 3). |
| Renaming stored members (`structure/Q2`) | Books already processed hold them. The reviewer does not suggest it either. |
| Pictures fetched side by side (`speed/I2`, second half) | The limits of 3.8 end the long waits. Fetching in a pool is a gain in comfort and has a risk for the same-bytes rule; later, if pages with many pictures prove slow in real use. |
| Markdown with 10,000 `[` (17 s) and HTML with 40,000 attributes (12 s for each of four parses) (`redteam/M5`) | The time is inside the libraries. The reader would have to add such a file themselves. Parsing HTML once and handing the tree on would divide the second case by four and make every HTML file a little quicker; it is a medium change to `sources/` and can follow stage 3 if wanted. |
| `GET /api/vault` makes the notes folder and writes the books' files (`redteam/M9`) | It does what the first visit to Notes would do anyway. After 3.5 it is cheap. Left as it is. |
| `pytest-xdist` | Not installed here, and nobody tried whether the tests are free of shared state. Worth a try after 2.3 and 2.4. |

## 7. Still not reviewed by anyone

These are gaps of the three reviews taken together. Nothing here is a known fault.

- **Windows and macOS.** Every measurement is from Linux. `speed/C6` is a suspicion. `redteam/M2` was shown in Chromium only (the limit is in the Fetch standard, so the other browsers should agree).
- **Real tools and the web.** No model was called and no real address was fetched, in any of the three reviews or in my checks. Start-up time, real error messages and real pages are read from the code.
- **The logic of `prep/`** beyond `words.py`, parts of `epub.py` and `epubzip.py`: `build.py`, `validate.py`, `draft.py`, `checkpoints.py`, `matter.py`.
- **`forms.js` grading** (how a typed answer is matched), `steer.py`, `prompts.py`, the instruction files, `visible.py`, `exports.py`, `content.py`, `settings.js`.
- **`index.html`:** Revisit, the timeline, a note's page and the trash.
- **A hostile Word file** (one test was stopped after six minutes) and a hostile `.mhtml` beyond nested parts.
- **Unused CSS:** a word search found none; a browser's coverage tool would prove it.
- **Typing in a very large note** (the editor rebuilds its block decorations on each change).

If one of these matters to you, it can be a fourth review of its own. The first two are the ones I would choose.

## 8. How the work is carried out

- Stage 1 items are small and stand alone. Under the standing rule for such changes, each goes to `main` once its test and the whole suite pass, after the owner has agreed to this plan.
- Stages 2 to 6 are developed on `claude/dreamy-allen-6un3tt` and go to `main` on the owner's word, a stage at a time.
- Where an item says "gain to confirm by measurement", a short script is kept in `tools/measure/` and its numbers before and after go into the commit message. The reviewers' own scripts are in the session's scratch folder and will not outlive it; the tests named in this plan are their lasting form.
- `DEFERRED.md` is brought up to date at the end of each stage: what was left out (section 6) and what is still unverified (section 7).

## 9. Record of progress

One line for each item when it is done, with its commit.

