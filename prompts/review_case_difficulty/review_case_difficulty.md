# Review difficulty — how hard is it to understand one case and what its court construed?

One call per case. A human reviewer will later check whether descriptions of
contract defects match the defects a court actually construed. Before they can
do that, they have to understand the case and each of the court's defects. This
prompt asks how hard that understanding is for this case, and why.

Only the court's defects are shown — no model's predictions and no judge's
calls — so the difficulty is a property of the case, not of any model's output.

The difficulty factors come from a law-trained reviewer's account of her first
reviews (Slack, October 2026): one case took about ten minutes and she was
confident; another took about three hours and she was still unsure. The
material here describes those factors without naming either case, so the cases
she reviewed can be used to test this prompt.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You are an experienced litigator who has supervised many document reviews. You
are asked to estimate how hard one case is to **understand** for a human
reviewer, and to say why.

### What the reviewer has to understand

The reviewer is law-trained but not a specialist in the case's industry. They
are given the court's opinion, the contracts filed in the case, and the list of
**court defects**: each provision the court construed, and for each one every
distinct defect the court construed in it, with the passage of the opinion it
comes from.

Later the reviewer will be shown other descriptions of defects and asked whether
each one names one of these court defects. To be ready for that, they must
understand, for every court defect:

1. what the provision means, and what the words at issue are;
2. what the parties fought over — the two readings, and who wanted which and
   why;
3. what the court actually construed, as distinct from the other arguments
   around it.

You are judging how hard it is to reach that understanding. Nothing else.

### What does not count

Difficulty is how hard the case and its court defects are to **understand**:
the contract, the dispute, and what the court construed. Two things can look
like difficulty and are not:

- **The number of court defects does not matter.** A case with ten defects
  that are each plain is easy; a case with one defect that takes an afternoon
  to grasp is hard. Judge how hard it is to understand the defects, never how
  many there are, and do not add time per defect.
- **The length of the documents does not matter by itself.** The reviewer
  reads what understanding the defects requires, not every page. A long
  contract adds difficulty only where understanding a defect means piecing
  together provisions scattered through it; a long opinion, only where the
  court's reasoning is spread through it.
- **Whether the court was right, or how the case came out, does not matter.**

### What makes a case easy to understand

- **The defect sits in one clause, often one phrase.** The court quotes the
  words and sets out the two readings.
- **The doctrine is familiar.** An undefined term, a vague standard, an
  ambiguous condition or deadline — general contract law that a law-trained
  reader already knows. No industry knowledge is needed.
- **The court states the dispute plainly.** The reviewer can say in one
  sentence what was construed, after one read of the passage.

A case like that takes minutes, and the reviewer ends confident.

### What makes a case hard to understand

Each of these adds time, and they compound:

- **The transaction has to be learned first.** Who the parties are, what each
  gave and got, what went wrong, and **who wants what procedurally** — which
  forum, which remedy, which stage of the case, and why. A reviewer may have to
  work this out before they can read a single defect.
- **Specialised knowledge is needed.** A technical product or market, an
  industry's regulatory scheme, a specialised body of law, or a line of
  precedent the court relies on. The reviewer has to look up background before
  the words at issue make sense.
- **The defect spans several provisions, or another document.** One provision
  read against another elsewhere in the instrument, or a term that makes sense
  only against a separate agreement, schedule or document. The reviewer has to
  reread the contract, and may need to go through much of it.
- **The court's reasoning is hard to pin down.** The construction is entangled
  with a procedural ruling, spread across the opinion, carried by precedent
  rather than by the words, or the court decides on a narrow or procedural
  ground and leaves the broader dispute unresolved. The reviewer has to
  separate what the court construed from the parties' wider arguments.
- **The court's defects are hard to tell apart.** Two defects share a passage,
  overlap in the words at issue, or one is a relational reading of the other,
  so it is unclear where one ends and the next begins.

A case like that can take hours, and the reviewer may still be unsure at the
end.

### The scale

- **`difficulty`** — `easy`, `intermediate` or `hard`.
- **`score`** — an integer from 1 to 10, and use the whole range. 1–3 is easy:
  1–2 when every defect is plain on one read of the clause and its passage, 3
  when a little context is needed. 4–6 is intermediate. 7–10 is hard: 9–10 when
  even a careful reader may stay unsure what the court construed. `difficulty`
  and `score` must agree.
- **`estimated_minutes`** — your estimate of how long a law-trained
  non-specialist needs to understand the case and every court defect well
  enough to recognise each one in someone else's words. Count the reading that
  understanding requires — the clause, the provisions it must be read with, the
  court's reasoning, any background — not a read of every page. Do not add a
  fixed amount per defect or per page: the time is set by how hard the case and
  its defects are to understand, not by how many defects there are or how long
  the documents run.

## DOCUMENT

### The opinion in {citation}

Every line is numbered.

{opinion}

### The contracts filed in {citation}

Each is shown in full, between markers naming its `contract_id`.

{contracts}

## INSTRUCTIONS

Work in this order.

1. **Read the opinion for the dispute.** What is the transaction? What does each
   side want, and what posture is the case in? How much would a law-trained
   non-specialist have to learn before they could follow it?
2. **Go through the court defects one at a time.** For each one, ask what the
   reviewer must read to understand it: the clause alone, other provisions,
   another document, the opinion beyond the quoted passage, or outside
   knowledge. Ask whether the court states it plainly, and whether it is clearly
   separate from the other defects.
3. **Judge the case by its hardest parts, not by its count.** One hard defect
   makes a case hard; many plain defects leave it easy.

Rate each factor in `factors` by how much it adds to the difficulty of
understanding this case: `low`, `medium` or `high`. Give each a one-sentence
note specific to this case — name the provision, the term or the background
involved.

In `hardest_defect`, name the court defect (by its id, e.g. `d03`) that is
hardest to understand, and say why in one or two sentences.

In `reason`, give three to five sentences that someone deciding which reviewer
should take this case could act on.

## TASK

### The court defects in {citation}

{packet}

### What to return

One assessment of this case.
