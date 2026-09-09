# Issue-alignment check — did the model find the issue the court actually fought over?

One call per issue the risk-detection model named. That model was shown a
contract and no opinion. This asks a different model — one that never saw its
reasoning — whether the defect it named is one the court actually construed, and
**which** one.

Step 2 of the dataset build records every distinct defect a court construed in a
provision, each as ONE SENTENCE with its own verbatim passage. So the question is
not only "is this aligned" but "aligned with which recorded defect", and the
answer is what lets the experiment count how many of the court's defects were
found rather than only how many of the model's guesses were right.

**The comparison is sentence against sentence.** Both sides are one sentence
naming one defect in one provision, written in the same form, so they can be set
side by side. The opinion passage is context: it shows what the court was
construing and confirms the recorded sentence is faithful to it, but a court
often disposes of several defects in one paragraph — 53% of the recorded defects
share their passage with another defect of the same case — so the passage alone
cannot tell two of them apart.

Only issues whose provision and risk type both match the gold label reach this
prompt. Whether the provision was risky is already known from the docket; the
question is only whether the model was right **for the right reason**.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order — instructions after the document, so a rule sits next
to the text it governs. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You compare a proposed reading of a contract provision against what a United
States federal court actually said about that provision.

You are not deciding whether the provision is well drafted, whether it is risky,
or whether the proposed reading is clever. Those questions are settled or
irrelevant. You decide one thing: **does one of the recorded defects below name
the same defect the proposed issue names?**

You are comparing two SENTENCES about one provision. Do not reason from what a
court would probably have said, from the provision's general riskiness, or from
your own view of the drafting. If the material does not settle it, say so — that
outcome exists precisely so that you never have to guess.

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

### The defects the court construed in this provision under this risk type

Each candidate has two parts, and they do different jobs.

**The recorded defect** is one sentence, written when the dataset was built by a
model that had read the opinion. It names ONE defect, in the same form as the
proposed issue above. **This sentence is what you compare against.**

**The passage** beneath it is the court's own words, quoted verbatim by line
range. It is context, not the target. A court often disposes of several defects
in one paragraph, so the same passage can sit under more than one candidate and
cannot by itself tell them apart. Use it to see what the court was construing,
and to check that the recorded sentence is faithful to it.

{candidates}

## INSTRUCTIONS

**Your job is to pick at most one candidate.** Set `matched` to the id of the
candidate whose defect is the same as the proposed issue's, or to an empty
string when none of them is.

**What counts as the same defect.**

The candidate and the proposed issue must be about the **same defect in this
provision**: the same words, the same silence, or the same conflict with the
same other text — not merely the same provision and the same general category
of complaint.

**Start by naming the words.** For the proposed issue, and for each candidate,
say which part of the provision it is about — the term, the sentence, the
silence, the cross-reference it turns on. Two defects pointing at different
words are different defects however similar the complaints sound; two pointing
at the same words, with the same complaint about them, are the same defect.
That comparison is the judgement.

The proposed issue does not have to be the court's main point, and it does not
have to be phrased as the candidate is. It has to name the same problem.

**What does NOT count.**

- A candidate about a different defect of this provision — a different term, a
  different conflict, a different silence.
- The proposed issue names a real weakness that no candidate covers and no
  passage shows the court engaging with. A defect can be genuine and still not
  be the one litigated. That is an empty `matched`, not a low-scoring match.
- The proposed issue is so general ("this term could be read more than one way")
  that it would fit almost any dispute about almost any provision. Vagueness is
  not alignment: if you cannot point to the words in a passage it corresponds
  to, it does not match.

**Where two candidates could fit**, choose the one whose passage supports it
most directly, and say in `reason` that the choice was close. Do not split the
difference by picking neither.

**Partial alignment is expected and is what the score is for.** Having chosen a
candidate, score how far the two are the same thing:

- **0.8–1.0** — the same defect: both sentences point at the same words of the
  provision and make the same complaint about them.
- **0.5–0.8** — clearly the same underlying problem, but the proposed issue
  frames it differently, or names one part of a defect the court treated more
  broadly (or the reverse).
- **0.2–0.5** — related but not the same: same provision and same general kind
  of problem, but the specific defect differs.
- **0.0–0.2** — unrelated. Prefer an empty `matched` to a score in this band.

An empty `matched` scores 0.0.

**Quote to check, after you have compared.** `evidence` must be a span copied
**exactly** from the passage of the candidate you chose — the words showing the
court engaged with what that recorded sentence describes. Copy it character for
character, including any OCR damage. This is a check on the recorded sentence,
not the comparison itself: if nothing in the passage shows the court construing
this provision at all, the recorded sentence cannot be relied on and
`determinable` is false.

**When you cannot tell.** A passage is cut from the opinion by line range and
may begin or end mid-argument. Set `determinable` to false, and score 0.0, when
the recorded sentence cannot be checked against its passage at all. Do NOT use
it merely because the answer is close, and do not use it because a passage is
long or covers several defects — that is ordinary, and the recorded sentence is
still what you compare against.

## TASK

Does one of the recorded defects name the same defect the proposed issue names?
If so, which one, and how closely?

Answer as JSON matching the schema. Name the words each sentence is about
before you decide, and quote from the chosen candidate's passage to confirm it
before you settle on a score.
