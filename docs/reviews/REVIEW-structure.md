# Review: structure and upkeep

Reviewed on 2026-11-22, at commit `4557303` (`main` and `claude/dreamy-allen-6un3tt` are the same there). This review looks at how hard the code is to understand and to change. Speed, resource use, concurrency, robustness and security belong to a second review and are left out here, except where a structural problem causes one.

No code was changed. Line numbers are those of commit `4557303`.

## 1. Summary

The code is in better order than its size suggests. The comments are short and say what the code is for. The engine (`prep/`), the converters (`sources/`), the run (`pipeline.py`), the model backends (`llm.py`), the scheduler (`srs.py`), the steer (`steer.py`) and the question forms (`forms.py`) each do one job and have clear edges. The tests are many and they pass: 856 passed and 1 was skipped in 210 seconds here, without the browser tests.

The cost of fast growth has gathered in four places:

- **`web/server.py`.** The class `Jobs` has 1,761 lines and 118 methods and does about twelve jobs. `Handler.route` is one `match` statement of 361 lines and 92 cases.
- **The pages.** The script of `index.html` is 1,600 lines and holds thirteen views. The five scripts each carry their own copy of the same two helpers, and the copies differ.
- **Questions.** There are three kinds of question (of a book, of a highlight, of a note). Each has its own code to ask a model for one, to check one and to reword one. Much of that code is the same text three times.
- **The tests.** The fixture that starts the app and calls it over HTTP is written eleven times. Test files import constants and fixtures from each other.

The documents are close to the code. The main gaps are the README's module table, which leaves out seven modules, and the docstrings of the ported engine, which name files that are not in this repository.

What I would change first, in this order:

1. Fix the small things that a reader of the app can see: the sixth criterion has no wording in the report or on the page (Q4), the page's plain-text form of a question has drifted from the Python one (Q5), the editor does not know the kind "article" (P5). Delete the dead code and the unused route (C4, S5). About two hours in all.
2. Give the pages one shared copy of the element builder, the API call and the notice (P1).
3. Give the tests one shared app fixture, one wait helper and one module of constants (T1, T2).
4. Write "ask a model for one question" and "check one question" once, for the three kinds of question (Q1).
5. Move the small shared tools (`now`, `write_json`, loading a stored file) out of `pipeline.py`, and the properties reader out of `vault.py` (C1, N1).
6. Split `Jobs` along the section comments it already has, and make the routes thin and grouped (S1, S2).
7. Split the script of `index.html` by area, and give the two pages one shared style sheet (P2, P3).
8. Bring the README's module table and the engine's ported docstrings in line with the repository (D1, D2).

There are 32 findings: 2 large, 10 medium and 20 small. Three of the small ones (Q5, T2, T4) have a second step that is medium. Section 2 has them all.

## 2. Findings

Each finding says where it is, what is wrong, what I suggest, the size of the work (small: under an hour; medium: a few hours; large: a day or more), the risk with the tests that protect the change, and whether I verified it.

### 2.1 The web server

#### S1. `Jobs` does about twelve jobs

- **Where:** `ebook_processor/web/server.py:143-1903`.
- **What is wrong:** The class has 1,761 lines and 118 methods. Its own section comments name the jobs: the library of runs, practice, editing a book's questions, questions from highlights, the settings window, highlights and notes, the notes folder, processing, the model's help in the notes folder, reading, the steer, other sources, and the run itself. Three things show that the class has outgrown its comments:
  - The section "the model's help in the notes folder (see assist.py)" (line 1031) runs for 340 lines. It also holds `timeline`, `vault_search`, `vault_note`, `vault_peek`, `vault_create`, `vault_gather`, `vault_cite`, `vault_save`, `vault_delete` and `practice_answer`, which are not the model's help.
  - The section "other sources than EPUB" (line 1612) also holds the whole life of a run: `create`, `start`, `cancel`, `delete`, `_event`, `_read`, `_work` and `rebuild`.
  - Two sections are called "reading" (lines 174 and 1371).
  The class also uses one name for two things. `Jobs.reading_state()` (line 1393) is the reader's place and grades. `job["reading_state"]` (lines 1472, 1492, 1766) is how far the model's reading of the reader's wishes has come.
- **Suggestion:** Split along the section comments. The parts share only a few things: `self.jobs`, `self.lock`, `self.data`, `folder()`, `get()`, `book()`, `prepared()`, `cards()`, `ui_settings()`, `vault()` and `backend_factory`. A possible shape, each part a class in its own file under `web/` that is given the library object:
  - `library.py`: the runs on disk, `get`, `summary`, `listing`, `detail`, `_store`, the cache of opened books, `import_prepared`, `set_type`, `delete`, `ui_settings`.
  - `runs.py`: `create`, `start`, `cancel`, `_work`, `_read`, `_event`, `rebuild`, the steer (`answer_reading`, `go_on`, `set_steer`, `_plan`, `_run_steer`, `steer_for`), and the held files of the Add form (`look_source` to `add`).
  - `study.py`: the reader's state, `notes_do`, `notebook`, `practice`, `practice_answer`, `cards`, `passage`, `sources`.
  - `questions.py`: a book's own questions (`_edit` to `rewrite_question`) and questions from highlights (`propose` to `remove_marked`).
  - `notes_folder.py`: `vault`, `file_book`, `_cited`, `_note`, every `vault_*` and `process_*` method, `timeline`, the `assist_*` and `note_question_*` methods.
  Keep `Jobs` as a thin class that holds the parts and passes on the method names the tests call. The tests call about 28 of them directly (`jobs.propose`, `jobs.manifest`, `jobs.notebook`, `jobs.steer_for` and others). Start with `notes_folder.py`: it is the largest part (about 600 lines), and it uses the rest only through a few methods (`get`, `folder`, `book`, `_readable`, `question_settings`, `today`).
- **Size:** large. It can be done one part at a time.
- **Risk:** medium. The lock is shared and several methods rely on being called with it held ("the caller holds the lock" in `_forget_answers`, `_writing`, `_accept`). Moving code must not change which lock is held where. The tests over HTTP protect the behaviour: `tests/test_vault_web.py`, `test_reading.py`, `test_srs.py`, `test_steer.py`, `test_editing.py`, `test_highlight_questions.py`, `test_forms.py`, `test_notes.py`, `test_visible_and_web.py`, `tests/sources/test_sources_web.py`, and the browser tests. `tests/test_srs.py:132` replaces `web.Jobs.today`, so that name must stay or the test must follow.
- **Verified:** yes. The sizes were measured with a script; the section contents were read.

#### S2. `Handler.route`: one statement of 361 lines, with rules in two places

- **Where:** `ebook_processor/web/server.py:2015-2375`.
- **What is wrong:**
  - **Order.** The 92 cases are in the order they were added. `process` routes stand between `vault` routes (2154-2159). `assist` routes stand between two groups of `vault/notes` routes (2168-2183). The routes of one run start at 2222 and end at 2374, with `detail` and `delete` in the middle (2324-2328).
  - **Where a request is checked.** Some requests are checked in the route (`practice/answer` at 2118-2120, `rebuild` at 2356-2362, `questions/propose` at 2244-2251, `timeline` at 2190). Others are checked in the `Jobs` method (`process_aside`, `set_place`, `vault_create`, `look_url`). A reader cannot know where to look.
  - **Repeats.** The check of a local path is written twice (2058-2062 and 2076-2080). "no file was sent" is written three times (2049, 2069, 2104). A Markdown download is written out twice, header by header (2204-2211 and 2268-2275). The ASCII form of a file name is made in three places (1952, 2203, 2267).
  - **Private methods.** The handler calls `jobs._note` and `jobs._vaulted` (2153, 2195, 2202, 2203, 2214, 2219). They are part of what the handler needs, so they are not private.
  - **One path, two meanings.** `PUT /api/jobs/<id>/reading` saves the reader's place (2318). `POST /api/jobs/<id>/reading` answers the model's reading of the reader's wishes (2334).
  - **A write outside `Jobs`.** `PUT /api/practice/settings` writes `srs-settings.json` in the route itself (2123-2126). The file name is also in `Jobs.srs_settings` (201). Every other write goes through a `Jobs` method.
