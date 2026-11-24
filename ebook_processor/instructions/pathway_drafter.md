
## 12. The graduated pathway

The reader of this book has asked for more than questions at the end of each section. Two things are added. Inside a section the reader meets **ephemeral questions**: brief checks right after the passage they are about. And the checkpoint at the end of a section opens with a **higher-order question** wherever the text rewards one. This section adds to sections 5 to 10. Where it says so, it replaces a rule of theirs.

A question has a **form** (how the reader answers) and a **role** (ephemeral, or for practice). The two are independent: an open question can be a quick ephemeral check.

$forms

### 12.1 Ephemeral questions

An ephemeral question serves the reading session. The book stops for a moment after a paragraph, with the text hidden. The reader recalls the answer in a few words, reveals yours, and reads on. Nothing is graded and the question is not kept for later practice. Its job is to let a reader whose attention has slipped notice it while the passage is still close, so that they can go back at once.

$purpose

Rules for every ephemeral question:

- **Brief.** One short question about one thing. The answer is a few words or one short sentence. At most $check_prompt characters for the question and $check_answer for the answer.
- **Placed where it can be answered.** `after_paragraph` names the paragraph it follows. Everything the answer needs has been read by then: every paragraph in its `supporting_paragraphs` is `after_paragraph` itself or comes before it in the same section. Place it at a meaningful pause: the end of an episode, a step, an argument or an example, where a reader would naturally take a breath. Never between a claim and its support, never after a heading, and never after the section's last paragraph: the checkpoint is there.
- **About what was just read.** Usually the paragraph it follows or the few before it.
- **Understood with the text hidden.** It may lean on what the reader has just read ("Which road does the messenger take to the council?" needs no more introduction), but it names its subject: no bare "he", "this" or "here".
- It does not give its answer away, and its answer comes from the cited paragraphs alone, like every answer.
- **How many.** As a guide, one for about every 300 to 500 words of a section, and none in a section of under about 300 words. Fewer where the text gives no natural pause or nothing worth a check. Never one added to reach a number, and at most $max_checks in a section. A section that gets no practice questions (section 5) may still have ephemeral questions when it is long enough to lose the thread in.

### 12.2 The higher-order question

Where the section, together with what the reader has read before it, has introduced enough material, the section's **first** practice question is a higher-order question: it asks the reader to use what they remember from the book to work something out. Choose the kind of reasoning the passage rewards, and name it in `reasoning`:

- `explain`: how a mechanism works or why something happens, when the text gives the parts and the reader must put the account together;
- `infer`: what lies behind, or follows from, what the text shows (a motive, a cause, an attitude, a meaning): an interpretation;
- `compare`: how two things differ or agree, when the text presents both and does not set them side by side;
- `synthesize`: what several passages add up to;
- `apply`: what the idea says about a new case that you describe in the question;
- `predict`: what the text's account leads one to expect;
- `example`: an example of the idea that is the reader's own;
- `evaluate`: how well a claim, an argument or a decision holds, by the reasons the text gives.

Several can overlap; name the main one.

**Judge the thinking by what the book says outright.** If the author lists three causes of an event, asking for those causes is recall, however weighty the subject. If the reader has to connect clues from three episodes to explain the event, even a one-sentence answer shows synthesis. A question whose answer the text states in one place is a recall question: leave its `reasoning` empty.

Not every section rewards a higher-order question. A section that states a few facts and stops gets recall questions as section 6 describes; do not dress one up. A section has one higher-order question, or two when it clearly rewards two. They come first in `prompts`. The remaining practice questions, up to the limit of $max_questions for the section, are as section 6 describes.

Everything sections 6 and 7 say holds for a higher-order question, with these changes:

- **Where the answer comes from.** It may rest on this section **and on earlier text you were shown**: earlier sections of the part you were given, and the paragraphs under EARLIER TEXT in the task message. The reader has read all of that by the time they reach this checkpoint, so a question may draw on several chapters. List every earlier paragraph the answer draws on in `earlier_paragraphs`. List the paragraphs of this section in `supporting_paragraphs`, which is never empty: a question belongs to the section where the last piece it needs appears. For this question, that replaces "answerable from this section alone". The example about chapter 2 in section 10 is acceptable for a higher-order question when you were shown the paragraphs of chapter 2 and cite them. Never use text that comes after the section, and never text you were not shown.
- **Grounding.** Every fact the answer uses is stated in the cited paragraphs. What the reader adds is the step that connects them: an explanation, a comparison, a reading. The reference answer makes that step plainly and names the facts it rests on. This replaces "do not ask the reader to interpret" in section 7: an interpretation is welcome when the text gives the details that support it, and the answer names those details.
- **More than one defensible answer.** Interpretation, evaluation, application and examples often have several good answers. For such a question fill `accounts_for`: two to $max_accounts short statements of the evidence from the text that any well-supported answer has to account for (what happened, what was said, what the author claims). Each is at most $account_chars characters and is stated in the cited paragraphs. `answer` is then **one** well-supported answer, written so that it shows how that evidence leads to it. The reader compares their reasoning with the evidence, not their conclusion with yours. Leave `accounts_for` empty for a question with one right answer.
- A higher-order question is an open question unless another form clearly tests the reasoning as well.
- It still stands alone, asks one thing and does not give its answer away. For narrative it never reveals later events.

### 12.3 Help me recall

At the checkpoint the reader can ask for help before answering a higher-order question. They are shown a short recall prompt, answer it, and return to the same question.

$help

A **cue** (`cue_prompt` and `cue_answer`) is a short recall question about a detail, an idea or a relationship the reader needs for the higher-order question, with its answer in a few words. It has the limits of an ephemeral question and its answer is stated in the paragraphs the practice question cites. It helps the reader retrieve the material and **leaves the conclusion for them to discover**: it never states or hints at the answer to the practice question. Leave both members empty for a recall question.

### 12.4 An example (invented)

A chapter of a novel is one section. Early in it, a mother answers a visitor's questions on her daughter's behalf. Later, in another scene, the daughter is asked directly and stays silent.

- Ephemeral question, placed after the first scene: "Who answers the visitor's questions about Ada?" Answer: "Her mother, before Ada can speak."
- Higher-order practice question (`infer`), first at the checkpoint: "How has the meaning of Ada's silence changed between the visitor's call and the dinner, and which details show it?" `accounts_for`: "At the visitor's call her mother answers for her before she can speak." / "At the dinner she is asked directly, twice, and says nothing." / "She meets her mother's eye while she stays silent." The answer gives one reading and names those details.

The ephemeral question rehearses a detail the reader will need. It does not say what the silence means.
