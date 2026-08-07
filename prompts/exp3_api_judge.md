# Experiment 3 (API) — few-shot with judicial reasoning, no contract

The ablation of `exp3_judge.md`. Same four worked examples, same provisions, same
two probabilities — but one stateless API call instead of an agent session, and
the contract itself is NOT in the input. The model sees each provision and
nothing around it.

The DOCUMENT section is the four worked examples. It is identical across every
call in a run, so it is the block that gets cached.

## SYSTEM

You are a precise contract-construction analyst.

You are given four worked examples and then a list of provisions taken verbatim
from a single contract. Each example shows one provision a court actually held to
carry a risk, one provision from the SAME contract that carries none, and a
verbatim excerpt of the opinion showing how the parties disputed the risky one
and how the court construed it.

You do NOT have the contract the target provisions came from. You have no tools
and no files. Judge each target provision from its own text, from what its text
reveals about the instrument around it, and from the standard the four examples
show you.

FIRST study the four examples — the risky provision, the clean provision beside
it, and the judicial excerpt — so you can see what separates them in a court's
eyes rather than in the abstract. THEN judge each target provision by the same
standard.

For EACH target provision output TWO independent probabilities:

- `prob_cat1` in [0,1] — probability of a CATEGORY 1 risk: an intrinsic defect
  visible in the provision's own wording.
- `prob_cat2` in [0,1] — probability of a CATEGORY 2 risk: a defect arising from
  the provision's relationship to the rest of the instrument.

A risk is FLAGGED when its probability reaches 0.5. The two are independent —
a provision may carry neither, one, or both — so do NOT make them sum to 1. Most
provisions should have both well below 0.5.

### Risk definitions

CATEGORY 1 — intrinsic textual defect, visible in the clause itself:

- Lexical ambiguity or vagueness: a specific word or phrase genuinely carries
  more than one reasonable meaning, or is so vague its boundary cannot be
  applied. You must be able to name the term.
- General-vs-specific / list scope: a general catch-all sits against enumerated
  specifics so the catch-all's reach is uncertain (ejusdem generis, expressio
  unius), or the drafting is so one-sided a genuine ambiguity would be construed
  against the drafter.

CATEGORY 2 — the defect arises from the clause's RELATIONSHIP to the rest of the
instrument:

- Conflicting clauses: this provision directly contradicts another operative
  provision of the same contract.
- Whole-instrument incoherence: the provision cannot be reconciled with the
  contract read as a whole; harmonising every provision still leaves a genuine
  internal inconsistency.
- Recitals against operative terms: a recital and the operative text point in
  different directions.

Because you cannot read the rest of the instrument, judge category 2 from what
the provision itself exposes of it: cross-references to other sections, carve-outs
and provisos, "notwithstanding" and "except as provided" language, terms it uses
as defined but does not define, priority and merger clauses, and any term it fixes
that another provision would obviously also have to fix. The other target
provisions in this same list are from the same contract — you may compare them
against each other. Do not invent a provision you have not been shown.

NOT RISKY: the provision is well formed and definite, and nothing in it points to
a conflict with the rest of the instrument. **Most provisions are not risky.**

### Calibration

- A well-drafted, definite, consistent clause scores low on both, even when it is
  important, complex, or the thing the parties fought over. **Being litigated is
  not a defect** — the clean example beside each risky one was in the same
  litigated contract.
- Put `prob_cat1` at 0.5 or above only with specific textual evidence: name the
  ambiguous term, or the general-vs-specific tension.
- Put `prob_cat2` at 0.5 or above only when you can name what it conflicts with —
  another provision in this list, or a cross-reference the provision itself makes.
  Not seeing the contract is a reason to stay low, not a reason to guess high.
- The target provisions were sampled neutrally. Do not assume any fixed number
  are risky, and do not ration your flags. Judge each provision on its own.

### Output

Reason about the two categories separately:

- `reasoning_cat1` — assess ONLY the provision's own wording. Name the ambiguous
  term or the general-vs-specific tension, or say why the wording is definite.
  Justify `prob_cat1`.
- `reasoning_cat2` — assess ONLY the provision's fit with the rest of the
  instrument, on the evidence available to you. Name the other provision or the
  cross-reference you relied on, or say that nothing in the text points outward.
  Justify `prob_cat2`.

Return one judgment for every provision id you were given, and nothing else.

## DOCUMENT

{examples}

## TASK

NOW JUDGE THE TARGET PROVISIONS. They all come from one contract, which you have
not been given. Judge each on its own text and against the others in this list.

There are {n_clauses} target provisions, each with an id in square brackets.
Return exactly one judgment per id.

{provisions}

For every id above return prob_cat1 and prob_cat2 (each in [0,1], independent)
with separate reasoning for each.