- **Suggestion:** Keep the `match` statement; it is plain and has no hidden parts. Change four things. (1) Group the cases by area under comment lines, in the order of S1's parts. (2) Move every check of a request body into the `Jobs` method, so that a route is one line: read the body, call, send. (3) Add three helpers to `Handler`: `upload()` (the body, or "no file was sent"), `local_file(request)` (the path checks), and `send_text(text, name, kind)` (a download), and use `send_text` in `send_file` too. (4) Give `Jobs` the methods the handler lacks (`vault_restore`, `vault_neighbourhood`, `vault_export`, `vault_token`, `save_srs_settings`) and stop calling the private ones. Do not rename the `reading` path: stored pages and tests use it. Say in a comment that the two differ.
- **Size:** medium.
- **Risk:** low. All but four of the routes have a test over HTTP in the Python suite (T4 names the four). Error texts must stay the same: tests compare some of them.
- **Verified:** yes, by reading every case.

#### S3. "Make a backend and check that its tools are there" is written six times

- **Where:** `web/server.py:383-387` (`_models`), `571-575` (`_propose_one`), `1036-1043` (`_helper`), `1814-1816` (`_read`); `runner.py:62-65` (`_ready`); `cli.py:271-272`.
- **What is wrong:** Each place builds `default_backend(settings, ...)` and then calls `check_available` with a list of model names worked out from roles. The expression `settings.checker_model or settings.model if r == "check" else settings.model` is at 386 and again at 1040. The four places in the server also each test `self.backend_factory` first.
- **Suggestion:** One function in `runner.py`: `ready_backend(settings, roles=("draft", "check"), progress=None, cancel=None)`, which builds the backend and checks the tools those roles need. One method in the server, `Jobs._backend(settings, roles, cancel=None)`, which returns the test backend when there is a factory and calls `ready_backend` otherwise. `_models` and `_helper` then differ only in which settings they read.
- **Size:** small.
- **Risk:** low. `_helper` turns `BackendError` into an `ApiError` at once and `_models` leaves that to `_asked`; keep both behaviours. Protected by `tests/test_editing.py`, `test_highlight_questions.py`, `test_vault_web.py` (the `helped` fixture) and `test_codex.py`.
- **Verified:** yes.

#### S4. "Where can this passage be opened" is worked out in five places

- **Where:** `web/server.py:846-856` (`_cited`), `1024-1028` (`vault_related`), `1065-1069` (`assist_connections`), `1076-1079` (`assist_answer`), `1240-1244` (`vault_search`).
- **What is wrong:** Each loop takes a row with a book id and a block, asks `bookfile.job_of`, asks `_readable`, and adds `job`, `go` and sometimes `in_library`. The rows name the book id differently (`book_id` in two, `id` in three), which is why the loop was copied and not shared. The list of members `("id", "title", "block", "text", "found", "job", "go", "in_library")` is also written twice (875 and 1326).
- **Suggestion:** One method `_openable(row, book_id, block)` that adds `in_library`, `job` and `go` to a row. Call it from the five places. Better still, make `Vault.related`, `Vault.search` and `assist` name the member the same way (`book_id`), so that the five calls are the same line.
- **Size:** small.
- **Risk:** low. The pages read `in_library`, `job` and `go`; keep those names. Protected by `tests/test_vault_web.py` and `tests/browser/test_knowledge.py`.
- **Verified:** yes.

#### S5. Small leftovers in `server.py`

- **Where and what:**
  - `GET /api/vault/tags` (2147-2148) has no caller. I searched the five scripts, `editor/src/index.js`, `update.py` and all tests for `vault/tags` and found nothing. The tags reach the page with `GET /api/vault`.
  - `_asked(self, job_id, call)` (389) never uses `job_id`. `_helped` passes `""` for it (1047).
  - `_seen(self, token, entry, made, split_how)` (1683) never uses `entry`.
  - `void()` (72) exists for one use (1172).
  - The server calls `srs._shuffle` (260) and `vault._taken` (1341). Both are private names of other modules.
  - The id of a new run is made twice with the same loop (1426-1430 and 1749-1753).
  - "What a book's file in the notes folder was made from" is worked out twice (810 and 826).
  - `Steer(mine["purpose"], mine["about"], mine["style"])` is built four times (1554, 1558, 1820, and with `.get` at 1496). The reader's two texts from the settings are read three times with the same expression (1506, 1594, 1743).
  - `questions.json` is opened and parsed by two methods with the same six lines (303-307 and 331-335).
  - In `practice`, the card for a note and the card for a book are two dictionaries with ten members in common (277-282 and 285-292).
  - The module docstring (line 1) still says "pick an EPUB".
- **Suggestion:** Delete the route, the two parameters and `void`. Make `srs.shuffle_key` and `Vault.taken` public, or better, move the title check of `vault_save` (1337-1342) into `Vault.save` as a `title` argument, so that the server needs no private name. Add `Jobs._new_id(stem)`, `Jobs._made_from(job_id)`, `Jobs._steer_of(job)` and `Jobs._questions_doc(job_id)`. Build the common part of a practice card once.
- **Size:** small.
- **Risk:** low. Protected by the HTTP tests named under S1.
- **Verified:** yes. The unused parameters were found with a script and then read.

### 2.2 Questions: three kinds, three copies

#### Q1. Asking for one question and checking one question are each written three times

- **Where:**
  - Ask a model, send a bad reply back once: `highlight_questions.py:292-306` (`propose`) and `assist.py:308-322` (`propose`). Line for line they are the same, down to the wording of the two error texts. `editing.py:248-263` (`rewrite`) has the same loop for another shape of reply.
  - Check one question: `editing.py:203-224` (`check`), `highlight_questions.py:309-327` (`check`), `assist.py:325-343` (`check`). All three build the task, call the checker's model, read the criteria as true or false, and return a verdict with `passed`, `criteria` and `problem`. The run does the same for many questions in `pipeline.py:766-801` and `896-940`.
  - "Add the question's own form to the allowed forms": `editing.py:197-200` (`_with_form`), `highlight_questions.py:317-318`, `assist.py:336-337`.
  - Tidy the choices and the accepted answers of a form before it is kept: `highlight_questions.py:99-103` (`formed`) and `editing.py:75-79` (`_wording`).
  - The text "PROBLEMS WITH YOUR EARLIER REPLY (fix exactly these and reply with the complete object again)" is a literal in four places: `prompts.py:357`, `392`, `420` and `editing.py:261`. A second wording of it ("PROBLEM WITH YOUR EARLIER REPLY (fix it and ...)") is a literal in two more: `highlight_questions.py:305` and `assist.py:321`.
  - `assist.py:314` calls `hq._clean`, a private name, to do this.
- **What is wrong:** A change to how a reply is retried, or to what a verdict holds, must be made in three files. The verdicts have already drifted: `editing.check` keeps `suggested_fix`, `minor_note` and `at`; the other two do not. `editing.check` raises when the model gives no verdict; `hq.check` returns "not passed".
- **Suggestion:** A small module, for example `ebook_processor/asking.py`, with three functions:
  - `ask_with_retry(backend, role, system, message, schema, model, read)`: calls the model, hands the reply to `read`, and when `read` raises with a problem, sends that problem back once. `hq.propose`, `assist.propose` and `editing.rewrite` use it.
  - `verdict(entry, criteria)`: turns one entry of a checker's reply into `{"passed", "criteria", "problem", "suggested_fix", "minor_note"}`. The three `check` functions and `pipeline.read_check` use it.
  - `with_form(settings, q)`: moved from `editing.py`.
  Put the "PROBLEMS WITH YOUR EARLIER REPLY" line in `prompts.py` as one function, `feedback_block(problems)`.
