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

On Windows itself (not WSL), npm installs Codex as a `codex.cmd` script. The tool looks up its full path and, when it finds the `codex.exe` that the script starts, runs that directly.

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
| `--max-questions N` | Most questions per checkpoint, 1 to 8. The model chooses how many, up to this, by the section's length and density and by what you want from the book | 3 |
| `--forms LIST` | The kinds of question a run may write, separated by commas: `open`, `choice` (multiple choice), `truefalse`, `cloze` (fill in the blank), or `all`. See "Kinds of question" | `open` |
| `--kind auto\|expository\|narrative\|technical` | The kind of book. `auto` decides section by section | `auto` |
| `--content prose\|text\|figures` | What Claude sees of tables, code, formulas and pictures (below) | `text` |
| `--rounds N` | How often a question may be checked. 0 switches checking off | 3 |
| `--model`, `--checker-model`, `--effort` | The model that drafts, the model that checks, and the reasoning effort. `ebook-processor models` lists the models your logins can use. See "Choosing the models" below | Claude Code's defaults |
| `--jobs N` | Calls to Claude at the same time | 3 |
| `--epubcheck` | Also run EPUBCheck on both books (needs Java; downloads EPUBCheck once) | off |
| `--preview-questions` | In the book with visible checkpoints, begin each section with a page that lists its questions (see "Previewing the questions") | off |
| `--purpose TEXT`, `--about-me TEXT`, `--question-style TEXT` | What you want from the book, what matters to you in general, and how you like questions (see "What you want from a book"). Each takes text, or `@FILE` to read it from a file | none |
| `--yes`, `--no-reading` | With those three: use the model's reading of them without asking you, or skip the reading and give your words to the question writers as they are | you are asked |
| `--try-chapter N` | Write and check the questions of one chapter only, and print them. A later run of the whole book with the same options takes the chapter over | whole book |

`ebook-processor process --help` lists them all.

**To see what a run would give** before it uses your plan:

```bash
ebook-processor estimate path/to/Book.epub --density short --max-questions 5
```

It prints about how many sections and questions the options lead to, and the book's chapters with the numbers `--try-chapter` takes. No model is called.

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
| Your questions | What the models are told about you: what matters to you and what you work on, and how you like questions. Whether you are shown how your wishes were read before a run (see "What you want from a book"). And how a question you add yourself gets its passage |
| Reader | Typeface, size, line spacing, column width, and the view a new book opens in |
| Notebook | Whether **Open** shows a passage beside the notebook or in the full reader, which side the reader is on, and whether it scrolls or turns pages |
| Practice | The numbers of the schedule (see Practice below) |
| Notes | Where your notes folder is (see Notes below) |

A change applies at once, also in other open tabs of the app. Settings are kept in `reader-settings.json` and `srs-settings.json` in the data folder.

### Kinds of question

A question can take one of four forms. On the Add a book page you tick the ones a run may write, under **Kinds of question**; the ticks are also in Settings, on the Add a book tab, as what the form starts with.

| Kind | What you do | Graded by |
| --- | --- | --- |
| **Open** | Recall the answer in your head (or type it), reveal the reference answer, mark yourself Correct or Incorrect | You |
| **Multiple choice** | Pick one of four choices, with the mouse or with A to D (or 1 to 4) | The app |
| **True or false** | Say whether a statement is true, with the mouse or with T and F | The app |
| **Fill in the blank** | Type the words missing from a statement | The app |

A new book starts with open questions only, as before: recalling an answer from nothing is the strongest practice. The other kinds are quicker, and good for terms, names and misunderstandings.

