# Deferred

Things that were discussed and put off, in one place. Nothing here is being worked on. Each item says where it came from, so that it can be picked up without rereading old conversations.

## Decided for later

These were chosen as later work by the project owner. The knowledge system is planned in five phases: see `docs/KNOWLEDGE-PLAN.md`. Phases 1 to 4 (the notes folder, the editor, idea notes and quick notes, search; the Process view and related notes; index notes, projects and more of Markdown in the editor; the graph, the neighbourhood, revisiting and the timeline) are built; phase 5, the model's part, is not.

| Item | What it means | Notes |
| --- | --- | --- |
| Personal knowledge system | Grow the per-book notebook into a full system in the spirit of Zettelkasten: conceptual "permanent notes" that stand apart from any one book, links between notes with back-links, and other features familiar from Obsidian | The first step, a notebook per book for "literature notes", is the current highlights and notes work |
| Notebook across all books | One place for every book's highlights and notes, with search and filtering | Part of the knowledge system above |
| Search over the full text of every book | From the Notes area, search the whole text of every book in the library, not only what was highlighted or noted, with results that open the passage | Asked for by the owner on 2026-11-18 as a later addition. Phase 1 of the knowledge system searches notes and book highlights. The reader already searches inside one book |

## Considered and not chosen for now

Options that came up in the interviews and were passed over. They are not planned; they are listed so the choice does not have to be rediscovered.

### Reading

| Item | What it means |
| --- | --- |
| Reading on a phone, tablet or e-ink browser | The reader was built for a laptop or desktop browser. E-ink in particular needs paged turns only, no animation and large targets |
| Standalone offline reader | A reader with no server that opens a prepared EPUB from the device and keeps progress there |
| Any EPUB without checkpoints | Reading an unprepared EPUB in the app, with a button to process it later |
| State shared with the Picco | Export and import of the firmware's saved-state file, so position and grades move between the app and the device. The app's `reading.json` already follows that format |
| Detailed reading history | A log of every reading session (date, duration, sections, pace) with a per-day view. Today only totals per book are kept |
| Finer grades at checkpoints | Anki-style grades (Again, Hard, Good, Easy) instead of Correct and Incorrect |
| Claude suggests a grade | Claude compares a typed answer with the reference answer and proposes Correct or Incorrect |

### Preparing books

| Item | What it means |
| --- | --- |
| Separate wording for flashcards | A second, standalone wording of each question for export. Instead, every question is written to stand alone |
| Non-English books | The drafting and checking rules assume English |
| Calling the Anthropic API directly | Runs use Claude Code and a Claude plan. `ebook_processor/llm.py` is where an API-key backend would go; it would allow parallel calls and running without Claude Code |
| Retiring the copy in eink-learner-aid | `tools/prep` there was left untouched. Pointing that repository at this one was an option |

### Settings and the reader beside the notebook

- **A place to return to after "Full reader".** Opening the full reader at a looked-up passage moves your place there, as a link into the reader always has. A "back to where I was" was not built.
- **The side reader remembered as open.** It opens when you press Open and is closed again when you leave the notebook.
- **The same side reader on the Practice page**, for "Reread the section". That button still opens the full reader.
- **Settings per book.** Every setting is for the whole app.

### Practice

| Item | What it means |
| --- | --- |
| FSRS | Mochi's alternative scheduler. Only Mochi's default scheduler is implemented |
| Settings per book | Mochi allows schedule settings per deck. Here they are global |
| Retiring cards | Mochi's option to stop showing a card once it reaches the longest interval |

### Highlights and notes

| Item | What it means |
| --- | --- |
| Colours or named kinds of highlight | One kind of highlight was chosen. Named kinds (Key idea, Question, Quote) would let the notebook filter and let question generation treat them differently |
| Export for other tools | A CSV in the layout Readwise imports, and JSON. Markdown export was chosen |
| Links between notes | `[[double-bracket]]` links resolved in the notebook. Notes are Markdown, so this has a format to build on; it belongs with the knowledge system above |
| A summary required at every checkpoint | The reader will offer a section note after a checkpoint; it will not insist on one |