- **Size:** medium.
- **Risk:** medium. The test stand-in `tests/scripted.py` reads the task messages by their exact wording, so the texts sent to the model must not change. Protected by `tests/test_highlight_questions.py`, `test_assist.py`, `test_editing.py`, `test_forms.py` and `test_pipeline.py`.
- **Verified:** yes, by reading the three files side by side.

#### Q2. One field has two names, and some names have two meanings

- **Where:**
  - A question's text is `prompt` in the run's state, in the draft, in the reader's manifest and in the models' replies. It is `question` in `notes.json`, in the notes' questions, in `questions.json`, in the review page and in the editor window. The two are turned into each other in ten places: `exports.py:103`, `outputs.py:183`, `185`, `runner.py:191`, `editing.py:104`, `assist.py:352`, `highlight_questions.py:192`, `206`, `316`, and `reader.html:1044`. `forms.js:47` has a line that takes either name ("the notebook calls it 'question'").
  - `kind` means four things: `job["kind"]` is `"imported"` or absent (`server.py:356`); `Settings.kind` is the kind of book (`settings.py:34`); a note's `kind` is idea, quick, book and so on (`vault.py:47`); an item's `kind` in `process.py:50` is highlight or note. What a library entry is (book or article) could not be called `kind` for that reason and is `type` (`docs/SOURCES-PLAN.md` section 6.7 says so).
  - `reading`: see S1 and S2.
- **What is wrong:** None of this is a fault today. It costs time on every change that carries a question from one store to another, and it invites a mistake of the kind `q["prompt"]` on a record that has `question`.
- **Suggestion:** Do not rename stored members: books already processed hold them. Do two small things. (1) In `forms.py`, add `as_prompt(q)` (a record with `question` as one with `prompt`) and `as_question(q)` (the other way), each carrying `members(q)`, and use them in the ten places. (2) In `server.py`, read `job["kind"] == "imported"` through one method (`Jobs.imported(job)`; it is tested in four places: 356, 637, 1893 and 2348) and say in `README.md` or in the module docstring what each `kind` is.
- **Size:** small for the two helpers. Renaming a stored member would be large and I do not suggest it.
- **Risk:** low.
- **Verified:** yes.

#### Q3. `editing.py` repeats rules that `pipeline.py` owns

- **Where:** `editing.py:105` and `246` write out `("passed", "unchecked", "unverified", "edited") and not q.get("removed")`; `pipeline.py:56` and `442-443` define that as `KEPT` and `kept(q)`. A new question's record is written out in `pipeline.py:678-682` and again in `editing.py:169-171`. The next checkpoint id is taken in `pipeline.py:673-674` and in `editing.py:166-167`. The order of the questions is restored in `pipeline.py:685-686` and in `editing.py:173-174`.
- **What is wrong:** A new status, or a new member of a question, must be added in both files. `editing.py` already imports five names from `pipeline.py`, so there is no reason for the copies.
- **Suggestion:** In `pipeline.py`, add `new_question(id, section, wording, **more)`, `next_checkpoint(state)` and `sort_questions(state)`. Use them in `Run.add_ranges` and in `editing.add`. Use `kept(q)` in `editing.py`.
- **Size:** small.
- **Risk:** low. Protected by `tests/test_editing.py` and `tests/test_pipeline.py`.
- **Verified:** yes.

#### Q4. The sixth criterion has no wording, and the wording of the other five is kept twice

- **Where:** `outputs.py:32-38` (`CRITERIA_WORDS`), `web/static/index.html:366-372` (`CRITERIA`), `prompts.py:271-273`, `prompts.py:429-430`, `steer.py:193`, `pipeline.py:13`.
- **What is wrong:** A run that allows multiple choice, true or false or a blank has a sixth criterion, `one_right_answer` (`prompts.py:218-220`). Neither table of plain wordings has it. The report falls back to the raw name (`CRITERIA_WORDS.get(k, k)` at `outputs.py:273`, which then prints "One_right_answer."), and so does the page (`CRITERIA[k] || k` at `index.html:1894`). The two tables also word the same five criteria differently ("the answer says something its section does not" and "answer says something its section does not"). `prompts.py:429-430` adds the sixth criterion by hand where `criteria(settings)` exists for that. `steer.checker_rules` tells the checker "The five criteria are unchanged" also when there are six. The docstring of `pipeline.py` says "five criteria".
- **Suggestion:** Keep the wordings in one place, beside `CRITERIA` in `prompts.py`, with a sixth entry such as "more than one answer is right, or the marked answer is not". Use it in `outputs.py`. Send it to the page in `GET /api/info`, as `densities` and `limits` are sent, and delete the table in `index.html`. In `prompts.py:429-430`, loop over `criteria(settings)`. In `steer.py:193`, say "The criteria are unchanged".
- **Size:** small.
- **Risk:** low. `tests/test_steer.py:86` checks the checker's rules and may compare the sentence; `tests/test_forms.py` covers the sixth criterion.
- **Verified:** yes, by reading. I did not build a report to see the raw name printed.

#### Q5. Three rules live in both Python and JavaScript, and one has drifted

- **Where:**
  - A question as plain text: `forms.py:189-199` (`as_text`) and `web/static/forms.js:52-56` (`plain`). The Python one leaves the answer alone when a true-or-false explanation already opens with "True" or "False" (`forms.py:197`). The JavaScript one does not have this case, so the same question reads "True. True, because ..." on the page and "True, because ..." in the files.
  - The sections and the outline of a project's file: `vault.py:396-453` (`section`, `add_under`, `outline`) and `web/static/notes.js:31-66` (`sectionOf`, `outlineOf`, `withOutline`, `gatheredOf`).
  - The names of the kinds of note: `notes.js:21` and `editor/src/index.js:362`. See P5.
- **What is wrong:** The pages need these rules without a round trip, so two copies are a fair price of the "no framework, no build step" decision. But nothing holds the copies together.
- **Suggestion:** Add the missing case to `forms.js`. Then add one file of cases, `tests/data/shared/forms.json` and `outline.json` (a question or a text, and the expected result), and run it twice: a Python test feeds it to `forms.as_text` and `vault.outline`, and a browser test feeds the same file to `QForms.plain` and to the outline functions of `notes.js` with `page.evaluate`. `outlineOf` is not on `window.Notes` today; it would have to be.
- **Size:** small for the fix; medium with the shared cases.
- **Risk:** low.
- **Verified:** yes for the drift, by reading both functions. I did not run a page to see the doubled word.

### 2.3 The notes folder

#### N1. `vault.py` is three layers in one file; the properties reader is the clear seam

- **Where:** `ebook_processor/vault.py` (1,450 lines). Lines 140-343: the properties at the top of a file (`Prop`, `split`, `parse_props`, `compose`, `prop`, `set_prop`, `clean_tags` and their helpers). Lines 345-610: a note's text (`mask_code`, `terms`, `section`, `outline`, `read_text`). Lines 617-1435: the class `Vault`.
- **What is wrong:** I judged the file as asked. The class `Vault` is long (about 820 lines) but it does one job, an index of files with reads and writes, and its section comments are right. I would not split the class. The first 200 lines are another matter. They are a small reader and writer of a YAML subset, made of pure functions, and other modules need them without needing a folder: `sources/from_markdown.py:13` imports the whole of `vault` to read a Markdown file's properties, and `bookfile.py:36` imports it for `Prop` and `compose`. `docs/SOURCES-PLAN.md` section 6.5 chose this reuse on purpose, and it was the right call over a second reader. Its cost is the direction of the import: a converter now depends on the module of the notes folder, and so does every test of a converter.
- **Suggestion:** Move lines 140-343 to `ebook_processor/frontmatter.py`. `vault.py` imports the names back, so nothing else has to change at once; then `from_markdown.py` and `bookfile.py` import from the new module. A second, smaller step: move `mask_code`, `unescape`, `section`, `add_under`, `outline` and the regular expressions (N2) to `ebook_processor/notetext.py`, since `assist.py` and `bookfile.py` use them too.
- **Size:** medium (small for the first step alone).
- **Risk:** low: the functions are pure and `tests/test_vault.py` tests them one by one. `tests/test_vault.py:400-403` calls `vault._inside`; that stays.
- **Verified:** yes.