- **Which kind a question gets.** For each idea the model picks the kind that suits it among those you ticked: a why or a how stays open, a key term suits a blank, a tempting misreading suits true or false. With several kinds ticked you meet each of them over a book, but you do not set how many of each. What you write under "How I like questions" can shift the balance among the ticked kinds; it cannot add a kind.
- **One try.** A question the app grades is answered once. Then you see whether you were right, the right answer, and a sentence or two from the section that says why. A wrong answer counts as a miss, like an open question marked Incorrect, and offers to reread the section.
- **Typed answers are matched with some forgiveness.** Capital letters, accents, punctuation, a leading "the", and a slip of the keys in a longer word are forgiven; an answer with a number in it must be exact. The model also lists other wordings that count. When the app judges a typed answer wrong and you know it was right, **I was right** counts it as right.
- **Multiple choice** always has four choices, of which one is right, shown in a new order each time. A blank is one gap per statement, and the statement is the model's own sentence, written to stand alone, not a sentence of the book.
- **The check** judges these questions on a sixth point besides the usual five: that the marked answer is the one right answer for a reader of the section. A wrong choice that is also defensible, a statement that is arguable, or a blank that a second answer would fill, fails it.
- **Practice** asks a question in the form it has, and what the app decides (or you, for a typed answer you overrule) is what the schedule uses.
- **Questions on highlights** take the kinds ticked in Settings, on the Add a book tab. With **Another** you can ask for a kind in words ("as true or false"). **Reword** on a proposal lets you change its choices and which one is right, whether a statement is true, or the missing words and the wordings that count. A question you write yourself is an open one.
- **In the files.** The book with visible checkpoints shows the choices on the question page and the right one on the answer page. The Anki deck and the cards text put the choices on the front; Anki does not grade them. `questions.json` has each question's `form` and its members.
- **On the Picco.** The firmware today knows open questions only. In the firmware-format book every question is therefore also there as plain text that it can show: "True or false: ...", the statement with its gap, the multiple-choice question without its choices, each with the right answer at the start of its answer, to grade yourself. The form itself travels beside that text, ready for a firmware that supports it: `docs/question-forms.md` says exactly how.

### Editing a question, and adding your own

A book's questions are yours to change. **Edit question** is on the book's page beside every question, at a checkpoint and in Practice once you have seen the answer, and on a checkpoint answer in the notebook. It opens the same window everywhere:

- **What you can change.** The question and its answer or explanation; the answer key of the kinds the app grades (the choices and which one is right, whether a statement is true, the missing words and the other wordings that count); and the **passage**, the paragraphs of the section the answer rests on. A question keeps its kind.
- **Tell the model what to change.** A line such as "make it harder" or "ask about the cause instead", and **Rewrite**: the model's rewrite fills the form, in the same kind, for you to take or change. Nothing is kept until you save.
- **It is yours once changed.** An edited question is marked "Edited by you" and is kept as you wrote it. **Save and check** runs the same independent check the run used and shows its verdict; nothing is removed because of it. **Put back the original** restores what the run wrote.
- **A question the check left out** can be edited too. The window says what the check objected to; once you have changed the question it is kept.
- **Your results stay.** Editing does not touch the grade or the practice schedule. When an edit changes what is asked, **Start this card over** makes the question count as never answered: it is asked again at its checkpoint, or is a new card in Practice if you are past it. What you answered before stays in the notebook.
- **Add a question** is beside each section on the book's page. You pick the kind and write it. Its passage is the whole section unless Settings says otherwise (Your questions, "A question you add yourself"): you pick the paragraphs, or the model picks them in one short call, with or without showing you its choice first. An added question is asked at its section's checkpoint, or goes straight to Practice when you have read past it. **Write my own** on a highlight offers the four kinds as well.
- **At once.** A saved change is in the reader, Practice, the notebook and every file straight away; saving takes a second or two because the files are written again. Taking questions out still waits for **Rebuild files**.

A book added as a ready-made prepared EPUB has no run behind it, so its questions cannot be edited.

### What you want from a book

Left to itself, the model asks about each section's own main ideas. You can tell it what matters to you, in three places:

| Where | What you write | Applies to |
| --- | --- | --- |
| **What do you want from this book?** on the Add a book page | Why you are reading this book and what you want to keep from it | That book |
| **About me and what I am working on**, in Settings under Your questions | What matters to you in general: your work, your projects, what you are trying to get better at | Every book, unless you untick it for one on the Add a book page |
| **How I like questions**, in the same place | The manner: why and how rather than what, short answers, names and dates or not | Every book |

**What it changes.** Which of a section's ideas are asked about, and from which side. How many questions a section gets: the model chooses the number up to your limit (1 to 8) by the section's length, its density and how strongly it bears on what you want. And the manner of the questions, where your wishes win over the tool's own taste (a reader preparing for an exam can have the dates the tool otherwise calls trivia).