### Questions from highlights

| Item | What it means |
| --- | --- |
| Automatic questions for every highlight | A proposal for each new highlight without asking. Passed over because it uses the plan for highlights that may not be wanted for practice |
| In the book's own checkpoints | Adding these questions to the checkpoints of the exported books and the firmware edition. They are asked in the app's reader only |
| Accepting a whole batch without review | "Propose for all" always leaves the proposals to be decided. The accept-without-asking setting covers single requests made in the reader |
| Its own model setting | These questions use the Add a book defaults |

### Editing questions

| Item | What it means |
| --- | --- |
| Changing a question's kind | Turning a multiple choice into an open question, say. A question keeps its kind; take it out and add one of the other kind |
| Editing an imported book's questions | A book added as a ready-made prepared EPUB has no run to rebuild from. Its questions cannot be edited or taken out |
| The model suggesting a new question for a section | "Add a question" is written by hand. The model rewrites an existing question to a wish; for a new one, highlight the passage and ask there |
| Checking every edit | An edit is checked only when you press "Save and check" |
| Edits collected until "Rebuild files" | Edits and added questions apply at once; only taking questions out is collected |
| Editing a highlight's question from Practice or a checkpoint | Those are edited in the notebook or in the reader's Questions panel. "Edit question" at a checkpoint and in Practice is for the book's own questions |
| Editing from the command line | The app only |

### Kinds of question

| Item | What it means |
| --- | --- |
| A balance or exact shares of the kinds | "Mostly open", or 50% open and 30% multiple choice. The model picks the kind for each idea among those ticked; a wish in "How I like questions" can lean it |
| Several right choices | Multiple choice where you tick all that apply. Always four choices with one right |
| More or fewer choices | Three or five choices |
| More than one blank | A statement with two or three gaps. One gap per statement |
| Blanks answered by picking | Words to choose from in place of typing. Typing with a forgiving match was chosen |
| The book's own sentence as a blank | A sentence of the book with words removed. The statement is the model's own, so that it stands alone |
| Trying again after a wrong answer | A second pick after a wrong one. One try, then the answer |
| Overruling any verdict | "I was right" exists for typed answers only; a lucky guess at multiple choice cannot be marked as a miss |
| Open recall in Practice | Asking a multiple-choice or true-or-false card without its choices when it comes back. Practice asks in the same form |
| Writing your own in the new kinds | "Write my own" on a highlight makes an open question. A proposal of any kind can be edited |
| Choosing the kind when asking for a highlight's question | The model picks among the kinds ticked in Settings; "Another" takes a wish in words |
| Native Anki cloze and graded Anki cards | The deck has ordinary cards for every kind: choices on the front, the right answer on the back |

### What you want from a book

| Item | What it means |
| --- | --- |
| Questions about your own work | "How could this apply to your pricing project?" Passed over: such a question has no answer in the book, so it cannot be checked, graded or practised. Your work decides which ideas of the book are asked about instead |
| Fewer or no questions for parts you care less about | Your wishes can add questions where the book bears on them. They do not remove a section's questions |
| Several named profiles | "Work", "Exam", "Leisure", one picked per book. There is one text about you, which a book can leave out, and one about how you like questions |
| The check judging whether a question follows your wishes | The independent check keeps its five points. It is told your manner only so that it does not remark on it |
| Rewriting a processed book's questions after the purpose changes | A changed purpose guides questions on highlights from then on. Writing the questions of unread sections again, or processing the book again as a second copy, were the alternatives |
| A wish with every request for a question | The box for what should be different comes with "Another". The first question for a highlight is written at one click |
| Trying part of a very long chapter | A trial covers a whole chapter (one document of the EPUB). For a book that is a single long document the trial is the whole book |
| Editing the reading on the command line | There you answer the model's questions and say what to change, and it reads again. Editing the reading's own words is in the app only |
| A wish for the batch of highlight questions | "Propose for all" follows what you want from the book, without a wish of its own |