#### N2. How a link is written, and what a link looks like, are each stated many times

- **Where:**
  - "The words that link to a note: its title, or its path when two notes share the title": `vault.py:844`, `981`, `1074`, `1088`; `assist.py:146`, `171`. Six times the same expression.
  - The pattern of a `[[link]]`: `vault.py:348` (`LINK_RE`), `vault.py:133` (inside `inbox_name`), `sources/from_markdown.py:15` (`WIKI`), `web/static/notes.js:71`, `editor/src/index.js:22` and `104`.
  - The pattern of a block id: `vault.py:351` and `sources/from_markdown.py:16`. The pattern of a heading: `vault.py:350` and `bookfile.py:66`. "A block id that ends in `-note` belongs to its highlight": `vault.py:791` and `bookfile.py:136`.
  - `assist.py:294` calls `vault._words`, a private method.
- **Suggestion:** Add `Vault.link_to(note)` and use it in the six places. In Python, keep the patterns in one module (the `notetext.py` of N1) and import them in `from_markdown.py` and `bookfile.py`. Add `entry_of(block)` for the `-note` rule. Make `Vault.words` public. The two copies in JavaScript stay; note beside each that it follows `LINK_RE`.
- **Size:** small.
- **Risk:** low. Protected by `tests/test_vault.py`, `test_bookfile.py`, `test_assist.py` and `tests/sources/test_convert.py`.
- **Verified:** yes.

#### N3. `Vault.put_book` takes a function where a text would do

- **Where:** `vault.py:1371-1395`, `bookfile.py:77-116`, `web/server.py:814-815` and `842`.
- **What is wrong:** `put_book(book_id, title, write, kind)` calls `write(name)` to get the file's text "for the file name it has or is given". But `bookfile.text(name, ...)` never uses `name` (found with a script, then read), and the other caller is `lambda name: bookfile.mark_removed(current)`, which ignores it too. The file name does not appear in the text. This is a leftover of an earlier design, and its docstrings still describe that design (`bookfile.py:79`: "`name` is the file's name without `.md`").
- **Suggestion:** `put_book(book_id, title, text, kind)` with the text itself. Drop `name` from `bookfile.text`.
- **Size:** small.
- **Risk:** low. `tests/test_bookfile.py` and `tests/test_vault.py` call both and must follow.
- **Verified:** yes.

### 2.4 The pages

#### P1. Five copies of the element builder and five of the API call, each a little different

- **Where:** The element builder: `index.html:340` (`h`), `reader.html:227` (`h`), `settings.js:34` (`el`), `forms.js:35` (`el`), `notes.js:73` (`el`). The API call: `index.html:350`, `reader.html:238`, `settings.js:44`, `forms.js:204` (all `api`), `notes.js:83` (`call`). A notice at the edge of the window: `reader.html:1072` (`say`) and `notes.js:199` (`toast`), both loaded by the reader.
- **What is wrong:** The copies are not the same, so the same call does different things on different pages:

  | Copy | Set as a property, not as an attribute | Error object carries |
  | --- | --- | --- |
  | `index.html` | `checked`, `disabled` | `status` |
  | `reader.html` | `disabled`, `value`; and `html` sets `innerHTML` | nothing |
  | `settings.js` | `checked`, `disabled`, `value` | nothing |
  | `forms.js` | `disabled`, `value` | nothing |
  | `notes.js` | `checked`, `disabled`, `value`, `hidden`, `readOnly` | `status`, `data` |

  So `h("input", { value: x })` sets the field's value in the reader and only an attribute in `index.html`. Only two of the five let the caller add headers. A fix to one copy does not reach the others.
- **Suggestion:** `settings.js` is the first script of both pages. Either put the three helpers there, or add a small `base.js` before it, and publish them as `window.App = { h, api, toast }`. The shared `h` takes the union of the property names; the shared `api` always carries `status` and `data` on its error and always takes extra headers. Each script then starts with `const { h, api } = App;` and its own copy goes. A new file must be added to `PAGES` in `server.py:56` and to the list in `tests/test_reading.py:213`.
- **Size:** medium. The change is mechanical, but each page must be checked for the attribute and property difference.
- **Risk:** low to medium. Protected by the browser tests in three engines, which fail on any script error (`tests/browser/conftest.py:99-105`). They cannot be run without Playwright.
- **Verified:** yes, by reading the ten functions.

#### P2. The script of `index.html` holds thirteen views in one file

- **Where:** `ebook_processor/web/static/index.html:337-1932`.
- **What is wrong:** One script of about 1,600 lines, many of them 150 to 400 characters long. Each view is one function that holds all of its state and its drawing as inner functions: `showAdd` (457, 185 lines), `showLibrary` (645), `showNotebook` (743, 162 lines), `showNotes` (1003, 112 lines), `showAsk` (1118), `showGraph` (1165, 124 lines), `showRevisit` (1289), `showTimeline` (1300), `showProcess` (1325, 143 lines), `showNote` (1468), `showTrash` (1495), `showPractice` (1515, 96 lines), `showRun` (1618, 313 lines). The views do not call each other. They share a dozen small helpers (`h`, `api`, `put`, `fmt`, `kv`, `band`, `day`, `md`, `estimateText`, `chapterSelect`) and about ten variables at the top level that `route()` resets (`timer`, `view`, `addForm`, `nbReload`, `practiceReload`, `notesReload`, `noteView`, `viewStop`, `nbShow`, `nbTidy`).
  Inside the views there are repeats: the passage of a practice card is built by the same long expression at 1561 and 1589; the line about a card's standing at 1560 and 1587; the two switches that rebuild the files at 1839-1842 and 1845-1848; the row of four number tiles at 1752, 1789 and 1828. The script has 88 inline `style:` strings, so part of the look is in the script and part in the style sheet above it.
- **Suggestion:** The seam is the view. Without a build step, the split is into plain scripts that the page loads in order:
  - `app.js`: the helpers, `route()` and a list that maps a pattern of the address to a view. Each view registers itself with `App.view(pattern, show, stop)`. The ten shared variables become what `stop` cleans up, kept inside each view's file.
  - `library.js` (`showLibrary`, `showAdd`), `run.js` (`showRun`), `notebook.js` (`showNotebook` and the reader beside it, lines 907-972), `practice.js`, `notes-area.js` (`showNotes`, `showNote`, `showTrash`, `showProcess`, `showAsk`), `notes-views.js` (`showGraph`, `showRevisit`, `showTimeline`).
  `index.html` keeps the frame and the list of scripts. Do P1 first: it gives the shared helpers a home. Give the repeated blocks names when a view is moved (`passageOf(card)`, `statTiles(pairs)`, `rebuildSwitch(label, key, values)`), and move inline styles that occur more than once into classes.
- **Size:** large. One view at a time is possible: a view that has moved to its own file works beside those that have not.
- **Risk:** medium. What the split costs: each new file must be listed in `PAGES` (`server.py:56`), which also means the server reads it at start, and in `tests/test_reading.py:213`. `pyproject.toml` already packages `web/static/*`. The browser tests reach into the pages' top-level names (`page.evaluate` and `wait_for_function` are used 64 times in `tests/browser/`); a name they use must stay global. More files mean more requests at page load, which on a local server does not matter.
- **Verified:** yes for the sizes and the repeats. That the views do not call each other I checked by reading the function list and the calls of `show*`; `showRun` calls itself once (1642) and `route` calls them all.

#### P3. The two pages each carry the same base styles