**What it never changes.** Every answer comes from the section it belongs to, so there are no questions about how something applies to your own work. Which kinds of question are written is set by the ticks under "Kinds of question", not by a wish. Every section is still asked about: your wishes move the emphasis, they do not skip the parts you care less about. The independent check judges the same points as before; it is told your manner only so that it does not remark on, say, a date you asked for.

**How your wishes were read.** Before anything is written, the model states in its own words what it will favour and how it will write, after a look at how each chapter opens. It lists the chapters your purpose bears on most (they may get more questions), says which wishes cannot be followed and what the book does not seem to hold, and may ask you up to three questions. You correct the reading in place, or tell the model what to change and have it read again, as often as you like. What you confirm is handed to the question writers word for word; they do not see your own words. **Go on without them** processes the book with no wishes at all. This step is one short call. It can be switched off in Settings; your words then go to the writers as they are.

**How many questions.** Once you choose a file, the Add a book page shows about how many questions and checkpoints the run will give, and the numbers follow the section length and the limit as you change them. It is a range, because the model decides within your limits; it was fitted to real runs. The maximum per checkpoint is a cap, not a target: the line under the estimate says how many questions that cap allows, and when the cap is above what sections of this length are usually given, it says so, because raising it further then changes little. In real runs of one chapter without a statement of what you want, maximums of 4, 5, 6 and 8 gave 10, 10, 8 and 10 questions.

**Try on a chapter.** Instead of the whole book you can have one chapter written and checked, which takes a minute or two, and look at its questions. Then process the whole book (the chapter is kept as it is), try another chapter, or change what you want and try the same chapter again. The page also says how many questions the whole book would get at the chapter's rate.

**Later.** A finished book's page has the box again. Changing it there guides the questions you ask for on highlights from then on; the book's own questions stay as they were written, with your grades and practice history.

What you write in these boxes is sent to the model with every request that writes questions. It is kept in the app's data folder and in the run's work folder. The report states what the question writers were told; the books, the deck and the questions file do not contain it.

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

Only your **first** grade for a question is kept: a Correct after a reread does not replace an earlier Incorrect. A checkpoint is **completed** once every question of it has a grade, Correct or Incorrect. A completed checkpoint does not open by itself again when you go back and read its section once more. One you skipped, in whole or in part, does, and then asks only the questions you have not answered. Jumping through the contents, the search or the map never sets off a checkpoint. To go through a completed checkpoint again by choice, use **Again** beside its section in the contents, or the link at the section's end in the scroll view; **Answer** there takes up a skipped one. Going through one again never changes a first grade. Closing the window in the middle of a question or a reread brings you back to the same place.

Also in the reader: contents by chapter and section, search, and footnotes that open in place. How the text is set (serif or grotesk, size, line spacing, column width) is in **Settings**, on the Reader tab.

The strip at the bottom is the book: one block per section, with a square for each question (blue recalled, black missed, hollow not yet met). The book's page shows the same map, your time and pace per view, and lets you choose which cards go into the Anki deck and the text file: all, only those you missed on the first try, or only those you have met. Cards are tagged `recall::correct`, `recall::missed` or `recall::unseen`.

### Questions from your highlights

A highlight can become a practice question. In the reader, select text and press **q** (or **Question**); in RSVP, **q** takes the sentence being shown. In the notebook, each highlight has **Make a question** and **Write my own**, and one action proposes a question for every highlight of the book, or of a chapter, that has none yet.

