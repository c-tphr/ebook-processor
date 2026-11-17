# Deferred

Things that were discussed and put off, in one place. Nothing here is being worked on. Each item says where it came from, so that it can be picked up without rereading old conversations.

## Decided for later

These were chosen as later work by the project owner.

| Item | What it means | Notes |
| --- | --- | --- |
| Personal knowledge system | Grow the per-book notebook into a full system in the spirit of Zettelkasten: conceptual "permanent notes" that stand apart from any one book, links between notes with back-links, and other features familiar from Obsidian | The first step, a notebook per book for "literature notes", is the current highlights and notes work |
| Notebook across all books | One place for every book's highlights and notes, with search and filtering | Part of the knowledge system above |
| Questions from highlights | Turn a highlighted passage into a practice card: Claude drafts a question and answer from the passage for you to accept, reword or discard, or you type your own | Deferred again when highlights were scoped: the first version of highlights uses no Claude. To follow once highlights have been in use for a while |

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
| Editing questions in the app | The review page can take questions out. Rewording a question or answer there was passed over; edits would not be re-checked against the text |
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

### Narration

Narration itself is planned and waiting on product research. These parts were passed over when it was scoped.

| Item | What it means |
| --- | --- |
| Listening away from the computer | Audiobook files or a private podcast feed for a phone, or audio files for the Picco |
| Spoken checkpoints | Reading questions and answers aloud, and answering or grading by voice. Narration will pause and hand over to the screen instead |
| Describing tables and pictures | Claude writing a short spoken description of each table, listing and picture. Narration will stop at them and show them, as RSVP does |
| Expressive voices for fiction | Acted dialogue, or different voices for different characters |
| A private local voice as a fallback | A voice running on the user's own machine for books that should not be sent to a service. Local voices are being researched only as the answer to the budget |

## To verify

Things that work in tests but have not been confirmed in real use, and facts that are still unknown.

| Item | What is missing |
| --- | --- |
| Mochi's actual defaults | Mochi does not publish its multipliers. Reading them off the Review Settings screen of a fresh install would settle the Remembered multiplier (1.8 here), the Forgot multiplier (0.5), the longest interval and the size of the due-date variation |
| Revision with real Claude | In the real runs no question failed its check, so the revise-and-recheck path has only run against the scripted stand-in |
| Usage-limit handling | A run recognises the plan's usage limit by the wording of Claude Code's error. No real limit message has been seen |
| macOS and WSL | Everything has run on Linux only |
| The visible-checkpoints book on a real reader | Page breaks were checked by rendering to pages in a browser engine, not in a reading app or on a device |
| The Anki deck in Anki | The deck was checked by reading its database, not by importing it |
| Fiction and technical books | The rules are written; no such book has been processed |
| Long RSVP sessions | RSVP was played for seconds at a time. Checkpoints firing during playback and stops at pictures and tables are untested in the browser |
| The schedule over real days | Practice was tested with a controlled calendar, not across days of real use |