- **Where:** `index.html:9-18` and `reader.html:8-18` (the colours, the two type stacks and the dark theme, with the dark values written twice in each page, so four times in all); `index.html:27-29` and `reader.html:25-26` (`.m`, `.mute`, `.raw`); the rules for `button`, `a` and `:focus-visible` in both. `notes.js`, `settings.js` and `forms.js` each add a block of CSS from a string (for example `notes.js:95`).
- **What is wrong:** A change of a colour is made in four places, in two files. The scripts' own CSS strings use the same variables and cannot be checked by an editor.
- **Suggestion:** One file `web/static/base.css` with the variables, the dark values in that one file only, and the rules both pages have. Both pages link it. Add it to `PAGES` with the content type `text/css`. The CSS strings of the three scripts can stay where they are; they belong to their parts.
- **Size:** medium.
- **Risk:** low to medium: a page must not flash unstyled, so the link stands in the head before the scripts, as `settings.js` does today for the theme. Protected only by eye and by the browser tests' layout checks; there are no picture comparisons.
- **Verified:** yes.

#### P4. A highlight's questions are drawn and handled twice

- **Where:** The notebook: `index.html:799-819` (`asked`), `821-830` (`another`), `831-835` (`wording`), `836-841` (`removeHighlight`), `761-765` (`watch`). The reader: `reader.html:1110-1150` (`questionPanel`, with `one`, `form` and the wish field inside), `1024-1029` (`removeHighlight`), `1085-1100` (`takeQuestions`).
- **What is wrong:** Both offer the same actions on a question made from a highlight: Accept, Reword, Remove or Discard, Another with a wish, Write my own. Both ask the same two questions before a highlight is removed, with the same sentences (`index.html:838-839` and `reader.html:1026-1027`). Both look again every 1,500 ms while a question is being written. The markup differs because the two pages have different classes. `DEFERRED.md` lists a browser test for "a question from a highlight edited in the reader's own panel" as not yet written, so the reader's copy is the less tested one.
- **Suggestion:** `forms.js` already holds what every page shares about questions (`QForms.edit`, `QForms.compose`, `QForms.openEditor`). Add `QForms.highlightCard(q, { onAccept, onReword, onRemove, onAnother })` for one question, `QForms.wish(onSend, onCancel)` for the wish field, and `QForms.confirmRemoveHighlight(highlight, kept)` for the two questions. Each page keeps its own frame and its own calls to the API.
- **Size:** medium.
- **Risk:** medium: the two pages style the card differently, so the shared card needs class names both style sheets know. Protected by `tests/browser/test_questions.py`, `test_notes.py` and `test_steer.py`.
- **Verified:** yes.

#### P5. Lists that the page and the server must both know

- **Where and what:**
  - The file endings the app takes: `sources/__init__.py:25` (`FORMATS`), `index.html:450` (`ACCEPTED_FILES`) and `index.html:453` (a pattern inside `isAddress`). A new format means three edits, one of them inside a regular expression.
  - The names of the kinds of note: `notes.js:21` has `article`; `editor/src/index.js:362` does not. When the pointer rests on a link to an article, the editor's box therefore shows the raw word "article" (`index.js:395`: `KINDS[got.kind] || got.kind`).
  - The options of a run: `server.py:1978-1979` (`allowed`) and `settings.js:26` (`RUN_KEYS`).
- **Suggestion:** Send the endings and the run's option names in `GET /api/info` (it already sends `densities`, `defaults` and `limits`), and build `ACCEPTED_FILES` and the pattern from them. Add `article` to the editor's `KINDS` and build `editor.js` again; or let the page hand the names to the editor when it creates it, so that there is one list.
- **Size:** small. The editor change needs Node for the build.
- **Risk:** low.
- **Verified:** yes for the lists. The raw word in the box I read from the code and did not see in a browser.

#### P6. `Notes.mount` is one function of 475 lines

- **Where:** `ebook_processor/web/static/notes.js:300-774`.
- **What is wrong:** `mount` draws a note's page and holds everything it does as inner functions: the title and properties, saving and the "changed on disk" question, the editor, the sources, links from other notes, related notes, the project page with its outline, the model's connections, the note's questions, the neighbourhood. They share about fifteen variables declared at its top (lines 302-304). It is the same shape as the views of P2, with the same cost: nothing inside can be read or tested alone.
- **Suggestion:** Keep `mount` as the part that owns the note and its saving. Move each side panel out to a function at the top level of `notes.js` that gets what it needs as arguments and returns its element: `projectPanel(note, editor, redraw)`, `relatedPanel(...)`, `connectionsPanel(...)`, `questionsPanel(...)`. The outline functions at lines 31-70 are already written that way.
- **Size:** medium.
- **Risk:** medium: saving has several timers and flags (`dirty`, `saving`, `again`, `closed`, `dead`), and the panels must not hold stale copies of them. Protected by `tests/browser/test_knowledge.py` (14 tests).
- **Verified:** the size, yes. That the panels can be cut loose cleanly is my judgement from reading; I did not try it.

### 2.5 Shared tools and module edges

#### C1. `pipeline.py` is also the tool box, so everything imports the run

- **Where:** `pipeline.py:69-93` (`now`, `write_json`, `clean_text`). Four modules import `pipeline` for `now` alone: `notes.py:30`, `process.py:23`, `assist.py:37`, `highlight_questions.py:38`. `web/server.py:41`, `outputs.py:22` and `runner.py:13` take `now` and `write_json` from it among other names. `vault.py:76-77` has its own identical `now()`. `sources/__init__.py:123` writes the same time format inline.
  A stored JSON file is loaded by the same ten lines in five places: `notes.py:44-54`, `process.py:30-39`, `assist.py:236-245`, `srs.py:172-181`, `reading.py:380-388`.
- **What is wrong:** `notes.py` needs a time stamp, and for it imports `pipeline`, which imports `prompts`, `llm`, the whole of `prep` and `steer`. The dependency graph says that notes depend on the run, which is not true. The five loaders differ only in the file name and in what an empty store looks like.
- **Suggestion:** A module `ebook_processor/store.py` with `now()`, `write_json(path, data)` and `load_json(path, version, empty, valid=None)`. `pipeline.py` keeps importing the first two so that old imports work; the other modules move to `store`. `vault.now` goes. `clean_text` is about questions and can stay in `pipeline.py`, or move to `forms.py`, which has no imports of its own.
- **Size:** medium, mostly mechanical.
- **Risk:** low. Each loader has a slightly different idea of "valid"; keep each one's check as the `valid` argument.
- **Verified:** yes. The import graph was printed with a script.

#### C2. Two imports that point the wrong way, and imports inside functions

- **Where:** `reading.py:29` imports `authors` and `chapter_titles` from `exports.py`. `runner.py:23`, `94`, `104`, `157`, `183` import inside functions (`sources`, `shutil`, `pipeline.steer_key`, `exports.CARD_CHOICES`, `exports.chapter_titles`). `outputs.py:203` and `notes.py:95` do the same.
- **What is wrong:** `authors(book)` and `chapter_titles(book)` (`exports.py:31-47`) are facts about a book. The reader should not need the module that writes Anki decks to learn a chapter's title. The imports inside functions suggest import cycles, but I found only one place where a cycle could arise (`notes.py` and `highlight_questions.py`; and `highlight_questions.py` does not import `notes.py` today). `runner.py:104` imports `steer_key` from `pipeline` inside a function although line 13 imports eleven other names from `pipeline` at the top.
- **Suggestion:** Move `authors` and `chapter_titles` to `prep/epub.py` (they use only `Book`), or to a small `book.py` if `prep/` is to stay as ported. Move the imports to the top of the files, one at a time, and run the tests after each.
- **Size:** small.
- **Risk:** low. If an import at the top does create a cycle, Python says so at once.
- **Verified:** the imports, yes. That none of them is needed for a cycle is a suspicion: I did not move them to try.

#### C3. Private names used from outside their module

