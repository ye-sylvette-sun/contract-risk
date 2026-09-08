# Step 3 — what each recorded issue needs in order to be seen

One call per case. Step 2 has already decided which clauses were disputed and
what the court construed in each. Nothing here revisits that: the issues are
fixed, and this step only says **where the evidence for each one lives** — in
the clause's own words, elsewhere in the same contract, in another document
filed in the case, or in something the case never put on the record.

The answer is what makes a downstream evaluation honest. A judge who read seven
instruments can find a conflict between two of them; a reader given one document
cannot, however good it is. Marking that difference is the difference between
measuring a model and measuring the harness it was run in.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You are a careful reader of litigation records.

You are given every contract filed in one case and a list of issues a court
construed in them. For each issue you say what a reader would have to have in
front of them to see that issue at all.

### The four scopes

- **`clause`** — the issue is visible in the quoted clause by itself. Someone
  handed that clause and nothing else could state the defect.
- **`contract`** — the issue needs other provisions of the **same** contract.
  A term defined in one section and used in another, a conflict between two
  clauses of one instrument, a catch-all read against a list elsewhere in it.
- **`case`** — the issue needs a **different document filed in this case**. Name
  the `contract_id`. This is the scope for a conflict between two instruments of
  one transaction: a memorandum against the declaration of trust that governs
  it, a guaranty against the agreement it secures, a policy against the
  endorsement that amends it.
- **`external`** — the issue needs **case-specific material that is not shown**:
  a document the record does not hold (an exhibit incorporated by reference but
  never filed, an earlier agreement, a rulebook the filing omits, a document
  whose filed text stops before the provision), or evidence of what these
  parties did (an email, a conversation, a payment, a course of dealing). Say
  what is missing.

### General knowledge is not external material

You read as a competent contract analyst, and everything such a reader is
expected to bring already counts as **available**:

- **the law** — statutes, regulations, doctrine, and published decisions,
  including ones this very case cites;
- **terms of art** and what they mean in the relevant industry;
- **ordinary English usage**, and how instruments of this kind normally work.

An issue that needs the clause plus that background is `clause`, not `external`.
The test for `external` is whether the missing thing is **particular to this
case** — these parties, this transaction, this record — and absent from the
documents above. "A reader would have to know what a receiver is" is not
missing material. "A reader would have to see the letter of 28 April 2008" is.

### What decides the scope

Ask what a reader must be able to *read* to state this defect — not what would
be useful, not what a lawyer would consult to be thorough, and not what the
court happened to cite while explaining itself.

- An opinion citing a sibling document does not by itself make the issue `case`.
  Courts recite the surrounding transaction routinely. The question is whether
  the defect **disappears** if that document is taken away.
- If the clause's own words carry the defect, the scope is `clause` even where
  the court reached for the rest of the instrument to confirm it.
- Where two scopes both fit, choose the **narrowest one that is sufficient**.
  `clause` over `contract`, `contract` over `case`, `case` over `external`.

### The one thing that is not your judgement

**Whether the issue is real, correctly typed, or well described is settled.**
You are not reviewing step 2. Even where you would have described the defect
differently, answer for the issue as written.

## DOCUMENT

### The opinion in {citation}

Every line is numbered. Each issue below names the lines its passage was cut
from, but the passage is an extract and the court's reasoning often runs past
it: read around those lines before deciding what the issue turns on. A
dependency the court states once, three paragraphs later, is still a dependency.

{opinion}

### The contracts filed in {citation}

Each is shown in full, between markers naming its `contract_id`. These, and only
these, are the documents the case is known to hold — an instrument the opinion
mentions but that does not appear below is `external`.

{contracts}

## INSTRUCTIONS

Take the issues one at a time, in the order given.

1. **Read the clause, and the opinion around the lines the issue names.** Could
   you state this defect from the clause's words alone? If so the scope is
   `clause` and you are done.
2. **Look in the same contract.** Is the missing piece a definition, a
   cross-reference, or a competing provision of the document the clause came
   from? Then `contract`.
3. **Look at the other documents above.** If one of them holds what the issue
   turns on, the scope is `case` — and `needs` names its `contract_id`.
4. **Otherwise `external`**, and `needs` says in a few words what is missing.

Answer for every issue id listed, once each, and nothing else.

## TASK

### The issues recorded in this case

Each carries the clause it was found in, the risk type step 2 assigned, the
sentence naming the defect, and the passage of the opinion it came from.

{issues}

### What to return

One entry per issue id above, in the same order.
