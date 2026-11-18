# Question forms in the firmware-format book

This is for whoever adds the forms of question to the Picco firmware. It says exactly what `ebook-processor` writes
into `rsvp/checkpoints.json` of a `Book.prepared.epub` for a question that is not an open question, and what a
firmware that does not know about forms sees instead. The code is `ebook_processor/forms.py` (`to_x`, `from_x`,
`as_text`).

## The rule

The file is still format `rsvp-checkpoints`, version 1. Every prompt still has `id`, `prompt` and `answer`, within
the limits the firmware has today (300 and 600 characters, plain text). Nothing a version 1 firmware reads has
changed.

A prompt whose question is **not open** has one more member, `x_form`. Version 1 ignores members it does not know,
so today's firmware shows such a question as an open one: it shows `prompt`, the reader recalls an answer, it shows
`answer`, and the reader grades themselves. `prompt` and `answer` are written so that this works:

| Form | `prompt` holds | `answer` holds |
| --- | --- | --- |
| multiple choice | the question, written so that it can be answered without the choices | the right choice, then the explanation |
| true or false | `True or false: ` and the statement | `True.` or `False.`, then the explanation |
| fill in the blank | the statement, with `____` (four underscores) where the missing words go | the missing words, then the explanation |

A prompt without `x_form` is an open question, as it always was.

## `x_form`

An object. `form` says which form it is; the other members depend on it.

```json
{ "form": "choice", "choices": ["...", "...", "...", "..."], "correct": 2, "explanation": "..." }
{ "form": "truefalse", "truth": false, "statement": "...", "explanation": "..." }
{ "form": "cloze", "accept": ["clockwork", "a clockwork"], "explanation": "..." }
```

| Member | Form | Meaning |
| --- | --- | --- |
| `form` | all | `choice`, `truefalse` or `cloze` |
| `explanation` | all | Shown after the reader has answered: why the right answer is right. At most 400 characters, plain text |
| `choices` | `choice` | Exactly four texts, at most 150 characters each, no two the same. Their order in the file is fixed by the tool and is not meaningful; a firmware may show them in this order or shuffle them |
| `correct` | `choice` | The index in `choices` of the right one, counted from 0 |
| `statement` | `truefalse` | The statement alone, without `True or false: `. At most 280 characters |
| `truth` | `truefalse` | Whether the statement is true |
| `accept` | `cloze` | The answers that count as right, one to five texts of at most 60 characters. The first is the missing words and is the one to show. The others are other ways of writing the same answer |

For `choice` the question is `prompt`. For `cloze` the statement is `prompt`, and it contains `____` exactly once.

The texts follow the same plain-text rule as `prompt` and `answer`.

## Grading

The app grades these forms itself, and a firmware that supports them can do the same:

- **Multiple choice, true or false**: right when the reader picks `correct`, or says what `truth` says. One try.
- **Fill in the blank**: the app compares what the reader typed with each text in `accept`, forgiving capital
  letters, accents, punctuation, a leading "the", "a" or "an", spacing, and a slip of the keys (one edit for an
  answer of five letters or more, two from nine). An answer with a digit in it must match exactly. The reader can
  overrule a wrong verdict. On a device without a keyboard, the simplest faithful behaviour is the one a version 1
  firmware already has: show the statement, let the reader recall the words, show `accept[0]` and the explanation,
  and let the reader grade themselves.

After any answer the app shows the right answer and `explanation`.

## The saved state

The app records a result for these questions as it does for open ones: `correct` or `incorrect` as the first
grade, and an attempt with the text of what was given (the choice's text, `True` or `False`, or the words typed).
An attempt that was judged wrong and overruled by the reader has `"overruled": true` and the grade `correct`.

## A book made elsewhere

The app reads `x_form` defensively: when it is missing, or is not shaped as above, the prompt is treated as an open
question with its `prompt` and `answer` as they stand.