- **Who writes it.** The model reads the highlight inside its section, with your note on the highlight as a pointer to what you care about, and proposes one question and answer. It follows what you want from the book (above) as it is at that moment. You accept it, reword it, ask for another, or discard it; a highlight can have several. **Another** has a box for what should be different ("ask about the cause instead", "make it harder"), which you can leave empty. Or you write your own. The models are the ones set in Settings, on the Add a book tab.
- **When it arrives.** A setting on the Reader tab decides what happens after you ask for a question while reading: it waits in the notebook (the default); a panel opens and shows it when it is written; a small notice appears that you can press or leave; or it is accepted without asking. Accepted without asking, a question first gets the same independent check as the book's own questions, and one that fails is held back for you with the reason. In the notebook a proposal always appears for you to decide.
- **Where it is asked.** An accepted question is asked at the end of its section in the app's reader, after the book's own questions, if you have not finished that section yet. Otherwise it goes straight to Practice as a new card. The book's files and the exported books are not changed.
- **Where it shows.** In the notebook under its highlight, set like a checkpoint answer, and in the notebook's Markdown export. In the Anki deck, the text file and the questions data file, tagged `from::highlight`; these are brought up to date when you next download them.
- **Removing a highlight** asks whether its accepted questions should stay for practice. Kept, they are shown with the words they were about.

### Previewing the questions

Reading with the questions in mind can help. Two separate switches show a section's questions before the section:

- **In the reader.** Settings, Reader tab, **Preview questions**. A section then begins with a box listing the questions of its checkpoint that you have not answered yet; a completed checkpoint has none. In RSVP, playback pauses at the section's start and shows them until you press play. The first time a question is shown this way is recorded, and an answer given after that is labelled "Previewed before reading" in the notebook and "previewed" in its Markdown export. It is graded and scheduled like any other.
- **In the exported book.** The book with visible checkpoints can begin each section with a page that lists its questions, without the answers: `--preview-questions` when processing, **Question previews in the exported book** under Advanced in the app, or afterwards on the book's page and with `ebook-processor rebuild Book-processed --preview-questions on` (or `off`). The Picco firmware edition is not changed.

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

### Notes

**Notes**, at the top of the library's pages, is where reading becomes your own thinking. It holds notes that stand apart from any one book: one idea each, in your own words, linked to each other and to the passages they came from.

