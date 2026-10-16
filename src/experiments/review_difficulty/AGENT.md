# Review difficulty — handoff

Read this before you touch anything in this directory. It says what this
experiment is for, how it was designed and redesigned, how to run it on a fresh
machine, and what to do with the result.

## Why it exists

The risk-detection agent (`../risk_detect/`) names issues on each provision, and
the alignment judge (`../issue_alignment/`, `gpt-5.6-sol`) says whether each
issue is a defect the court actually construed. A human has to check those
judge calls. From a Slack thread (October 2026):

- **Yiqin Fu** (law-trained fellow student) reviewed two cases. Aspect Software
  v. Barnett (`787 F.Supp.2d 118`, a noncompete) took **~10 minutes**: one
  phrase ("reasonably likely"), familiar doctrine, checkable at a glance.
  Presbyterian Healthcare v. Goldman Sachs (`122 F.Supp.3d 1157`) took
  **~3 hours** and she was still unsure: the defect spans the merger clause and
  the forum-selection clause plus a separate "Proposal"; she had to learn
  auction-rate bonds, swaps, FINRA arbitration vs. federal court and a
  precedent; she spent ~30 minutes just on who wants what procedurally. She
  guessed a third of the queue is easy, a third hard, a third in between, and
  said she cannot review all 50+ cases this quarter.
- **Julian Nyarko** (professor) asked Ye Sun to *systematically identify the
  easy and hard cases*, and guessed that easy cases have a very high or very
  low judge relevance (alignment) score and hard ones an intermediate score.

This experiment asks an LLM to rate each case's difficulty before anyone
reviews it, so the queue can be triaged and Julian's guess tested.

## The current design (v2)

One call per case. The model sees **the case and the court's defects, and
nothing else**:

