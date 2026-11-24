# Deferred

Things that were discussed and put off, in one place. Nothing here is being worked on. Each item says where it came from, so that it can be picked up without rereading old conversations.

## Decided for later

These were chosen as later work by the project owner. The knowledge system is planned in five phases: see `docs/KNOWLEDGE-PLAN.md`. All five phases are built: the notes folder, the editor, idea notes and quick notes, search; the Process view and related notes; index notes, projects and more of Markdown in the editor; the graph, the neighbourhood, revisiting and the timeline; and the model's part (connections, answers from the notes, questions from a note).

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

### The model in the notes folder (Phase 5 of the knowledge system)

| Item | What it means |
| --- | --- |
| The model drafting notes | By the owner's decision the model does not write idea notes, and "Link" inserts a link only, not the model's reason |
| Sending the whole folder | Not chosen. A connection is looked for among the titles and first lines of the newest 400 notes and the full text of the 16 likeliest; a note beyond those cannot be proposed |
| Answers from general knowledge | Not chosen: when the notes do not answer, that is said, and nothing is added |
| A log of questions and answers | Not chosen: an answer is kept only on request, as a quick note |
| A queue of their own for notes' cards | Not chosen: they are practised with the cards of books. Practice for one book alone does not include them |
| A note's question checked against the passages it cites | Not chosen: the note alone is the passage |
| Pausing a card when its note changes | Not chosen: the card stays, and the note's page says the question was made before the change |
| Connections for many notes at once | "Find connections" is asked for one note at a time. A pass over the whole folder was not built |
| Follow-up questions | Each question to "Ask" stands alone; there is no conversation |
| The notes' questions in the Anki deck | A book's exports hold the book's questions. Cards made from notes are practised in the app only |
| Choosing the sources of an answer | The sources sent with a question are found by shared words and tags. A question phrased in words your notes do not use can miss them; the model is not asked to search |

### Other sources than EPUB

Both steps of `docs/SOURCES-PLAN.md` are built: Markdown, text, HTML and Word files, saved pages, web addresses. Left for later there: PDF, a browser extension (or taking pages from one that exists), pasted text, several files at once, fetching a page again to see what changed, articles spread over several pages, Safari's `.webarchive`, `.rtf` and `.odt`.

| Item | What it means |
| --- | --- |
| Pandoc for Word files | Not added. Word files are read by a small library (mammoth), which did well on the files tried. Pandoc stays the fallback if your own files come out badly: say so and it can be added as an optional extra |
| A route for a browser extension | Not built. The converter already takes "a page's HTML and its address", which is what an extension would send; what is missing is a route another program may call, with a key of its own shown in Settings |
| A page's caption lines | The line under a picture is kept as a paragraph in italics, not as a caption tied to the picture |
| Sites that refuse the app | No way round is built (no other identity, no waiting and trying again). The form names the saved page as the way in |
| Where an article came from, in the reader | The address and "open the original" are on the entry's page. The reader's top bar does not show them |
| "Book" in the wording | The Add form, the entry's page and the library speak of an article. Settings, the help under options, the reader and the Process view still say "book" for both |
| A setting of its own for articles' section length | Not built: the usual setting applies and can be changed per run |
| Formulas | MathML in an HTML file is kept as its text, not as a formula |
| Chapters at two levels | A text with parts and chapters is cut at one level only: the highest that appears twice or more. In a text file, parts and chapters are one flat list |
| Small first document | What stands before the first chapter is a document of its own, however short |
| SVG pictures | Not kept from a file that is no EPUB: only PNG, JPEG, GIF and WebP |
| Tiny pictures | A picture one or two pixels wide or high is dropped as a counter or spacer. The plan said "under 2 KB", which would also drop small real pictures |
| `--as book\|article` on the command line | Not built: the command line has no library. The kind is chosen in the app |
| Tables that arrange a page | Opened into plain blocks by a guess (no header cells, and long text or blocks in the cells). A real table without header cells and with long cells would be opened too |
| Text files: indented blocks | A block whose every line is indented is kept as it is (verse, code), unless most of the file is indented |