- **Where:** found with a script over the syntax trees, then read:
  - `pipeline.py:41` imports `_preflight` from `prep/build.py`.
  - `prep/matter.py:29` imports `_parse_member` and `_resolve` from `prep/epub.py`.
  - `prep/draft.py:115` and `prep/validate.py:920` call `cp._shorten`; `prep/validate.py:401` uses `draft_mod._Resolver`.
  - `sources/split.py:29` and `41` call `C._is_note` and `C._loose`.
  - `assist.py:294` calls `vault._words`; `assist.py:314` calls `hq._clean`.
  - `web/server.py:260` calls `srs._shuffle`; `1341` calls `vault._taken`; the handler calls `jobs._note` and `jobs._vaulted` (S2).
  - Tests: `tests/test_bookfile.py:73` (`bookfile._escape`), `tests/test_codex.py:21` (`outputs._price`), `tests/test_recovery.py:289` (`jobs._event`), `tests/test_vault.py:400` (`vault._inside`).
- **What is wrong:** The underscore says "change me freely". For these names that is not true.
- **Suggestion:** Drop the underscore where the name is in fact shared (`preflight`, `is_note`, `loose`, `shuffle_key`, `words`, `clean`). The four inside `prep/` can stay if that package is to remain close to the code it was ported from; that is a decision for the owner. The uses in tests are fine as they are.
- **Size:** small.
- **Risk:** low.
- **Verified:** yes.

#### C4. Dead code and unused parameters

- **Where:** For each name I counted its uses in all Python files of `ebook_processor/` and `tests/` with a script, and read the result.
  - No use at all: `outputs.OUTPUT_SUFFIXES` (`outputs.py:30`), `prompts.LABEL_MAX_CHARS` (`prompts.py:17`; the limit in use is `prep.limits.MAX_LABEL_CHARS`), `highlight_questions.OPEN` (`highlight_questions.py:47`).
  - Used only by a test of itself: `bookfile.is_removed` (`bookfile.py:129`), `forms.with_choices` (`forms.py:113`).
  - In the ported engine, no use at all: `SpineDoc.block_by_ref` (`prep/epub.py:184`), `Book.all_refs` (`234`), `iter_blocks` (`662`), `extract.OUTPUT_FILES` (`prep/extract.py:26`), `refs.parse_ref` (`prep/refs.py:46`), `validate.STATUSES` (`prep/validate.py:51`), `validate.result_bytes` (`924`), `words.MARKER_KINDS` (`prep/words.py:85`), `Word.flag_names` (`97`).
  - Parameters never read: `bookfile.text(name)` (N3), `content.passage_text(sd)` and `content.clipped(sd)` (`content.py:103`, `110`), `outputs._epubcheck_line(data)` (`outputs.py:49`; it reads the file from disk again), `server._asked(job_id)` and `server._seen(entry)` (S5).
- **Suggestion:** Delete the first two groups. For the engine, decide once: either delete, or keep the package as ported and say so in `prep/__init__.py`. Drop the unused parameters; `passage_text` and `clipped` have three callers between them (`exports.py:114`, `167` and `prompts.py:367`).
- **Size:** small.
- **Risk:** low. The tests that call `is_removed` and `with_choices` go with them or the functions stay.
- **Verified:** yes, with the limit that a name used only through `getattr` or from JavaScript would not be found. None of these is of that kind.

#### C5. Small repeats inside single modules

- **Where and what:**
  - `sources/clean.py:233-244` (`paragraph` inside `_breaks`) and `401-411` (inside `_loose`) build a paragraph from a mixed list of texts and elements in the same way.
  - `visible.py:46-59` (`insertion_point`) and `62-74` (`insertion_point_before`) are the same loop with two different offsets.
  - `cli.py:324-325`, `334` and `424` each work out `split_how` and `whole` from the arguments.
  - `pipeline.py:381-382` and `405-406` write out the empty totals of a role.
  - `notes.py:291` and `bookfile.py:112` both decide that a chapter with one section of the same name shows no section heading.
  - `prompts.py:320`, `highlight_questions.py:268` and `steer.py:292` each write the "BOOK / Title / Language" head of a task.
- **Suggestion:** One helper for each: `clean._paragraph(group)`, `visible._insertion(block, before)`, `cli._how(args)`, `pipeline._totals()`, `notes.single_section(chapter, section)`, `prompts.book_line(book)`.
- **Size:** small.
- **Risk:** low. The texts sent to a model must stay the same to the letter (see Q1).
- **Verified:** yes.

### 2.6 The tests

#### T1. The fixture that starts the app is written eleven times, and the wait for a run eight times

- **Where:** A fixture named `app` that builds a server or a `Jobs` object, wraps `urllib` in a `call(method, path, ...)` function and shuts down: `tests/test_editing.py:269`, `test_forms.py:312`, `test_highlight_questions.py:192`, `test_notes.py:113`, `test_reading.py:156`, `test_srs.py:130`, `test_steer.py:297`, `test_update.py:41`, `test_vault_web.py:20`, `test_visible_and_web.py:213`, `tests/sources/test_sources_web.py:25`. A twelfth is `App.call` in `tests/browser/conftest.py:51`.
  The loop "ask for the run until it is no longer queued or running, with a sleep of 0.05 s" stands at `test_forms.py:340`, `test_notes.py:163`, `test_highlight_questions.py:196`, `test_editing.py:288`, `test_visible_and_web.py:239`, `test_steer.py:329`, `tests/sources/test_sources_web.py:42` and `tests/browser/conftest.py:73`. Three more loops of the same shape wait for a question from a highlight: `test_forms.py:387`, `test_steer.py:449`, `test_highlight_questions.py:205`.
  Small helpers are copied too: `state_of` in four files (three identical), `book` in three (identical), `run_prep` in `tests/prep/prepkit.py:103` and again in `tests/prep/test_extract_build.py:34`.
- **What is wrong:** The eleven `call` functions return different things: `(status, data)`, `(status, data, headers)`, `(call, clock)`, or a function with attributes set on it (`call.jobs`, `call.folder`, `call.book`). `test_vault_web.py` takes `json_body` before `body`; `test_notes.py`, `test_reading.py` and `test_srs.py` take `body` first. Some paths start with `/api` and some do not, by file. Reading a test means first finding out which `app` this file has. A change to the CSRF header or to how the server is started is eleven edits.
- **Suggestion:** In `tests/conftest.py`, one class and two fixtures:
  - `class WebApp`: `url`, `jobs` (the `Jobs` object), `data` (the folder), `call(method, path, *, body=None, json=None, csrf=True)` returning an object with `status`, `data`, `headers`; `wait(job_id)`; `import_fixture()`; `process_fixture(**settings)`.
  - `web_app` (per test) and a factory `make_web_app(backend_factory=None, today=None, ...)` for the files that need a stand-in model or a fixed date.
  The browser `App` class can build on the same `WebApp`.
- **Size:** medium. The files can move one at a time.
- **Risk:** low: a mistake shows as a failing test at once.
- **Verified:** yes. The definitions were found with a script over the test files and four of them were read side by side.

#### T2. Test files import constants, helpers and fixtures from each other

- **Where:** `from tests.test_pipeline import FIXTURE` in fifteen files; `from tests.test_reading import PREPARED` in four, while `test_highlight_questions.py:23` defines its own `PREPARED`; `from tests.test_vault import BOOK` (`test_assist.py:13`); `from tests.sources.test_convert import CLUTTER, FILL, PNG, the_web  # noqa ... the_web is a fixture` (`tests/sources/test_sources_web.py:179`, `tests/browser/test_sources.py:138`); `from tests.browser.test_forms import ASKED, next_checkpoint, shelf  # noqa` (`tests/browser/test_editing.py:12`); `from tests.browser.test_notes import highlight` or `SELECT` in five browser files. `tests/sources/test_sources_web.py:246` reaches into another test module's dictionary with `__import__`.
- **What is wrong:** A test file is not a library. Importing one runs everything at its top level, and pytest rewrites and collects it in its own way. A fixture brought in by import needs a `noqa` and is easy to remove by mistake as "unused". Moving or renaming a test file breaks others.
- **Suggestion:** `tests/paths.py` for `FIXTURE`, `PREPARED` and the other data paths. Fixtures that several files use (`the_web`, `shelf`) go to the `conftest.py` of their folder. Helpers (`highlight`, `SELECT`, `next_checkpoint`, `MARKDOWN`, `PAGE`, `body`) go to the helper modules that already exist for this: `tests/sources/makers.py`, `tests/prep/prepkit.py`, and a new `tests/browser/helpers.py`.
- **Size:** small to medium.
- **Risk:** low.
- **Verified:** yes.