- the whole numbered opinion and every contract filed in the case;
- every provision the court construed, every defect the court construed in it
  (the dataset's `issues`: sentence, risk type, scope, opinion passage), with
  ids `d01, d02, …` across the case. A passage shared by two defects is printed
  once and referred to after.

It does **not** see the agent's issues or the judge's calls or scores. The
prompt says explicitly that difficulty is **how hard the case and its defects
are to understand**, and that neither the number of court defects nor the
length of the documents is difficulty in itself. The time estimate counts the
reading that understanding requires, not a read of every page, and adds nothing
per defect. The prompt gives no expected split across the three bands. The
difficulty factors are Yiqin's, written without naming her two cases, so those
two cases can validate the prompt.

**The model is `gpt-6.1-sol`, effort `medium`** — on purpose not the alignment
judge's `gpt-5.6-sol`. The two answer different questions about the same
cases, and the analysis below sets one against the other.

Output per case (`prompts/review_difficulty/review_difficulty.schema.json`):
`difficulty` (easy / intermediate / hard), `score` (1–10; 1–3 easy, 4–6
intermediate, 7–10 hard), `estimated_minutes`, five `factors` each with a
`level` (low / medium / high) and a note — `transaction_background`,
`domain_knowledge`, `defect_spread`, `court_reasoning`, `issue_distinctness` —
then `hardest_defect` and `reason`. The script adds `court_defects` (the count,
for analysis only) and `warnings` (score/difficulty disagreement, or a
`hardest_defect` that does not name a shown id).

**Scope.** The risk-detection evaluation set (every case except the worked
examples' cases, `runs.held_out`), computed from `dataset.csv` alone: 50 cases.
8 have no court defect in the set and are skipped, so **42 cases** are rated.

### What v1 was, and why it was replaced

v1 showed the model the court defects **plus** every agent issue and the
judge's call and reasoning on each, and asked how hard the *review* would be.
It ran on 42 of 50 cases before the OpenAI account ran out of credits (~$7.50).
Findings, which motivated v2:

- difficulty tracked **volume** — number of judged items (Spearman ρ 0.68),
  provisions, contracts — far more than anything about understanding;
- Julian's guess got only weak support: share of lukewarm judge scores
  (0.5–0.9) vs. score ρ 0.29 (p 0.09), 0.40 (p 0.017) after controlling for
  item count — not robust to the ~14 features tried;
- the model never used scores 1–2 and gave every easy case exactly 30 minutes.

v2 removes the agent and the judge from the input (so difficulty can be
compared against them without having been built from them), tells the model
the count does not matter, and asks it to use the whole 1–10 range. **All v1
results and logs were deleted on purpose.** Only the first two v1 cases survive
in git history (commit `be18601`); do not mix them with v2.

## Files

```
prompts/review_difficulty/review_difficulty.md           the prompt (SYSTEM/DOCUMENT/INSTRUCTIONS/TASK)
prompts/review_difficulty/review_difficulty.schema.json  strict output schema
src/experiments/review_difficulty/review_difficulty.py   the run
output/review_difficulty/review_difficulty.json          results, keyed by citation (written as it goes)
output/review_difficulty/llm_logs/review_difficulty/<case_id>.json
                                                         prompt sections, document digest, response, usage
```

Inputs are all committed under `output/dataset/` (dataset.csv, cases.json,
contracts.json, contracts/, opinions/). `data/` and `contract_risk/` are **not**
needed for this experiment.

## Running it on a new machine

1. **Code.** `git pull` on `main` of https://github.com/ye-sylvette-sun/contract-risk.
2. **Python.** Any env with the pins in `requirements.txt` except matplotlib,
   which this run does not use (`matplotlib==3.8.4` has no Python 3.13 wheel, so
   `pip install -r requirements.txt` fails on 3.13 — install the rest, or use
   Python 3.12). What this run needs: `openai==2.54.0`, `tiktoken==0.14.0`,
   `httpx==0.28.1`, `python-dotenv==1.2.3`, plus what `runs.py` imports.
3. **Key.** Put `OPENAI_API_KEY=...` in `.env` at the repo root (gitignored;
   `lib.py` loads it). The model is `gpt-6.1-sol`, reasoning effort `medium`.
4. **Dry run — no calls, no cost.**
   ```
   python src/experiments/review_difficulty/review_difficulty.py --dry-run
   ```
   Expect `8 case(s) skipped`, 42 cases, ~1.94M input tokens.
5. **Validate on the two known cases first**, outside `output/`:
   ```
   python src/experiments/review_difficulty/review_difficulty.py \
       --case "122 F.Supp.3d 1157" --case "787 F.Supp.2d 118" --out /tmp/rd_trial
   ```
   Goldman (`122 F.Supp.3d 1157`) should come back **hard**, Aspect
   (`787 F.Supp.2d 118`) **easy**, ideally with a wide gap in score and minutes.
   If not, fix the prompt before spending on the rest. About $0.25.
6. **Full run.**
   ```
   python src/experiments/review_difficulty/review_difficulty.py --parallel 4
   ```
   About **$5.5–6** (~2.0M input tokens at $2.50/M as cache writes, plus
   output at $10/M; the largest case is ~142k tokens, below the 272k
   long-context tier). Check current rates before trusting this.

### Resuming and failures

- The run is **resumable**: a case already in `review_difficulty.json` is
  skipped. Re-run the same command to continue.
- A failed case (dropped stream, exhausted account) is reported and the others
  keep going; the run exits non-zero listing the failures.
- A case whose call succeeded and was logged but never reached the result file
  is **recovered from its log at no cost** ("from log"), but only if the log is
  the identical request (model, prompt sections, document digest).
- **After changing the prompt**, delete `review_difficulty.json` and the logs,
  or the old answers are kept for every case already done.

## After the run: the analysis

The point is Julian's question. Per case, from
`output/issue_alignment/issue_alignment_check.json` (209 judged issues; keyed
`<contract>__<clause>__t<type>_<n>`, field `alignment` 0–1, `citation` on each
candidate), compute the mean alignment, the share of lukewarm scores
(0.5 ≤ a < 0.9), and the mean distance from 0.5. Then Spearman against `score`,
with and without controlling for the number of judged issues. Note the judge's
scores are bimodal: 140 of 209 are exactly 0 and none lie in (0, 0.5).

Also check that v2 did what it was redesigned to do:

- `score` vs. `court_defects` should be weak — if it is still strong, the "count
  does not matter" instruction did not take;
- the scores should spread over 1–10, and `estimated_minutes` should not
  collapse onto one value per band;
- the two validation cases should sit far apart.

What would actually settle it is **measured human review time** on 6–8 cases
across the bands; the LLM's difficulty is an estimate, validated so far on two
cases only.

## Things not to do

- Do not modify Yiqin's review-queue artifact
  (https://claude.ai/artifact/FoSwySdjcy78Y9XEkfd9KC). Read it only.
- Do not name Yiqin's two cases, or their specifics, in the prompt: they are the
  validation set.
- Do not show the model the agent's issues or the judge's scores in v2.
- Commit as `ye-sylvette-sun`; the repo stores line endings as written
  (`.gitattributes: * -text`), so do not let an editor or script convert CRLF
  files to LF.
