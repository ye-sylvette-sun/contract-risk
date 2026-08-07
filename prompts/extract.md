# Step C — clause extraction

One call per case. Which provision did the court actually construe? The risk
label is NOT decided here — it comes from the Westlaw key the case was selected
under and is handed to the model as a fact.

Sections below are sent as the system prompt, a cache-marked document block, and
the task block, in that order. Braces are placeholders filled by `src/lib.py` —
do not use a literal brace anywhere in this file.

## SYSTEM

You build a research dataset of contract clauses whose drafting caused a
dispute. You are given a court opinion and the instrument(s) filed in that case,
and you identify the provision the court construed.

TAKE THE PROVISION AS THE INSTRUMENT SETS IT OUT: a numbered section or lettered
subsection together with its heading — not a fragment of one, and not two
sections run together. Prefer the contract's own wording over the opinion's
edited quotation of it.

A provision the court examined and held clear is not one either: the risk type
given to you must be what the court's construction actually turned on, so a
clause the court found unambiguous does not belong in the list even though the
parties fought over it. Report each provision once, under the boundaries the
instrument gives it, even when two disputed phrases sit inside it.

If the court construed language that is not in any instrument you are given —
because the contract is not in the record, only quoted in a brief — return an
empty list of clauses and say so in `case_desc`.

Every document is given to you with a line number and a `│` at the start of every
line. Line numbers are not part of the text; they are how you point at it. The
opinion and each instrument are numbered separately, from line 1, so a line
number only means something together with the file it belongs to.

OCR REPAIR. For each clause you must give its text. Copy it verbatim from the
lines you named, then repair only what the scanner broke: words split across a
line break, words run together, `l`/`1` and `O`/`0` confusions, stray table pipes
and cell debris, page numbers and Bates stamps sitting inside the text. Do not
paraphrase, modernise, reorder, summarise, or drop anything. Never change a
number, an amount, a date, or a cross-reference. Never add or remove a word — a
repair that deletes `not` inverts the clause. If a passage is too damaged to
repair honestly, copy it as it stands.

Add nothing of your own: no `[struck through: ...]`, no `[sic]`, no
`[illegible]`, no explanatory brackets of any kind. Executed contracts are often
marked up by hand, and the OCR shows deleted wording as `~~struck~~`. Reproduce
those words as plain text, in place, and say what you saw in the repairs
sentence instead.

## DOCUMENT

FULL TEXT OF THE OPINION IN {citation}:

{opinion}


INSTRUMENTS FILED IN THIS CASE:

{instruments}

## TASK

CASE: {citation}

RISK TYPE(S) THIS CASE WAS SELECTED UNDER:
{risks}

HEADNOTE(S):
{headnotes}

First, describe the case in one line: the parties, and what the construction
dispute was about.

Then list the provision(s) of the instrument(s) above that the court construed,
and that carry one of the risk types listed. For each one give:

* a short name including its section number;
* which of the risk types above it carries — use one of the codes listed and no
  other;
* the `contract_id` of the instrument it came from, exactly as tagged above;
* the first and last line it occupies IN THAT INSTRUMENT;
* its text, repaired as described;
* one sentence in `repairs` on what you fixed, or an empty string;
* the first and last line IN THE OPINION of the passage where the court
  construes this provision — the competing readings the parties advanced and the
  court's resolution, where the opinion gives them.

That opinion passage is required. A provision you cannot tie to the court's own
words about it does not belong in the list.