### Narration

Narration itself is planned and waiting on product research. These parts were passed over when it was scoped.

| Item | What it means |
| --- | --- |
| Listening away from the computer | Audiobook files or a private podcast feed for a phone, or audio files for the Picco |
| Spoken checkpoints | Reading questions and answers aloud, and answering or grading by voice. Narration will pause and hand over to the screen instead |
| Describing tables and pictures | Claude writing a short spoken description of each table, listing and picture. Narration will stop at them and show them, as RSVP does |
| Expressive voices for fiction | Acted dialogue, or different voices for different characters |
| A private local voice as a fallback | A voice running on the user's own machine for books that should not be sent to a service. Local voices are being researched only as the answer to the budget |

### The notes folder (Phase 1 of the knowledge system)

Passed over while Phase 1 was built. The later phases are in `docs/KNOWLEDGE-PLAN.md` and are not repeated here.

| Item | What it means |
| --- | --- |
| Emptying the trash | Deleted notes stay in `.trash` until they are removed in a file manager. The app only lists them and puts them back |
| Moving the notes for you | Pointing the app at another folder never moves or copies files. A "move my notes there" button was left out as too easy to get wrong |
| Index notes and project notes in the app | Done in Phase 3 |
| Embeds, callouts, footnotes, tables and task lists in the editor | Done in Phase 3 |
| An idea note from RSVP | "Idea note" is offered on selected text in the page and scroll views. In RSVP a sentence can be highlighted and noted, and the idea note started from the highlight afterwards |
| Citing a second passage with one click | Done in Phase 2: "Cite" on a related passage, and "Add as a source" in the Process view. From a search result or from the reader a second citation is still typed with `[[Book#^` |
| A list of every note | The front page shows the inbox and the notes worked on last (forty at most); the rest are found by search, tag or book. A full, sortable list was not asked for |
| Checkpoint answers and questions in a book's file | By the owner's choice a book's file holds only highlights and the reader's own notes |
| Aliases for merged highlights | When two highlights become one, the id that goes is not kept as an alias: a link to it shows as broken. This is rare: it needs a citation of a highlight that is later joined to another |
| Watching the folder | The app looks at the files when Notes asks for something, and every four seconds for a note that is open. It does not watch the folder, so a list on screen can be up to one reload old |
| Full YAML | Properties are read for a small subset (one value, a list in brackets, a list of `- item` lines). Anything else is kept as written and shown as text that cannot be changed in the app |

### Processing (Phase 2 of the knowledge system)

| Item | What it means |
| --- | --- |
| Going through items one at a time | A view like Practice, one passage filling the page. The owner chose the list with the editor beside it |
| Processing at the end of a chapter | An offer in the reader to process what was marked in the chapter just finished. Not chosen: Process is reached from the Notes page and from a book's page |
| A count at the top of every page | A standing number of what waits. Not chosen |
| Marking an item done by hand | Processed is read from the notes' links, never stored. An item you dealt with in your head only is set aside |
| Checkpoint answers as items | What you typed at a checkpoint is not offered for processing |
| The reader beside the Process view | "Open in the book" leaves for the reader; Back returns to the same item. Three columns were too many |
| Better relatedness | Related notes are found by shared uncommon words and tags. Word stems, phrases and languages other than English are not handled; "Find connections" with a model is Phase 5 |

### Index notes and projects (Phase 3 of the knowledge system)

