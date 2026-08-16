# Step 1 — locate the clauses the parties disputed

One call per case. Which clauses did the parties dispute, and where are they in
the documents filed with the case? The risk label is NOT decided here — it
comes from the Westlaw key the case was selected under and never from the model.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order — instructions after the document, so a rule sits next
to the text it governs. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You build a research dataset of contract clauses whose drafting caused a
dispute. You are given a court opinion and the contracts filed in that case, and
you identify the clauses the parties disputed and say exactly where they sit.

### What you produce

- You **locate**. You do not transcribe.
- Your whole answer is a pointer: a line range, and a few words copied off the
  scan at each end of the clause.
- The pipeline cuts the text out of the file itself. Nothing you write becomes
  dataset text, so there is nothing to be gained by tidying it.

{clause_def}

## DOCUMENT

### The opinion in {citation}

---------- OPINION START ----------
{opinion}
---------- OPINION END ----------

### The contracts filed in this case

Each contract is wrapped in its own marker below, and that marker names its
`contract_id` at both ends.

{contracts}

## INSTRUCTIONS

### What the blocks above are

- **Every block is a contract.** Each one is a single named agreement, already
  cut out of the court filing it was attached to. You do not have to work out
  which block is a contract and which is a brief.
- Where more than one block appears, they are **different agreements** — often
  related, sometimes successive versions of the same deal. Say which one a
  clause came from with the `contract_id` in its marker.

Because each block was cut as a line range, a little of the wrapper can survive
at its edges. Expect, and read past:

- an exhibit label on the first line — `EXHIBIT A`, `EXHIBIT 64`;
- a table of contents, a cover page, or an `Execution Version` line;
- page numbers, `Page 12 of 40`, and Bates stamps anywhere in the text;
- a signature, notary or attestation block at the end;
- occasionally a court caption page ahead of the agreement itself.

None of those is a clause. But none of them means the block is not a contract
either — do not let one at the top make you skip the agreement below it.

### Take the clause from the contract, not from the opinion

- The opinion quotes the clause after an editor has cleaned it up.
- The scan of the contract is what the dataset is cut from, and the two rarely
  read the same.
- `head` and `tail` must match the contract's own lines, in whatever state the
  scanner left them.

### Which clause to report

**A clause is an answer when the opinion shows the two sides disputed it and the
court discussed it. How the court decided does not matter.**

The dataset is about drafting that causes litigation, not about who won. A
clause that had to be argued over in a federal court is risky whether or not the
court ended up agreeing with the party that drafted it — we would rather the
dispute had never arisen.

So, explicitly:

- A clause the court examined and **upheld** belongs in the list.
- A clause the court held **clear**, **plain** or **unambiguous** belongs in the
  list. That the parties needed a court to tell them so is the point.
- A clause the court construed **against** the drafter, or found ambiguous,
  belongs in the list.
- Whichever party won, and on whatever ground, does not change the answer.

What is required is evidence in the opinion itself, of either kind:

- the parties advancing **competing readings** of the clause; or
- the court **discussing or construing** the clause in its own analysis.

A clause merely quoted in passing, listed in a recital of the facts, or cited
for background with no dispute and no discussion, is not an answer.

Every clause you report must cite the passage of the opinion that shows this.
That citation is the evidence. A clause you cannot tie to the court's own words
does not belong in the list.

### The dispute must match the risk type

- The risk types listed in the task come from the Westlaw key the case was filed
  under and from that key's headnote. They are facts about the case, not
  judgements for you to make.
- Report a clause only when the dispute over it **is one of those types**. If
  the parties clearly fought over something else — a different kind of drafting
  defect, or a question that is not about the drafting at all — the clause is
  not an answer, however heated the dispute was.
- Expect this to exclude very little. Most cases are filed under a single risk
  type, and the disputes the opinion discusses are usually that type. Use it to
  drop a clause that plainly does not fit, not as a reason to be selective.

### When the answer is no clause at all

- If the disputed language is not in any block above, return an empty `clauses`
  list and say so in `case_desc`.
- That happens legitimately. The opinion may turn on an agreement that was never
  filed, or on a different one from the agreements shown; a block may be only
  part of a longer contract; and the OCR may have lost the page the clause was
  on.
- It is a valid answer. Do not reach for the nearest similar clause instead, and
  do not report a clause from the wrong agreement to avoid returning nothing.

## TASK

**Case:** {citation}

### Risk types this case was selected under

{risks}

### Headnotes

{headnotes}

### What to return

1. `case_desc` — one line: the parties, and what the construction dispute was
   about.

2. `clauses` — every clause of the contracts above that the parties disputed
   and the court discussed, whose dispute falls under one of the risk types
   listed. For each one:

   - `clause_name` — a short name including its section number.
   - `taxonomy` — which of the risk types above the dispute over this clause
     falls under. Use one of the codes listed and no other.
   - `contract_id` — the id in that contract's START marker, copied exactly.
   - `start_line`, `end_line` — the first and last line the clause occupies
     **in that contract**.
   - `head`, `tail` — copied exactly as that contract's lines show them.
   - `opinion_comment_start_line`, `opinion_comment_end_line` — the first and
     last line **in the opinion** of the passage that shows the dispute over
     this clause: the competing readings the parties advanced and the court's
     discussion of them.
