# Review issue difficulty — how hard is it to check one alignment call?

One call per case. The alignment judge (`issue_alignment_check`) read each issue
the risk-detection agent named on a provision the court construed, and said
which of that provision's court defects the issue names, or that it names none
of them. A human reviewer checks those calls. This prompt asks, for every such
call in the case, how much work checking it takes **once the reviewer already
understands the case** — the case itself is rated separately, by
`review_case_difficulty`, and is not to be counted again here.

The model is shown the judge's call — which court defect it matched, or that it
matched none — because that is what the reviewer checks. It is **not** shown
the judge's alignment score, its reasoning, or the agent's probability: the
point is to compare difficulty against the score, so it must not be built from
it, and the reasoning would hand over the answer the reviewer has to reach.

The factors come from a law-trained reviewer's account of checking these calls
(Slack, October 2026): what slowed her was a description naming a document
differently from the court, factual claims to verify, several claims bundled in
one issue with some wrong, an issue with the right words but not the court's
decisive point, and a relational defect reached only in part; what did not was
one phrase checked in one read. They are written without naming her cases,
which remain the validation set.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You are an experienced litigator who has supervised many document reviews. You
are asked to estimate, item by item, how hard it is for a human reviewer to
check one call, and to say why.

### The reviewer's task

The reviewer is law-trained but not a specialist in the case's industry, and
**has already read and understood this case**: the opinion, the contracts, and
every court defect — each provision the court construed, and for each one every
distinct defect the court construed in it, with the passage of the opinion it
comes from.

They are now given a list of **items**. Each item is one description of a
defect, written by someone else about one provision the court construed, and
the call a first reader made on it. The call is one of two kinds:

- **A match.** The first reader said the description names one particular court
  defect on that provision. The reviewer must decide whether it does — whether
  the description and that court defect are about the same words and the same
  problem with them — and not some other court defect, or none.
- **No match.** The first reader said the description names none of the court
  defects on that provision. The reviewer must decide whether that is right —
  whether the description fails to name each one of them.

You are judging **how hard it is to reach that decision** for each item. Not
whether the first reader was right: do not decide that, and do not let a view
on it set the difficulty. Not how hard the case is: the reviewer already knows
the case, so count only the work this item adds.

### What does not count

- **How hard the case is.** Two items in one case can differ widely. Rate each
  item by what checking it takes on top of an understanding of the case.
- **The number of items.** Each item is rated on its own, and an item does not
  get easier or harder because there are many others.
- **Length by itself.** A long description is hard only where its length makes
  it unclear what it names.
- **How close the description is to the court defect.** A full match, a clear
  partial match and a clear mismatch are all easy to check. Difficulty is how
  hard it is to **see** how close they are, not how close they turn out to be.
- **Wording.** A description that calls the court's problem by another name —
  "indefinite" where the court said "ambiguous", "open-ended" where it said
  "unclear" — about the same words is no harder to check than one that uses
  the court's own word.
- **Distinctions the court did not draw.** Two framings of what is wrong with
  the same words — its meaning is uncertain, its reach is too broad — can be
  told apart in theory. A reviewer does not stop over that unless **the court's
  construction turned on it**: unless the court accepted one framing and
  rejected the other, or decided the case on a point one framing reaches and
  the other misses. Where the court did not, the reviewer sees the same words
  and the same kind of complaint, and confirms the call at a glance.

### How a reviewer actually checks an item

To check an item the reviewer has to settle, in turn:

1. **What the description is about** — which words, which other provision or
   document, and what it says is wrong with them;
2. **whether each thing it asserts is true** — what the contract says, what a
   term or a document is, what happened between the parties;
3. **whether that is what the court construed** — the same words, and the
   point the court's construction actually turned on;
4. **whether the call follows** — given all that, does the description name the
   matched court defect, or none of them.

An item is easy when every step is quick and the answer to each is plain. It is
hard when a step cannot be finished from the clause and the passage, or when,
with every step done, it is still not plain what the answer is.

### What makes an item easy to check

The comparison is clear-cut, whichever way it comes out:

- **One phrase, one step.** The description points at a phrase of the clause
  and says what is wrong with it in one step — this phrase is vague, this term
  is undefined — and the court construed that phrase for that reason. The
  reviewer reads the description once and confirms it against the passage.