| Item | What it means |
| --- | --- |
| "Add to an index" on a note | Not chosen: a note gets into an index only by writing its link there |
| An index as a managed, draggable list | Not chosen: an index note is free text |
| Gathering a passage with a button | "Add to a project" is on a note's page only. A passage is gathered by writing its link in the project's file. Buttons in search, in Process and in the reader were not chosen |
| Gathering from the project's page | A search box on the project page that adds notes and passages was not chosen |
| Several outlines per project | The outline is one nested list in the project's file. A separate outline note per purpose was not chosen |
| Choosing what an export contains | The export always carries the notes' text. An outline-only export was not chosen |
| Projects steering the questions | By the owner's earlier decision a project does not replace "what I am working on" in Settings |
| Editing table cells in place | A table is drawn as a table and edited as its Markdown text. Cell editing with Tab and buttons for rows and columns was not chosen |
| Formatting inside a drawn table or an embed | Cells and embedded text are shown as plain words; links in them are not clickable there |
| Embedded pictures | `![[file.png]]` and `![](...)` are kept as written and not drawn |
| Kinds of callout | Every callout has the same look, with its kind as a label. Colours and icons per kind, and folding, were left out |
| Footnotes at the foot | A footnote shows as a raised mark with its text on hover. Numbering them and listing them under the note is left to an export |

### Seeing and revisiting (Phase 4 of the knowledge system)

| Item | What it means |
| --- | --- |
| A graph you lay out yourself | Points can be dragged, but the layout is worked out again on each visit. Keeping where you put things was not chosen |
| Tags as points of the graph | Not chosen: tags are a filter |
| The neighbourhood always beside a note | Not chosen: it opens on request |
| "Not seen for long" | A note is old by the date of its file's last change. Recording when a note was last opened in the app was not chosen, so a note you only read can come back |
| Notes nothing links to | Only a note with no link in either direction counts as unlinked. Notes that link out but cannot be reached were not included |
| A history of changes | The timeline shows a note on the day it was made and on the day of its last change. Earlier changes are not known: the files keep one date |
| A quick note's day after it became an idea note | It then shows on its day as a note written, not as a quick note: the file no longer says it was one |
| Reading on the timeline | Reading sessions are not there: the app keeps totals per book, not a log by day (see "Detailed reading history" above) |
| The graph for the keyboard | The canvas can be moved and zoomed with the keys, but a point cannot be chosen with them. The list under the graph and the neighbourhood diagram, whose points are links, are the keyboard's way in |

## To verify

Things that work in tests but have not been confirmed in real use, and facts that are still unknown.