#### T3. Twenty-one tests take 60% of the time, and they can reach the web

- **Where:** `tests/prep/test_validate.py:461` and `472` (the parametrised tests over the broken books), the `test_cli_*` tests of the same file, `tests/prep/test_extract_build.py` (two tests with "passes_epubcheck" in their names), `tests/prep/prepkit.py:55-59` (`real_epubcheck`), `tests/conftest.py:16`.
- **What is wrong:** I measured the suite with `--durations=30`. The 21 slowest tests are all of this group, at 4.5 to 14.6 seconds each, together about 129 of 210 seconds. Each starts Java to run the real EPUBCheck. Ten of them are one parametrised test over the broken books, of which only those whose fault is one that EPUBCheck finds need it. Where Java is missing, the tests that use `real_epubcheck` skip themselves and say so. Where Java is there but EPUBCheck is not yet in the cache, `run_epubcheck` downloads it (`prep/epubcheck.py:151-154` with `download=True`). The docstring in `tests/conftest.py:16` says "No test reaches the web"; the guard below it covers `sources.fetch` only.
- **Suggestion:** Register a marker `epubcheck` in `pyproject.toml` and put it on these tests, so that `pytest -m "not epubcheck"` is a suite of about 80 seconds for daily work, and CI runs everything. In `test_negative_fixture_fails_exactly_its_checks`, use `real_epubcheck` only for the entries whose expected failing check is `epubcheck`, and the stand-in `ok_epubcheck` for the rest. Make `real_epubcheck` call `run_epubcheck(path, download=False)` unless an environment variable allows the download, and correct the docstring.
- **Size:** small.
- **Risk:** low. Check in the manifest (`tests/data/books/negative/manifest.json`) that no entry expects EPUBCheck to report something as a side effect.
- **Verified:** the times, yes (measured here, with Java installed). Which entries need the real EPUBCheck I did not work out.

#### T4. What has no test of its own

- **Where and what:**
  - `POST /api/jobs/<id>/cancel`, the Stop button of a run (`server.py:1783-1789`, `2331`). I searched all tests, also the browser tests, for `/cancel`, `.cancel(` and a click on "Stop" and found none. The stop of a single call is tested in `tests/test_recovery.py`, and "Stop" for questions from highlights in `tests/test_highlight_questions.py`; the route and `Jobs.cancel` are not.
  - `POST /api/vault/cite` and `GET /api/vault/projects` are not called by any Python test. The browser tests reach the first through the reader's "Idea note" (`tests/browser/test_knowledge.py:289`, `314`, `320`); whether they reach the second I did not check. With `GET /api/vault/tags` (S5), which nothing calls, these are the four routes without a test over HTTP in the Python suite. I checked all 92 routes with a script and the doubtful ones by hand.
  - `process.py`, `content.py`, `exports.py`, `outputs.py`, `settings.py` and `runner.py` have no test file of their own. All are exercised through the run and the HTTP tests, so this is not a gap in what is covered; it is a gap in where a failure points. A wrong line in `settings.from_options` shows as a failed run in `test_pipeline.py`.
  - The command line is tested in six files: `test_forms.py:425`, `test_codex.py:296`, `test_pipeline.py:398`, `test_steer.py:469`, `test_update.py`, `tests/sources/test_sources_web.py:156`.
  - `tests/test_visible_and_web.py` holds six subjects: the book with visible checkpoints, the content modes, the instructions, Claude Code's command line, reading JSON out of a reply, and the web app's run. Its name covers two of them.
- **Suggestion:** Add a test that starts a run with a stand-in that waits, calls `/cancel`, and sees the status "stopped" and then "done" after `/continue`. Add `tests/test_settings.py` with the dozen cases of `from_options` and `validate`; it is ten minutes of work and makes failures point at the right file. When T1 is done, move the command-line tests into `tests/test_cli.py` and split `test_visible_and_web.py` into `test_visible.py`, `test_claude_code.py` (beside `test_codex.py`) and the HTTP part.
- **Size:** small for the two new tests; medium for the regrouping.
- **Risk:** low.
- **Verified:** yes for the searches.

#### T5. Three things the checks do not check

- **Where:** `.github/workflows/tests.yml`, `pyproject.toml`, `editor/`, `tests/prep/fixture/generate.py`.
- **What is wrong:**
  - The code carries `# noqa` marks (`E731`, `N802`, `BLE001`, `E402`, `F401`) and type hints throughout, but there is no configuration for a linter or a type checker, and CI runs neither. The marks are therefore comments for a tool that is not run.
  - `editor.js` is built from `editor/src/index.js` and committed. Nothing checks that the committed file is the build of the committed source. A change to the source without a build passes every check. (The article gap of P5 is a case where the source itself lagged; a stale build would look the same from outside.)
  - `tests/prep/fixture/generate.py --check` says whether the committed fixture book is what the generator writes. No test and no CI step runs it; I searched for `--check` and for a call of the generator's `main`.
- **Suggestion:** Add a `[tool.ruff]` section with the line length the code uses (about 150) and the rules the marks refer to, and one CI step `ruff check`. Add a test that runs the generator's check in a temporary folder. For the editor, add a CI job with Node that runs `npm ci && npm run build` and fails when `git diff --exit-code ebook_processor/web/static/editor.js` shows a change.
- **Size:** small each. The first run of a linter may list many lines to settle.
- **Risk:** low.
- **Verified:** yes for what is absent. I did not run a linter, since none is installed here and nothing may be installed.

### 2.7 The documents

#### D1. The README's module table leaves out seven modules

- **Where:** `README.md:515-536` ("For developers").
- **What is wrong:** The table has no row for `cli.py`, `runner.py`, `settings.py`, `steer.py`, `forms.py`, `editing.py` or `content.py`. Three of them are whole features with their own README sections ("What you want from a book", "Kinds of question", "Editing a question, and adding your own"). `runner.py` is where the command line and the web app meet. In `sources/`, `chars.py` is not named. The row for `sources/` says "`fetch_page()` and `convert()` are all the rest of the app calls"; the server and the command line also use `is_source`, `tidy`, `TYPES`, `Made`, `Page` and `SourceError`.
- **Suggestion:** Add the seven rows, one line each. Reword the `sources/` row to "the rest of the app calls `fetch_page()` and `convert()`, and asks `is_source()` whether a file is one of these".
- **Size:** small.
- **Risk:** none.
- **Verified:** yes.

#### D2. The ported engine's docstrings name files that are not in this repository

- **Where:**
  - `ebook_processor/prep/README.md` is named in `prep/checkpoints.py:17`, `prep/draft.py:14`, `prep/epub.py:16`, `prep/validate.py:9` and `514`. There is no such file.
  - `docs/PLAN.md` and `docs/spikes/S1-word-stream.md` are named in `prep/checkpoints.py:1`, `prep/validate.py:7`, `prep/words.py:4` and `394`, `prep/version.py:5`, `prep/entities.py:6`, both schema files in `prep/schema/`, and in `tests/prep/`. `docs/` here holds three other files. `reading.py:253` names the same plan but says "of eink-learner-aid", which is the right way.
  - `prep/words.py:8` and `prep/version.py:7` name `fixtures/words/...` and `fixtures/books/...`; the files are under `tests/data/` here. `prep/words.py:8` names `tests/test_words.py`; it is `tests/prep/test_words.py`.
  - `prep/entities.py:9` says a test "re-reads the C++ file and fails if the two drift apart". The C++ file is in the other repository, and `tests/prep/test_words.py` has no such test.
  - `tests/prep/fixture/generate.py:2`, `13`, `22`, `27` name `fixtures/books/`, `tests/prep/schema/draft.schema.json`, `tests/prep/words.py` and `tests/test_fixture.py`. None exists. `tests/prep/fixture/content.py:5` tells the reader to run `python3 tools/prep/fixture/generate.py`.