Your notes are **Markdown files in one folder** on this computer. The files are the truth, not a copy: the app reads and writes them, and any other program can too. The folder can be opened in [Obsidian](https://obsidian.md) as a vault, and what you write in either shows in the other.

```
ebook-processor-notes/
  Books/The Lighthouse Keeper's Almanac.md     one file per book, kept by the app
  Inbox/2026-11-18 1530 a thought.md           quick notes, and notes that have no title yet
  Prices carry information.md                  a note: its title is its file name
  .trash/                                      deleted notes, which can be put back
```

The folder is `ebook-processor-notes`, beside the folder that holds your books (so `~/ebook-processor-notes` unless you moved that). It is made the first time you open Notes. **Settings > Notes** shows where it is and lets you point the app at another folder, as does `ebook-processor serve --notes FOLDER`. The app never moves files: to move your notes, move the folder yourself and then name its new place.

There are two kinds of note for now:

| Kind | What it is for |
| --- | --- |
| **Idea note** | One idea, in your own words, with a title that says it. It cites where it came from and links to other notes. This is what the folder is for |
| **Quick note** | A thought caught before it goes. It waits in the inbox until you make something of it |

**Writing.** A note has a title, which is also the name of its file, and its text. The text is one pane that formats as you type: headings, emphasis, lists, quotes, code and links look like what they are, and the marks of a construct show only while the cursor is in it. Type `[[` to link to another note: the app offers your notes as you type, and after `#` the headings of the note, or after `#^` its passages. A link can be written `[[Title]]`, `[[Title|other words]]`, `[[Title#Heading]]` or `[[Title#^passage]]`. Click a link to follow it; point at it to see what it leads to; a link to a note that does not exist yet offers to make it. `#tag` anywhere in the text is a tag. Ctrl or Cmd with **B**, **I** and **K** make bold, italic and a link. Tab indents a list item; to leave the text with the keyboard, press Escape and then Tab. A note is saved a moment after you stop typing, when you leave it, and with Ctrl or Cmd + **S**.

Beside the text are the note's **sources** (the passages it cites, with a way to open each in the book) and **Linked from**: every note that links to this one, with the sentence the link is in. **Properties** shows the block at the top of the file as fields: the kind, the tags, and any property of your own. Properties the app does not know, written in Obsidian or by hand, are kept as they are.

**Renaming** a note renames its file and rewrites the links to it in every other note. Two notes cannot have the same title, also not in different letter case, because Windows and macOS would not tell their files apart; and a title cannot contain `\ / : * ? " < > | # ^ [ ]`. **Delete** moves the file to the `.trash` folder inside the notes folder; **Trash** on the front page lists what is there and puts a note back.

**Starting an idea note.** From nothing: **New note**. From a book: select words in the reader and choose **Idea note** (or press **i**). The words are highlighted, and the note opens **beside the text** with its source filled in: a line `Source: [[Book#^highlight]]` and the same link in the note's `sources` property. The highlighted words are shown with the note, not copied into it. Nothing is kept until you write something. A note you wrote in the book, on a section or a paragraph, can also be made an idea note: its words are where the idea note starts. A note without a title waits in the inbox under a name made of the date and its first words; giving it a title moves it out and names its file.

**Quick notes.** **Quick note** at the top of every page, or **Ctrl + .** (Cmd + . on a Mac) anywhere, also while typing in another field, opens a small box. What you write goes to the inbox. Written in the reader, the note remembers its book and the place you were at. A Markdown file dropped into the `Inbox` folder shows in the inbox too. To keep a quick note, open it, rewrite it, give it a title and press **Make this an idea note**: it stays the same file, moved out of the inbox.

**A file per book.** Every book of your library has a file in `Books`, written by the app whenever you change something in the book: your highlights, the notes on them, and your notes on paragraphs, sections, chapters and the book, in reading order under the book's chapters and sections. Each entry ends with a block id (`^h3f9a`), which is what a citation points to, in the app and in Obsidian. These files are the app's: what you change in them is written over. A book removed from the library keeps its file, marked at the top; links to it still show the words, and no longer open the reader.

**The front page** shows the inbox, the notes you worked on last, your tags and your books. The **search** box looks through notes of every kind and through the highlights and notes of every book: notes first, then passages, with titles you can jump to, and filters by kind, tag and book. Choosing a book shows its passages and the notes that cite it. A passage that is found can be opened in the book or made the start of an idea note.

**Edits made in another program** are seen: the app looks at the files again whenever Notes asks for something. A note that is open reloads when its file changes. If you have also changed it in the app, the app does not write over the file: it asks which version to keep.

This is the first part of a larger plan (`docs/KNOWLEDGE-PLAN.md`). Working through highlights one by one, index notes and projects, a graph, and help from the model in finding connections are still to come.

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

When you have said what you want from the book, a step comes first: the model reads your wishes beside a skim of the book and you confirm its reading (see "What you want from a book").

The rules Claude follows are in `ebook_processor/instructions/drafter.md` and `checker.md`; `reading.md` holds the rules for reading your wishes and `highlight.md` those for a question on a highlight. Read them to see exactly what a good section, question and answer are taken to be; change them to change the questions.

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

- **A lost connection.** While a book is processed, the tool checks every ten seconds whether the model's service can be reached. If the connection goes, the run says so ("No connection to Claude Code. Waiting for the network") and waits, for as long as it takes. When the connection is back it goes on by itself where it was; nothing done so far is lost, and the wait does not use up any of a call's retries. A call that was under way is given up after 45 seconds without a connection and made again afterwards. **Stop** ends a run at once, also while it waits or a call hangs, and **Continue** takes it up again. The check asks `api.anthropic.com` (or your `ANTHROPIC_BASE_URL`) for Claude Code and `chatgpt.com` for Codex, through the system's proxy settings. On a network where that check itself never gets through, it is not acted on, and a call is given up after 30 minutes as before.

- **Usage.** A run reads the book about twice (once to draft, once to check) and writes a few hundred words per section. The report gives the number of calls and tokens, and what they would have cost at API list prices, as a measure of size; a Claude plan is not billed per run.
- **Card identities.** Importing a rebuilt deck of the same run updates the cards and keeps your review history. Processing the same book again from scratch writes new questions, so it makes new cards: delete the old deck first if you do not want both.
- **Very long books in the firmware format.** The firmware's data file holds at most 512 sections and 1,024 questions. A very long book at a short section length, or with a high limit of questions per checkpoint, can exceed that; the estimate on the Add a book page warns when it may. The book with visible checkpoints and the deck are still written, and the report says so; a longer section length fixes it.
- **Page breaks.** The visible checkpoints use standard CSS page breaks written into the chapter files. The original's own styles, fonts and images are untouched. The book's title gets the suffix " (checkpoints)" so that the two editions can be told apart in a library (`--title-suffix ''` switches that off).
- **DRM.** A book with DRM cannot be read. Use a DRM-free EPUB.

## For developers

```bash
python3 -m pip install -e '.[test]' && python3 -m pytest -q
```

The tests never call a model: `tests/scripted.py` is a stand-in that answers from the task message alone, and small stand-in programs take the place of Claude Code and Codex.

The browser tests (`tests/browser/`) drive the app itself: reading in the three views, checkpoints, highlights and notes, the notebook and the reader beside it, settings, adding a book, previews, practice, and the Notes area with its editor. They need Playwright and are left out without it:

```bash
python3 -m pip install -e '.[browser]' && python3 -m playwright install
```

```bash
python3 -m pytest tests/browser --browser firefox
```

After every push GitHub runs both (`.github/workflows/tests.yml`): the test suite on Linux, Windows and macOS, and the browser tests in Chromium and Firefox on Linux and Windows and in WebKit, the engine of Safari, on macOS.

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
| `ebook_processor/highlight_questions.py`, `instructions/highlight.md` | Questions made from highlights: their store, what the model is sent, the check, and where an accepted question is asked |
| `ebook_processor/srs.py` | The spaced-repetition scheduler and a book's cards |
| `ebook_processor/vault.py` | The notes folder: files and their names, the properties at the top of a file (read and written without a YAML library), links, tags and block ids, the index kept in memory, saving that does not write over a change made on disk, renaming with links rewritten, the trash, search |
| `ebook_processor/bookfile.py` | A book's file in the notes folder, made from its highlights and notes |
| `editor/` | The source of the note editor: CodeMirror 6 with the additions a notes folder needs (see below) |
| `ebook_processor/reading.py` | A prepared EPUB as the reader needs it (clean chapter HTML with word positions, sections, search) and the saved reading state |
| `ebook_processor/update.py` | `ebook-processor update`: find and stop the running app, fast-forward the checkout, install, start again |
| `ebook_processor/web/` | The local web app: a standard-library HTTP server, the library page (`index.html`), the reader (`reader.html`, which is also the reader beside the notebook when opened with `?pane=1` in a frame), and the scripts both share: the settings window (`settings.js`), the questions' forms (`forms.js`), a note's page and the Quick note box (`notes.js`) and the built editor (`editor.js`) |
| `tests/data/` | A small made-up book with its expected results, broken prepared books the validator must reject, and Thomas Paine's *Essays* (Standard Ebooks, CC0) |

**The note editor** is the one part of the app that is built from other people's code in the browser: [CodeMirror 6](https://codemirror.net) and the Lezer Markdown parser, both MIT. Its source is `editor/src/index.js`; the file the app serves, `ebook_processor/web/static/editor.js`, is built from it with esbuild and committed, so that installing and running the app needs no Node. To change the editor (Node 20 or later):

```bash
cd editor && npm ci && npm run build
```

`editor/package-lock.json` fixes every version, so the same source gives the same file; the versions are also listed at the top of the built file, and the licence texts are in `editor/THIRD-PARTY.md`. As built now: `@codemirror/view` 6.43.15, `@codemirror/state` 6.7.6, `@codemirror/language` 6.13.1, `@codemirror/commands` 6.11.1, `@codemirror/autocomplete` 6.20.4, `@codemirror/lang-markdown` 6.5.2, `@lezer/markdown` 1.8.1, `@lezer/common` 1.5.5, `@lezer/highlight` 1.2.5, built with esbuild 0.28.2. The page loads the editor only when a note is opened.

The firmware format (`rsvp/checkpoints.json`, version 1, and the `rsvp-<section>-start` and `-end` markers) is the one eink-learner-aid defines; its names are kept so that books from this tool load on that firmware. The two repositories share no code at run time.

## Licence

MIT. The EPUB engine was ported from eink-learner-aid, a fork of CrossPoint Reader (MIT).
