# TODO

## Next: let step 1 report every dispute over a clause, not just one

Today's build records **one contiguous opinion passage per clause** —
`opinion_comment_start_line` / `opinion_comment_end_line` in
`prompts/extract.schema.json`, cut by `step1_extract.py` and stored as
`opinion_comment`. A court that construes the same clause in two separate
places is captured once, and the second passage is lost.

**The change.** Have the extraction model return a **list of disputes** per
clause instead of a single line range. One dispute = **one issue under one risk
type**, so a clause construed for a vague term *and* for a conflict with
another section yields two entries, each with its own passage and its own
taxonomy code.

**Where two disputes are the same problem, report one.** A court returning to
the same defect later in the opinion is not a second dispute. The test is
whether the second passage would need a different `issue` sentence to describe
it — if the same sentence covers both, it is one dispute with two passages, and
one entry is enough. This mirrors the parsimony rule the prediction prompt
already uses (`prompts/risk_detect.md`, "How long the list should normally be").

### Why it is worth trying, and what it will and will not buy

What is already measured, and should be re-read before starting:

- The recorded passage is a **median 4.2%** of the opinion (median 5 lines of
  99), and within a case **69%** of passage pairs are disjoint. Courts really do
  discuss different clauses in unconnected places.
- But re-judging the 78 misaligned issues against the **whole opinion** flipped
  only **9** of them. The ceiling on alignment from perfect passage capture is
  therefore about **+4.7 points** (59.2% → 63.9%), and 88.5% of misalignments
  are the model naming a defect the court never construed anywhere.
- Giving the judge more text is not free: of those 78, 38 scored higher but
  **25 scored lower**, because a whole opinion is mostly about other clauses.
  Multi-passage capture must stay tight or it will dilute what it improves.

So the case for doing it is **not** the alignment number. It is the measurement
problem underneath:

- **The recall denominator is currently unknowable.** Scoring counts 222 gold
  `(clause, risk type)` targets, but a single passage may hold several distinct
  defects of one type, and the model is capped at one issue per target by its
  own parsimony rule. What is reported as recall is really **target coverage**,
  and it is an upper bound. A dispute list gives a real denominator.
- It would let the issue-level figure
  (`plot_issue_alignment_thresholds.py`) say "found 2 of the 3 defects the
  court construed" rather than "hit the target".

### What has to change

| | |
|---|---|
| `prompts/extract.schema.json` | `disputes: [{opinion_comment_start_line, opinion_comment_end_line, taxonomy, issue}]` in place of the single range + `taxonomy` array |
| `prompts/extract.md` | the parsimony rule above; one entry per distinct defect, not per passage |
| `src/step1_extract.py` | `check()` validates each dispute's range; the record carries the list |
| `src/build_dataset.py` | decide the row shape — one row per clause with a dispute list, or one row per dispute (this changes every downstream join, so decide it first) |
| `src/experiments/issue_alignment_check.py` | judge against the dispute whose taxonomy matches, and count targets per dispute rather than per `(clause, type)` |
| `docs/DATASET.md` §6, `docs/EXPERIMENTS.md` §10 | the two limits recorded there stop being true and must be rewritten, not deleted |

**Cost.** Step 1 is 62 calls (one per case) on `gpt-5.6-sol` at high effort —
cheap to re-run. Step 2 and the risk-detection run are untouched, so the
100-contract agent run does **not** need repeating; only
`issue_alignment_check.py` re-runs, at 191-ish calls.

**Keep the old dataset.** Branch `legacy_multi_issue_experiment_9.3` holds the
single-passage build and every number quoted above, so the two can be compared
rather than one replacing the other.

### Do not lose these while changing it

- `taxonomy_provenance` — all 35 multi-code positives are `model`, by
  construction. If a dispute list makes the model assign a code per dispute,
  that column's meaning changes and the "westlaw is a pure Westlaw fact" claim
  has to be re-stated.
- The verbatim discipline: the model reports line ranges, never text.
  `build_dataset.py` re-cuts every row from disk and refuses to write unless it
  reproduces `clause_text` exactly. That must survive.

## Smaller, still open

- **Human spot-check of the gold link.** Nothing verifies that the recorded
  passage discusses the clause it is attached to — the extraction model made
  that link and the heuristic that once second-guessed it was removed. Thirty to
  fifty positives read by hand would put a number on it.
- **Run-to-run variance is unquantified.** No seed, no temperature control. One
  repeat under the previous design moved ROC-AUC by ~0.02 and recall@0.5 by
  ~0.10. Three paired runs over a fixed stratified subset would bound it.
- **`docs/REPORT.md` has no issue-alignment section.** The method is written up
  in `docs/EXPERIMENTS.md` §10; the results (59.2%, the two controls, the
  whole-opinion diagnostic, the issue-level figure) are not yet in the report.
- **`replay_anchors.py` now scores 99.4% of anchors matching, 97.7% exact.**
  That number belongs in `docs/DATASET.md` under "what guarantees the text",
  where there is currently no figure at all.