- **What is wrong:** The engine is the part with the strictest rules, and a newcomer who follows its references finds nothing. One of the references promises a test that does not run here.
- **Suggestion:** One pass over `prep/` and `tests/prep/`. Where the reference is to the other repository, say so ("`docs/PLAN.md` of eink-learner-aid"). Where the file has moved, give its place here. Either bring the engine's README over as `ebook_processor/prep/README.md` (the five references then hold), or point the five at the README of the other repository. Correct the sentence in `entities.py`. If `prep/` must stay byte-close to its origin for later comparison, do the opposite: leave the files alone and add one paragraph to `prep/__init__.py` that maps each old path to its place here.
- **Size:** medium.
- **Risk:** none for the code. `tests/data/` holds generated files that carry some of these paths as text (`fixture.expected.json`); changing the generator's output means generating them again.
- **Verified:** yes. Each named path was looked for.

#### D3. Docstrings and comments that no longer describe the code

- **Where and what:**
  - `web/server.py:1`: "pick an EPUB, watch the run". The app has a library, a reader, practice and notes, and takes six kinds of file.
  - `web/server.py`: the section comments of `Jobs` (see S1).
  - `sources/fetch.py:3`: "and, from step 2, for the page at an address". Step 2 is built.
  - `sources/__init__.py:4`: "Nothing outside this package knows about formats". `index.html:450-453` knows the endings (P5).
  - `pipeline.py:13`: "five criteria" (Q4).
  - `bookfile.py:79` and `vault.py:1372`: the file-name argument that is not used (N3).
  - `ebook_processor/__init__.py:1` and the description in `cli.py:113`: "to an EPUB". `pyproject.toml:8` already says "a book or an article".
  - The version is written twice: `ebook_processor/__init__.py:3` and `pyproject.toml:7`.
  - `docs/KNOWLEDGE-PLAN.md` section 4.2 says the app "has no runtime dependencies beyond `genanki`". That was true when it was written. The plan is a record now, so I would leave it, and add one line at its top that sections 4 to 6 describe the plan as it stood and section 7 and the README describe what was built.
- **Suggestion:** Correct each in place. For the version, let `pyproject.toml` read it: `dynamic = ["version"]` with `[tool.setuptools.dynamic] version = { attr = "ebook_processor.__version__" }`.
- **Size:** small.
- **Risk:** none.
- **Verified:** yes.

## 3. Looked at and found in good order

So that nobody reviews these again for nothing:

- **`prep/`** as a design: reading, numbering, the draft checks, the build and the validator each in their own module, with one direction of imports (`validate` on top, `xmltree` and `entities` at the bottom). The private names it shares inside itself are listed under C3; they are a matter of naming, not of structure.
- **`sources/`**: one door (`convert`, `fetch_page`), one module per format, one place that decides what markup survives (`clean.py`), one that writes the EPUB (`pack.py`). `convert()` is long (135 lines) but reads top to bottom, and each step is one decision of `docs/SOURCES-PLAN.md`. Formats are imported only when used, which is on purpose.
- **`llm.py`**: the two backends share `run_process`, `_with_retries`, `_halter` and `Connection`. What is left double (the fields of the two dataclasses, the shape of `_once`) follows the two tools' real differences.
- **`pipeline.py`** apart from the tool box (C1): the checks of a reply, the state, and the run are three clear parts, and the long functions (`check_part`, `check_loop`) are long because the rules are.
- **`srs.py`, `steer.py`, `forms.py`, `settings.py`, `content.py`, `update.py`, `visible.py`**: each does one thing, has no surprising import, and has tests that read as its specification. `forms.py` imports nothing of the app, which is right for what everything else uses.
- **`reading.py`** apart from one import (C2).
- **`notes.py`, `bookfile.py`, `process.py`**: three walks over one structure (`notes.notebook`), each short. I looked at whether they should share a walker and decided against it: each wants different things from each entry.
- **The class `Vault`** (N1): long, but one job, and its sections are named rightly.
- **`reader.html`**: I judged it as asked. Its script is 1,170 lines, in sections that are named and in order (loading, saving, the three views, checkpoints, highlights and notes, questions, links, the drawer, keys). The three views already share an interface (`view.stop`, `view.refresh`, `view.label`), and no function is longer than about 140 lines (`rsvpView`). Its weak points are its shared state (18 `let` lines at the top level, several with more than one name) and the flag for the side reader, `PANE`, which is read in 28 places. I would leave it as one file for now. P1 and P4 take some lines out of it. If it grows again, the seam is the three view functions.
- **`settings.js` and `forms.js`**: each is one closed part with a documented list of what it offers at its top. Apart from the copied helpers (P1) I found nothing to change.
- **`editor/src/index.js`**: 616 lines, one concern per block (the syntax, the widgets, formatting, links, hover, completion, the theme, `create`). The build script is short and records the versions in the built file. One list has lagged (P5).
- **Error handling in Python**: every module has its own error class with a message for the reader, and the server turns each into an HTTP status in one place per area (`_vaulted`, `_asked`, `_helped`, `_edit`, `notes_do`, `question_do`). The pattern is the same everywhere; the 18 lines `raise ApiError(BAD_REQUEST, str(exc)) from exc` are its visible cost.
- **The stand-ins for the models** (`tests/scripted.py`, `tests/programs.py`): they read the task messages as the app writes them, which makes the tests strict about wording. That is a real constraint on any change to `prompts.py`, and a good one.
- **`tests/prep/prepkit.py`, `tests/prep/bookmaker.py`, `tests/sources/makers.py`**: shared helpers done the right way; T2 suggests more of the same.
- **`DEFERRED.md` against the code**: I checked a sample of its statements (the 40 recent notes of the front page, the four-second look at an open note, `--as` not built, `.webarchive` refused with a hint, the 13 structure checks named in the README) and each held.
- **`pyproject.toml` and the workflow**: the nine jobs the documents speak of are the nine the workflow runs; the packaged data patterns cover every file the code reads at run time (`instructions/*.md`, both `schema/` folders, `web/static/*`).

## 4. Not reviewed, or not judged

- **The browser tests were not run**, as instructed. Every statement about a page comes from reading its source. Where a finding says what a reader would see (Q4, Q5, P5), I say there that I did not see it.
- **`web/static/editor.js`** is a built file of 377 KB. I read its header only.
- **`index.html` lines 974-1514** (the Notes front page, Ask, the graph, Revisit, the timeline, the Process view, a note's page, the trash) I read as an outline and in parts, not line by line. Repeats inside those views may exist that P2 does not list.
- **The CSS** of both pages (about 510 lines) I compared only at the top (P3). I did not look for unused rules; that needs a browser.
- **`prep/`** I read for structure, names and references. I did not review its logic: it is a port with its own tests, and its rules are set by the firmware.
- **The instruction files** (`instructions/*.md`) and the JSON schemas I did not review for content. I only checked that each is read by code or by a test: the eight instruction files and the three schemas of `prep/` by the app, and `schema/questions.schema.json` by four tests.
- **The data under `tests/data/`** I did not open, apart from a few lines of the manifests.
- **No linter or type checker was run**: none is installed, and nothing may be installed. A linter would likely find unused imports that this review does not list.
- **`docs/question-forms.md`** I did not compare with the code line by line.
- **Performance, concurrency, robustness and security** were left to the second review. Two findings touch them and are marked as such: the lock in S1, and the download in T3.