| Item | What is missing |
| --- | --- |
| The notes folder in Obsidian | The folder is written to be an Obsidian vault: file names as titles, `[[links]]` by name, block ids, YAML properties, `.trash`. Obsidian itself has not opened one, because it is not available where the app is built. To check: open the folder as a vault, follow a `Source: [[Book#^id]]` link to its highlight (a highlight is written as `> words ^id`; if Obsidian does not find an id at the end of a quoted line, the id belongs on a line of its own after the quote, a form the app already reads), edit a note there and see it change in the app, rename a note in the app and see Obsidian's links follow |
| The notes folder on Windows and macOS | File names, letter case and renaming are tested, and GitHub runs the tests on both systems. Not seen in real use: a note open in another program while the app saves it (Windows refuses, and the app says so), and a folder inside OneDrive or iCloud |
| The editor on a phone or tablet | CodeMirror supports touch, but the Notes pages were laid out and tested for a laptop or desktop browser |
| More of Markdown, compared with Obsidian | Tables, embeds, callouts, footnotes and tasks were written to match how Obsidian reads them, and were not compared side by side with it. In a table, a bar inside a link must be written `\\|`, as Obsidian also asks |
| Dragging in the outline | Tested with the buttons. Dragging uses the browser's own drag and drop and was tried by hand in Chromium only |
| The graph with many notes | The layout was tried with a few dozen points. It is written to stay quick with a few thousand (points only push those near them), but that was not measured, and how readable a large graph is will show with a real folder |
| Related notes on a real folder | The scoring was tried on a handful of made-up notes. Whether the list is useful, and how many everyday words slip through, will show only with a real folder of some size |
| A large notes folder | The index is kept in memory and files are read again when they change. Measured only with a handful of notes; the design expects a few thousand to be read in under a second |
| Mochi's actual defaults | Mochi does not publish its multipliers. Reading them off the Review Settings screen of a fresh install would settle the Remembered multiplier (1.8 here), the Forgot multiplier (0.5), the longest interval and the size of the due-date variation |
| Revision with real Claude | In the real runs no question failed its check, so the revise-and-recheck path has only run against the scripted stand-in |
| Recovery from a lost connection | Tested with stand-in programs that hang and a stand-in network, for both tools, and one real call through the new code path. A real loss of connection during a real run was not produced: what Claude Code and Codex themselves do then (hang, or fail with which message) was not observed |
| Questions from highlights with a real model | Built and tested against a stand-in that writes a fixed kind of question. What a real model writes from the instructions, how long a request takes, and how often the check holds a question back have not been seen |
| What you want from a book, with a real model | One real reading and one steered trial chapter (2,563 words, limit 8) were made: the reading was sensible, said that multiple choice cannot be written, and the 17 questions followed the purpose and manner and all passed the check. Two unsteered trials of the same chapter gave 10 questions at a limit of 8 and 7 at a limit of 3. Not seen: a whole steered book, the reading being corrected over several rounds by a real model, Codex as the reader, and how well the estimate holds for fiction and technical books (it was fitted to two expository books) |
| Kinds of question with a real model | One real run of the short test book with all four kinds (8 questions: one multiple choice, two true or false, one blank, four open, all passing the check on six points) and three real questions on a highlight, one of each new kind. Not seen: a whole real book, how often the check fails a key, how the model spreads the kinds over many sections, and Codex with the longer reply shape |
| Kinds of question on the Picco | The firmware-format book carries the forms in `x_form` (`docs/question-forms.md`) and passes its structure checks. No firmware reads `x_form` yet: today's firmware shows these questions as open ones, which was checked only by reading its rule that unknown members are ignored |
| Editing with a real model | One real rewrite of a multiple-choice question (new, closer wrong choices), one of an open question (narrowed to one idea as asked), a check of an edit with an invented detail (it objected, and named the detail and the paragraph that contradicts it) and one choice of passage, 2 to 10 seconds each. Not seen: the same with Codex, and saving edits to a long book (a book of 50,000 words takes about two seconds to write again) |
| Usage-limit handling | A run recognises the plan's usage limit by the wording of Claude Code's error. No real limit message has been seen |
| Codex with a real login | The Codex side was built against the real program (codex-cli 0.169.0): it accepts every option the tool passes, its model list is read, and the instructions it would show the model were inspected. No call reached a model, because the machine it was built on had no ChatGPT login and no route to OpenAI. Unconfirmed: that the reply schema is accepted as it is, the wording of a real usage-limit message, the token counts, and that Codex opens pictures in figures mode |
| macOS, WSL and Windows itself | The tests run on Linux, Windows and macOS after every push, with stand-ins for Claude Code and Codex, and the app's pages run in Chromium, Firefox and WebKit. Still unknown: the real tools on each system beyond what the owner has seen work on Windows, the Safari app itself (WebKit is its engine, not the app), and whether Codex's read-only sandbox needs any setup on Windows |
| Preview pages in the exported book | They pass EPUBCheck and are built like the checkpoint pages, but were not looked at in a reading app or on a device |
| The visible-checkpoints book on a real reader | Page breaks were checked by rendering to pages in a browser engine, not in a reading app or on a device |
| The Anki deck in Anki | The deck was checked by reading its database, not by importing it |
| Fiction and technical books | The rules are written; no such book has been processed |
| Long RSVP sessions | RSVP was played for seconds at a time. Checkpoints firing during playback and stops at pictures and tables are untested in the browser |
| Browser tests not yet written | Editing a question in RSVP and at a checkpoint in review mode; "I pick the paragraphs" when adding; The new kinds of question in RSVP and in the side reader; a question from a highlight edited in the reader's own panel; Changing what you want after a trial and trying the chapter again; the reader's search, footnotes and pictures; rereading a section after a missed question; taking questions out and rebuilding on a book's page; the practice schedule over several answers; Markdown export of the notebook |
| The schedule over real days | Practice was tested with a controlled calendar, not across days of real use |