### The review of November 2026

Three reviews of the code and a plan for what they found are in `docs/reviews/`. The plan's last section records what was built. Left out, with the reason:

- **One long document drafted in parallel** (`speed/M3`). A text that is one document (a long file with no headings) is drafted one part after the other. Changing that is a day or more of work on the order of the sections, and helps only such texts.
- **The app listening before the tools are asked** (`speed/S1`). The library appears in under two seconds on the owner's computer, so the start was left as it is.
- **Pages kept by the browser** (`speed/P4`). On one computer the gain is a few milliseconds.
- **A kept copy of the opened run, and parsed books handed to the validator** (`speed/R3`, second part). A rebuild of a 53,000-word book takes 1.1 s after the other changes; this would save about 0.3 s more and needs changes inside the engine's validator.
- **Checks of one file in the notes folder** (`speed/R5`, in part). With the quick listing a save costs little, and such checks change when a change made outside is seen.
- **Only the shown items sent to the Process view** (`speed/P2`, in part). The page uses the other items too. The list is drawn in parts and a choice changes two rows, which were the slow things.
- **Pictures fetched side by side** (`speed/I2`, in part). The limits on addresses and seconds end the long waits. Fetching in a pool has a risk for the rule that the same page gives the same book.
- **Slow input inside the libraries** (`redteam/M5`, in part). A Markdown file of 10,000 `[` characters takes 17 s in the Markdown library, and an HTML file with one element of 40,000 attributes 12 s for each of four readings. You would have to add such a file yourself. Reading an HTML file once and handing the result on would divide the second case by four.
- **`GET /api/vault` makes the notes folder** (`redteam/M9`). It does what the first visit to Notes does anyway, and is cheap now.
- **One shared card for a highlight's questions, one style sheet for both pages, the side panels of a note's page as functions of their own** (`structure/P4`, `P3`, `P6`). No fault depends on them; they are for when those parts are next worked on. The other views of the library page stay in its one script until they are reworked; the graph and the Process view have moved to files of their own.
- **Not reviewed by anyone:** Windows and macOS in real use; the real tools and real web pages; the logic of the EPUB engine beyond the word pass and the zip reader; how a typed answer is matched (`forms.js`); a hostile Word file beyond its size.

### The graduated pathway (ephemeral questions)

Built on 2026-11-23 from `docs/COMPREHENSION-PLAN.md`. Considered and left for later:

| Item | What it means |
| --- | --- |
| Ephemeral questions in the other books | The firmware-format book and the book with visible checkpoints hold the practice questions only. The firmware knows nothing of a question inside a section, and its checkpoint file has a size limit. The app keeps the pathway in `pathway.json` beside the book |
| Ephemeral questions for a book that has none | A book prepared without them, or imported as a ready-made prepared EPUB, cannot be given them afterwards. It must be added again |
| A model's reading of your typed answer | For a question with more than one defensible answer the feedback is written when the book is prepared: the evidence a well-supported answer accounts for. A button that sends your answer to the model and says which evidence it accounts for was considered and not built (owner's decision, 2026-11-23) |
| Questions that reach far back | A higher-order question can draw on the earlier sections of the part its drafter was given, and on about 3,000 words of the text before that part. A question that connects chapter 2 with chapter 20 is not written: the drafter is not shown that much earlier text |
| Changing which questions an ephemeral question supports | The links are written when the book is prepared. The editor changes a question's wording, its evidence and its passage, not its links or its place |
| Taking an ephemeral question out of a book | There is Skip while reading and the choice of fewer or none. A question the check failed is left out. Removing one for good from the book's page is not offered |
| A promoted question at the checkpoint | A promoted question is a card at once and is not asked at the section's checkpoint in the app (owner's decision). In the firmware-format book it stands at the checkpoint, after the book's own questions: that format has no other place for it |
| A choice of how many of each kind | The two rows of ticks say which kinds the practice questions and the ephemeral questions may take, not how many of each. Choosing a share per kind, or per role, was considered and not built (added with the separate kinds, 2026-11-24) |
| Converting a practice question to another kind | A promoted question can be rewritten in a practice kind when it is kept. A question the run wrote, or one already kept, still keeps its kind when edited. Taking it out and adding one of the other kind does the same |
| Continuing an older run without `--ephemeral-forms` | A run with ephemeral questions saved before the two roles had kinds of their own wrote them in the kinds of `--forms`, and is read so. Continuing it with other kinds than open needs `--ephemeral-forms` with the same list as `--forms`; otherwise it is refused with a message that names the difference. Making the default follow `--forms` was considered and not done: the owner chose open only as the default |
| An ephemeral question left open when the window closes | A checkpoint's question comes back after the window is closed in the middle of it. An ephemeral question does not: it is asked again when you read past its place |

