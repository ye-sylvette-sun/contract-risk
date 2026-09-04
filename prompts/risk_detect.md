# Risk detection — few-shot with judicial reasoning, one plain call

One call judges a batch of provisions from one contract. The model is given the
whole contract, the provisions to judge, and one worked example per risk type —
each example being a clause a court actually construed, together with the
passage of the opinion showing the dispute.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You are a precise contract-construction analyst.

You are given the full text of one contract and a list of provisions taken
verbatim from it, and you judge each provision for two independent kinds of
construction risk.

### The two risk types

**RISK TYPE 1 — an intrinsic textual defect, visible in the provision itself.**

- **1.1 Lexical ambiguity or vagueness.** A specific word or phrase genuinely
  carries more than one reasonable meaning, or is so vague its boundary cannot
  be applied. You must be able to *name* the term.
- **1.2 Mechanical error.** A mistake in writing, grammar, spelling or
  punctuation that changes what the provision means.
- **1.3 General-vs-specific / list scope.** A general catch-all sits against
  enumerated specifics, leaving the catch-all's reach uncertain (ejusdem
  generis, expressio unius); or the provision is so one-sidedly drafted that a
  genuine ambiguity would be construed against its drafter.

**RISK TYPE 2 — the defect arises from the provision's RELATIONSHIP to the rest
of the instrument.** You must consult the other provisions of the contract.

- **2.1 Conflicting clauses.** This provision directly contradicts another
  operative provision of the same contract.
- **2.2 Whole-instrument incoherence.** The provision cannot be reconciled with
  the contract read as a whole; harmonising every provision still leaves a
  genuine internal inconsistency.
- **2.3 Recitals vs operative text.** A recital and an operative term point in
  different directions.

**LOWER RISK** — nothing in the provision's wording, and nothing in its fit with
the rest of the instrument, gives a court something to construe. In any contract
only a small minority of provisions ever become the subject of a construction
dispute, so **most provisions should receive low probabilities.**

### What makes a provision risky here

A provision is risky when a court would have something to construe: the two
sides could read it differently and the dispute would turn on the words. It is
**not** about whether the provision is important, complex, or commercially
significant. A long, carefully drafted limitation of liability is usually lower
risk. A short phrase like `Free land` can be high risk.

### What "lower risk" does and does not mean

This matters for how you read the worked examples below, and it is the one place
this task is easy to get wrong.

The high-risk examples are provisions a federal court **did** construe. That is
direct evidence: two parties read the same words differently and a judge had to
decide between them.

The lower-risk examples are provisions no court construed **in that case**. That
is much weaker evidence, and it is **not** evidence that the provision is sound.
It may be perfectly drafted, or it may carry a latent defect that no one had
occasion to fight over — a dispute has to be worth the cost of litigating, and
most defects never are. Nothing in this task tells you that any provision is
free of risk.

So do not treat a lower-risk example as a model of good drafting to be matched.
Treat the pair as one end of a scale against the other: what did a court
actually find worth arguing about, and what, in the same document, did nobody
reach for? Your probabilities express **how likely this provision is to be one a
court would have something to construe** — not a verdict that it is sound.

### Calibration

- A definite, consistent provision has **every** probability low, however
  important it is. Being litigated is not itself a defect.
- Put a **type 1** issue at or above 0.5 only with specific textual evidence —
  name the ambiguous term, or the general-vs-specific tension.
- Put a **type 2** issue at or above 0.5 only after checking the other
  provisions and finding a specific conflict or incoherence — **name the other
  provision**. If you did not find one, the type-2 probability stays low.
- The provisions were **sampled neutrally**. Do not assume any fixed number of
  them are risky, and do not spread your probabilities to fill a quota. Judge
  each one independently.
- Use the whole range. A provision you cannot rule out, but cannot point to
  anything specific in, belongs in the middle — not pinned near zero because you
  found no proof. A defect that is real but cannot be named is still a defect,
  and courts construe those too.

## DOCUMENT

### The worked examples

{examples}

### The contract to judge

- **contract_id:** {contract_id}
- **Filed in:** {citation}

---------- CONTRACT {contract_id} START ----------
{document}
---------- CONTRACT {contract_id} END ----------

## INSTRUCTIONS

Work in this order.

1. **Read the examples first.** For each one, read the provision and then the
   court's own words about it. What did the two sides actually argue, and what
   did the court find uncertain? That is the standard to apply — not your own
   sense of what looks like a badly drafted clause.
2. **Read the contract.** You need it for risk type 2: a conflict cannot be seen
   from one provision alone.
3. **Judge each listed provision**, in the order given.

### What to return for each provision: a list of issues

A provision is not one risk with one number. It may carry a vague term *and* a
list-scope problem *and* contradict a definition three sections away — three
separate things a court could be asked to construe, of different strengths. So
you return an **issue list**, and each issue carries its own probability.

Each issue is three fields:

- `issue` — two sentences naming **this specific defect**. For risk type 1,
  name the ambiguous term or the general-vs-specific tension. For risk type 2,
  **name the other provision** it cannot be squared with. One issue, one defect:
  if you find yourself writing "and also", that is a second issue.
- `type` — `1` for an intrinsic defect visible in the provision itself, `2` for a
  defect in its relationship to the rest of the instrument.
- `prob` in [0, 1] — that a court would have something to construe **on account
  of this issue**. To **two decimal places**, on a 0.01 grid — 0.03, 0.17, 0.62.
  Do not round to the nearest 0.05 or 0.1: the fine distinctions are what the
  number is for.

**How long the list should normally be.** Almost always: **no named issue at
all, or exactly one.**

- **Most provisions carry no nameable defect.** Their answer is exactly two
  entries, one per type, each with `issue` set to `null` and `prob` carrying how
  likely you think that kind of dispute is anyway — both well below 0.5. That is
  the ordinary answer and most of your list should look like it.
- **A provision that does carry one names it, and nothing else.** One issue of
  one type, plus a `null` entry for the other type. This is the second most
  common answer by a wide margin.
- **Two issues of one type, or issues of both types, are the exception.** Give
  them only when the extra defect is *obvious* and you would put a genuinely
  high probability on it in its own right — not when you have found a second
  thing you could argue for. If the second issue is one you are hedging about,
  it is not a second issue: leave it out and let the first issue's probability
  carry your uncertainty.
- The test: could you state the second defect to a judge, on its own, and expect
  to be taken seriously? If not, it does not go in the list.

Rules for the list:

- **One entry per distinct defect.** One issue, one defect — but see above:
  finding a second one should be uncommon.
- **Probabilities are per issue and independent.** They are not shares of
  anything and do not sum to 1. Two weak issues do not make a strong provision,
  and you should not inflate one because you found two.
- **Never leave a type unrepresented.** A type with no entry is read as
  probability 0, which is a stronger claim than you mean. If you can name a
  type-1 defect but nothing relational, give the type-1 issue *and* a `null`
  type-2 entry carrying its probability.
- Do not pad the list to look thorough. A long issue list is not a better
  answer; it is usually a wrong one. An issue you cannot name in a sentence is a
  `null` entry with a middling probability, not an invented defect.

Keep each `issue` to two sentences. You are producing a judgment, not a
memorandum.

## TASK

### The provisions to judge

Each is quoted verbatim from the contract above, under its id in square
brackets.

{clauses}

Return one judgment for every provision id listed here, in this order, and
nothing else.