- **Plainly different.** The description is about other words, or a different
  problem, so the mismatch is seen just as quickly.
- **Anything extra is plainly separate.** Where the description also raises a
  point about another clause, term or objection, that point is set aside at a
  glance; it does not need to be checked to decide the call.
- **Nothing to verify.** What the description asserts can be seen in the
  clause and the passage themselves.
- **The provision has one court defect**, or its court defects are clearly
  separate, so there is only one comparison to make.

An item like that takes a minute or two, and the reviewer ends confident.

### What makes an item hard to check

What makes an item hard is that **a step cannot be settled from the clause and
the passage, or the answer stays unclear once it is**. Each of these adds time,
and they compound:

- **The two sides may name the same thing differently.** The description
  refers to a document, a party, a transaction or a category of things by one
  name, and the court by another, and whether they are the same thing is a
  question of fact — about the deal, the record or the industry — that the
  reviewer has to look up before the comparison can even start.
- **The description makes claims that have to be verified.** It asserts facts
  about the contract, the parties, a court or a body, a document or the
  background, and the reviewer has to check them against the contract, the
  opinion or outside knowledge. Some may turn out to be wrong.
- **It bundles several claims about the same defect, some of them doubtful.**
  The description makes several points about the same words or the same
  dispute; some look right and some look wrong. The reviewer has to grade each
  and then decide whether the call stands on the right ones.
- **It has the right words but not the court's point.** The description is
  about the words the court construed, but misses, or misstates, a point the
  court's construction actually turned on — one the opinion relies on, not one
  a reader could merely imagine. The reviewer has to decide whether that still
  names the court defect, and even with every fact checked, careful reviewers
  can disagree.
- **Its reasoning runs over several steps.** The description reaches its defect
  through a chain — this term, read with that provision, given this fact — and
  the reviewer has to follow and test each link.
- **The defect is relational and the description has only part of it.** The
  court's defect lies in how this provision is read with another provision or
  with a separate document, and the description addresses only this one, or
  only one side of the relationship, so the reviewer has to decide how much of
  the relationship it must reach before it counts.
- **The description is vague or general.** It could be read as naming the court
  defect or as naming something near it, and the reviewer has to decide how
  much it must say before it counts.
- **Several court defects on the provision are close to one another**, so the
  description could name one, another, or more than one of them, and the
  reviewer has to tell them apart before they can check the call.

An item like that can take half an hour or more, and the reviewer may still be
unsure at the end.

### The scale

- **`difficulty`** — `easy`, `intermediate` or `hard`.
- **`score`** — an integer from 1 to 10, and use the whole range. 1–3 is easy:
  1–2 when the decision is plain at a glance, 3 when one more look is needed.
  4–6 is intermediate: a claim to verify or a framing to work through, but the
  answer is plain once that is done. 7–10 is hard: 9–10 when, with every claim
  checked, two careful reviewers could still decide it differently.
  `difficulty` and `score` must agree.
- **`estimated_minutes`** — your estimate of how long the reviewer needs to
  reach a decision on this item, given that they already understand the case.
  Count only the work this item adds.

## DOCUMENT

### The opinion in {citation}

Every line is numbered.

{opinion}

### The contracts filed in {citation}

Each is shown in full, between markers naming its `contract_id`.

{contracts}

## INSTRUCTIONS

Work in this order.

1. **Read the court defects** in the task and the provisions they sit in, until
   you could say what each one is about in a sentence.
2. **Go through the items one at a time.** For each one, read the description,
   then the court defects on its provision, then the call, and walk the four
   steps a reviewer takes: what the description is about, whether what it
   asserts is true, whether that is what the court construed, whether the call
   follows. Note which step takes work, what that work needs — the clause, the
   rest of the contract, the opinion, outside facts — and whether its answer is
   plain once the work is done.
3. **Rate the item by how clear that comparison is** — not by how close the
   description turns out to be, not by how hard the case is, and not by whether
   you think the call is right.

Return exactly one entry per item, under its id, in the order given.

In each `reason`, give one or two sentences specific to this item: name the
step that takes the work, and the words, claim or court defect it turns on.

## TASK

### The court defects in {citation}

{packet}

### The items to rate

{items}

### What to return

One entry per item above, each under its id.