## To verify

Things that work in tests but have not been confirmed in real use, and facts that are still unknown.

| Item | What is missing |
| --- | --- |
| The graduated pathway with a real model | **Not tried.** The session that built it had no model to call. The instructions for ephemeral and higher-order questions (`instructions/pathway_drafter.md`, `pathway_checker.md`) were tested only for what the script holds a reply to, with a stand-in model that writes the plan's worked example. Whether a real model places checks at good pauses, judges recall against reasoning as the plan asks, and writes evidence that lets a different answer be judged fairly needs a run of a chapter you know well: try one with `ebook-processor beta` and read its questions on the book's page before reading the book |
| Ephemeral questions in Firefox and Safari | The page view finds the page a question's paragraph ends on from the paragraph's last box on the page. Tested in Chromium here, and by the GitHub jobs in Firefox and WebKit once pushed |
| Real web sites | **Not tried at all.** The session that built this could not reach the web, so fetching and article finding were tested against a small site run by the tests themselves (a page with a menu, an advert, a related box, comments and a footer; a redirect; refusals; a PDF; a script-only page; a paywalled page). Try five or six of your own addresses with `ebook-processor beta` before trusting it |
| A page saved by Firefox or Safari | A `.mhtml` file written by Chromium itself is tested. Firefox saves `.html` with a folder beside it (the pictures are then missing unless the page names them by full address); Safari's `.webarchive` is refused with a hint |
| Real Word files | Tried on files built by the tests and on one written by Pandoc. Files from Word, Google Docs and Pages differ in their styles |
| Real Markdown, text and HTML files | Tried on files written for the tests and on two pieces through the real model (320 and 1,370 words: one and two sections, two and five questions, all passing their check). Your own exports from writing tools, and text files of real books, have not been through it |
| Chapter headings in text files | The rules were tried on made-up layouts. Real books vary more (a contents list that repeats every heading, for one) |
| A file in a non-Western encoding without a mark | The guess prefers Western European when it fits. A wrong guess shows as garbled accents in the form's title, before anything is run |
| The new libraries on every system | `lxml` is compiled code, and trafilatura brings a dozen libraries of its own (about 35 MB on disk in all); GitHub's runs confirm that they install and work on Linux, Windows and macOS on the Python versions tested |
| The notes folder in Obsidian | The folder is written to be an Obsidian vault: file names as titles, `[[links]]` by name, block ids, YAML properties, `.trash`. Obsidian itself has not opened one, because it is not available where the app is built. To check: open the folder as a vault, follow a `Source: [[Book#^id]]` link to its highlight (a highlight is written as `> words ^id`; if Obsidian does not find an id at the end of a quoted line, the id belongs on a line of its own after the quote, a form the app already reads), edit a note there and see it change in the app, rename a note in the app and see Obsidian's links follow |
| The notes folder on Windows and macOS | File names, letter case and renaming are tested, and GitHub runs the tests on both systems. Not seen in real use: a note open in another program while the app saves it (Windows refuses, and the app says so), and a folder inside OneDrive or iCloud |
| The editor on a phone or tablet | CodeMirror supports touch, but the Notes pages were laid out and tested for a laptop or desktop browser |
| More of Markdown, compared with Obsidian | Tables, embeds, callouts, footnotes and tasks were written to match how Obsidian reads them, and were not compared side by side with it. In a table, a bar inside a link must be written `\\|`, as Obsidian also asks |
| Dragging in the outline | Tested with the buttons. Dragging uses the browser's own drag and drop and was tried by hand in Chromium only |
| The model's help with a real folder | Six real calls on a made-up folder of seven notes: "Find connections" proposed two sound connections with quotations that were in the texts and left the weak ones out; an answer named its sources after each statement; a question the notes did not cover was answered with "your notes do not say" and what would be needed; a question made from a note passed its check, and a deliberately wrong one failed it for the right reason. Each call took 3 to 8 seconds. Not seen: a folder of hundreds of notes, kinds of question other than open, Codex, and how often a real proposal names something it was not shown |
| The graph with many notes | Measured in a browser without a graphics card (the review of November 2026): with 3,000 notes the first picture took 2.9 s, with 10,000 notes 15.5 s. Since then the points start spread out, the layout gets 8 ms of each frame, and a settled graph draws nothing; the times with thousands of notes were not measured again after that change. A note with 150 links no longer hangs the page (tested). How readable a large graph is will show with a real folder |
| Related notes on a real folder | The scoring was tried on a handful of made-up notes. Whether the list is useful, and how many everyday words slip through, will show only with a real folder of some size |
| A large notes folder | Measured with made-up notes of about 120 words: 1,000 notes are read in about half a second and 10,000 in 1.5 to 6 seconds (120 MB) the first time; after that the folder is listed in 28 ms and a common word is found in 40 ms at 10,000 notes. Not measured: typing in one very large note, and a real folder |
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
| Windows: a file replaced while another program has it open | A write is put in place through a temporary file; when Windows refuses that for a moment the app now tries five times. Written from how Windows is known to behave: nobody had a Windows machine to produce the refusal |
| Windows: Ctrl-C during a run | On Linux and macOS Ctrl-C now gives up the model calls that are running (tested with a real signal). The test is skipped on Windows, where a signal sent to oneself is not Ctrl-C, so the behaviour there is not known |
| Windows: the notes folder's listing | The folder is listed with one call for each folder, which on Windows takes a file's time and size from the folder's own record. A file that another program is still writing may show its change a moment later than before |
| A power cut during a run | The run's state is now forced to the disk before it takes its place. Not tried: it needs a power cut |
| A page of another site reaching the app | A request that names another host, or a changing request from another origin, is refused (tested with made-up requests). The attack this guards against, a web page that gives its own name this computer's address, was not carried out in a browser |
| The limits of a call the reader waits for | A check, a rewrite, a question on a highlight and the model's help in the notes folder are given up after five minutes and one more try. Whether five minutes suit the real models at high effort has not been seen |
| The visible-checkpoints book on a real reader | Page breaks were checked by rendering to pages in a browser engine, not in a reading app or on a device |
| The Anki deck in Anki | The deck was checked by reading its database, not by importing it |
| Fiction and technical books | The rules are written; no such book has been processed |
| Long RSVP sessions | RSVP was played for seconds at a time. Checkpoints firing during playback and stops at pictures and tables are untested in the browser |
| Browser tests not yet written | Editing a question in RSVP and at a checkpoint in review mode; "I pick the paragraphs" when adding; The new kinds of question in RSVP and in the side reader; a question from a highlight edited in the reader's own panel; Changing what you want after a trial and trying the chapter again; the reader's search, footnotes and pictures; rereading a section after a missed question; taking questions out and rebuilding on a book's page; the practice schedule over several answers; Markdown export of the notebook |
| The schedule over real days | Practice was tested with a controlled calendar, not across days of real use |
