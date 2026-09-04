# Issue-alignment check — did the model find the issue the court actually fought over?

One call per issue. The risk-detection model was shown a contract and no opinion,
and it named a defect in one provision. This asks a different model — one that
never saw the risk-detection model's reasoning — whether the defect it named is one the
court actually construed, judged against the court's own words.

Only issues whose provision and risk type both match the gold label reach this
prompt. The question is never "was the provision risky" — that is already known
from the docket. The question is only whether the model was right **for the
right reason**.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order — instructions after the document, so a rule sits next
to the text it governs. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You compare a proposed reading of a contract provision against what a United
States federal court actually said about that provision.

You are not deciding whether the provision is well drafted, whether it is risky,
or whether the proposed reading is clever. Those questions are settled or
irrelevant. You decide one thing: **does the passage from the opinion show the
court engaging with the same defect the proposed issue names?**

The opinion passage is your only evidence about the court. Do not reason from
what a court would probably have said, from the provision's general riskiness,
or from your own view of the drafting. If the passage does not settle it, say
so — that outcome exists precisely so that you never have to guess.

## DOCUMENT

**Case:** {citation}
**Contract:** {contract_id}
**Provision:** {clause_name}

### The provision, verbatim from the filed contract

```
{clause_text}
```

### The risk type this issue was filed under

{type_def}

### The proposed issue — written by a model that had the contract but NOT the opinion

```
{issue_text}
```

### What the court said — verbatim from the opinion in {citation}

```
{opinion_comment}
```

## INSTRUCTIONS

**What counts as aligned.**

The court's discussion and the proposed issue must be about the **same defect in
this provision**. That means the same words, the same silence, or the same
conflict with the same other text — not merely the same provision and the same
general category of complaint.

A court often construes several things about one provision in a single passage.
The proposed issue does **not** have to be the court's main point, and it does
not have to be the only thing the court discussed. **It is enough that the
defect it names is one of the things the passage shows the court construing.**

**What does NOT count as aligned.**

- The passage discusses this provision, but over a different defect — a
  different term, a different conflict, a different silence.
- The proposed issue names a real weakness that the passage gives no sign the
  court engaged with. A defect can be genuine and still not be the one litigated.
- The proposed issue is so general ("this term could be read more than one way")
  that it would fit almost any dispute about almost any provision. Vagueness is
  not alignment: if you cannot point to what in the passage it corresponds to,
  it is not aligned.
- The passage discusses a different provision that merely resembles this one.

**Partial alignment is expected and is what the score is for.** Score the degree
to which the named defect and the litigated defect are the same thing:

- **0.8–1.0** — the passage shows the court construing this exact defect. You
  can quote the words where it does.
- **0.5–0.8** — clearly the same underlying problem, but the proposed issue
  frames it differently, or names one part of a defect the court treated more
  broadly (or the reverse).
- **0.2–0.5** — related but not the same: same provision and same general kind
  of problem, but the specific defect differs.
- **0.0–0.2** — unrelated, or the passage gives no sign the court engaged with
  this at all.

**Quote before you score.** `evidence` must be a span copied **exactly** from
the opinion passage above — the words on which you are relying. Copy it
character for character, including any OCR damage. If you cannot find a span to
copy, you do not have evidence, and `determinable` is false.

**When you cannot tell.** The passage is cut from the opinion by line range and
may begin or end mid-argument, or discuss the provision only in passing. If it
does not contain enough to judge alignment either way, set `determinable` to
false and score 0.0. This is not a failure — an unjudgeable passage recorded as
such is worth more than a guess. Do not use it merely because the answer is
close; use it when the evidence is absent.

## TASK

Does the opinion passage show the court construing the defect the proposed issue
names?

Answer as JSON matching the schema. Quote your evidence from the passage before
you settle on a score.
