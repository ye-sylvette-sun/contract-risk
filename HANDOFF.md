# Handoff

Written 2026-09-06 for the next session. Replaces `TODO.md`, which was deleted.

The dataset is finished and correct. The agent experiment has been run on 11 of
101 contracts. Everything else downstream is stale. **Nothing in this repo is
committed** — see the last section before you touch git.

---

## 1. Where the work stands

### Dataset — done, no outstanding work

```
117 contracts in the registry
 −14 two-column layout, excluded
 ───
 103 → step 1 enumerated 12,076 clauses
     → step 2 ran all 62 citations
     → 12,060 rows | 226 POSITIVE / 11,834 NEGATIVE | 310 issues
```

Both steps were verified complete against a target set recomputed from the
registry, not against a stored list: 103/103 contracts, 62/62 citations, no gaps.

The 16-clause difference between 12,076 enumerated and 12,060 rows is a
deliberate rule in `build_dataset.py`, not a failure. A clause that reproduces a
positive character for character (boilerplate repeated across endorsements,
several editions of one instrument in one case) is dropped rather than labelled
NEGATIVE, because labelling it would assert the opposite of a label the corpus
already holds. Each drop prints.

Evaluation set is smaller than the dataset: **11,921 clauses / 220 positive /
101 contracts**, after holding out the 2 contracts the prompt's worked examples
are drawn from. Do not confuse the two numbers — docs use both.

### Agent experiment — 11 of 101 contracts

`output/risk_detect_agent_preds.csv`, 1,739 rows, **every row `ok=1`**.

Run as `--shuffle --seed 0 --limit 20 --parallel 4`, stopped by hand after 11
completed. `--shuffle` was deliberate: `groups` is sorted largest-first, so a
bare `--limit N` takes the N *biggest* contracts, which is both expensive and
unrepresentative. The seed is in the manifest.

One contract (`527BR351_loan_and_security_agreement`, 403 provisions) was killed
mid-flight and logged `rc=1, 0/403 judged`. **It wrote no rows** — the process
died before the write — so there is no `ok=0` residue in the CSV. Three other
containers were stopped cleanly. The run is resumable as-is.

`output/agent_run_20.log` is that run's log. The name says 20; only 11 finished.

---

## 2. Results so far, and what they do and don't support

```
1739 provisions, 11 contracts, 0 unjudged

                     ROC-AUC   P@0.5   R@0.5   flagged   positives
risky vs not           0.931    0.19    0.30      0.9%          10
risk type 1            0.990    0.23    0.50      0.7%           6
risk type 2            0.920    0.00    0.00      0.2%           7
```

**Read the caveat first: there are 10 positives.** Only 7 of the 11 contracts
contain any positive at all. These numbers are enough to conclude the pipeline
works and to point at where to look; they are nowhere near enough to publish.
Do not let 0.931 into a document without the denominator beside it.

Three findings worth carrying forward:

**Ranking is strong, the 0.5 threshold is not calibrated.** `FLAG = 0.5` in
`runs.py` is a reporting default, and the gap between AUC 0.931 and R@0.5 0.30
is entirely that. The cost curve is the actual result:

| target recall | threshold | precision | flagged |
|---|---|---|---|
| 70% | 0.40 | 0.100 | 4.0% |
| 80% | 0.34 | 0.051 | 9.1% |
| 90% | 0.27 | 0.022 | 23.7% |

**Risk type 2 scores 0.00/0.00 at threshold, with AUC 0.920.** All 7 type-2
positives rank high and none crosses 0.5. Type 1 crossed 3 times. The reading —
consistent with what turned up contract by contract — is that the model does
detect cross-clause incoherence but scores it far more conservatively, because
the evidence has to be assembled from several places rather than read off one
clause. Two clean illustrations: `18FSupp3d456` put its 3 positives at ranks
1, 2, 3 of 191 with zero false positives, but the pure-2.2 one stalled at 0.44
while both 1.1-bearing ones reached 0.68 and 0.72; `252FSupp3d52` ranked a pure
2.2 positive 169th of 284, the one genuine miss so far. **If this holds at full
scale it is the most interesting result in the experiment** — it is exactly the
hypothesis the agentic design exists to test.

**The length confound does not hold up here — this closes an old open item.**

| | model score | clause length |
|---|---|---|
| pooled AUC | **0.931** | 0.552 |
| within-contract AUC | **0.839** | 0.591 |

The worry (recorded at length in `docs/DATASET.md` §6) was that positives are
about 2× longer than negatives corpus-wide, length alone ranks at within-contract
AUC 0.706, and the model might be reading nothing else. On these 11 contracts it
clearly is not: 0.839 against a 0.591 baseline. **Recompute this on the full run
and put it in the report next to the model's AUC** — it is the sentence that
makes the headline number mean something. `docs/DATASET.md` §6 already sets out
why the superseded build's flattering 0.523 was the artifact (its positives were
cut by a model that had read the opinion, so spans hugged the disputed language
instead of the clause containing it — a label-dependent boundary, which is worse
than the confound it concealed).

Within-contract AUC by contract, the 7 with both classes:

```
0.607  1pos/  57  874FSupp2d328_sale_and_servicing_agreement
0.682  2pos/ 284  252FSupp3d52_agreement_for_network_operator
0.810  1pos/  22  78FSupp3d520_consent_j
0.815  1pos/  28  982FSupp2d518_2007_mta
0.973  1pos/  75  266FSupp3d961_farmout_agreements_foas
0.990  1pos/  97  976FSupp2d606_employment_contract
1.000  3pos/ 191  18FSupp3d456_phoenix_accumulator_universal_life
```

Cost: ~$25 at API-equivalent rates for 11 contracts, ~25 min at parallel 4.
Extrapolates to roughly 3.5 h and ~$200-equivalent for the remaining 90.

---

## 3. Next steps, in order

**Ask before starting step 1 — it is hours of compute and the user may want a
different sample size or a different question asked first.**

1. **Finish the agent run.** Re-run the identical command; `done_contracts()`
   treats a contract as done only when *complete*, so the 11 are skipped and the
   90 remaining are picked up. Drop `--limit` to take all of them.

   ```
   python src/experiments/risk_detect_agent.py --shuffle --seed 0 --parallel 4
   ```

2. **`issue_alignment_check.py`** — one call per named issue on a gold provision
   of a matching type. Precision over issues, recall over distinct matched gold
   issues (denominator 310).

3. **The two figures** — `plot_risk_detect_thresholds.py`,
   `plot_issue_alignment_thresholds.py`.

4. **`compare_risk_detect.py`** — the panel table above.

5. **Rewrite `docs/REPORT.md`.** It currently carries the *superseded* run behind
   a warning banner; every number in it was computed on a dataset that no longer
   exists. Replace, don't append.

The alignment check and both plots were reworked for the multi-issue shape but
**have only ever been exercised on synthetic input**. The full run will be their
first live test — expect to fix something.

---

## 4. Operational notes that will cost you an hour if you rediscover them

- **`conda run -n contract-risk` fails** (exit 127, broken shell snapshot).
  Invoke the env's interpreter directly instead — find it with `conda env list`
  and call that env's `python.exe`.
- **Use Windows-style paths** for that interpreter. A `/c/Users/...` style path
  raises FileNotFoundError; `C:/Users/...` works.
- **Set `PYTHONIOENCODING=utf-8`** or cp1252 encode errors kill long runs.
- **Docker Desktop must already be running** before the agent experiment;
  otherwise the daemon npipe is missing and `image_id()` exits. Start it and
  poll `docker image inspect contract-risk-judge:0.2.139` until it answers.
- **Bash heredocs mangle `\n` and apostrophes** in this environment. Use the
  Write/Edit tools for anything with escapes, or `NL = chr(92) + "n"`.
- `replay_anchors.py` scores ~12k clauses and takes over 2 minutes — background it.

### Credentials — these are not interchangeable

| what | credential | why |
|---|---|---|
| dataset steps 0–2, alignment check | `OPENAI_API_KEY` (`gpt-5.6-sol`) | billed |
| agent experiment | `CLAUDE_CODE_OAUTH_TOKEN` | **subscription — the user requires this** |

The container is only ever passed `-e CLAUDE_CODE_OAUTH_TOKEN`
(`risk_detect_agent.py:279`). `ANTHROPIC_API_KEY` never enters it, so there is no
path by which the agent run bills the API. Startup prints `auth via token` —
check that line. The `$X.XX at API-equivalent rates` figure the run prints is a
**conversion for reporting, not a charge**.

---

## 5. Open questions, roughly by value

- **Does the type-2 threshold gap survive the full run?** See §2. Highest value.
- **Recalculate the length baseline on all 101 contracts** and put it in the
  report. See §2.
- **Human spot-check of the gold link.** Nothing verifies that a recorded opinion
  passage actually discusses the clause it is attached to — a model made that
  link and the heuristic that once second-guessed it was removed. Thirty to fifty
  positives read by hand would put a number on it. This is the weakest unmeasured
  link in the dataset.
- **Run-to-run variance is unquantified.** No seed, no temperature control on the
  agent side. One repeat under an earlier design moved ROC-AUC by ~0.02 and
  recall@0.5 by ~0.10. `compare_risk_detect.py --against <other>_preds.csv`
  exists for this; three paired runs over a fixed stratified subset would bound it.
- **A prompt bug worth fixing before the full run.** Two of 11 contracts logged
  `1 path(s) denied outside the workspace`, both identical: the agent read
  `/examples/...` instead of `/work/examples/...`. Benign — isolation caught it,
  the agent retried correctly, all provisions were judged — but the same typo
  twice in eleven says the prompt's path wording invites it. Cheap to fix, and it
  removes a recurring scary-looking warning from the logs.
- **The clause definition and what courts construe do not quite agree.** Two of
  the four step-1 misses are an endorsement's "this endorsement changes the
  policy" line, which `lib.CLAUSE` deliberately excludes as an editing
  instruction and which a court nonetheless construed. Decide this deliberately
  rather than leaving it an accident.
- **35 of 103 contracts carry an over-capture flag.** The flags reject nothing.
  The longest negative is 18,970 characters — the clearest candidate to read by
  hand.
- **43 `unlocated` entries.** The user decided not to act on these. 39 are
  documents the corpus lacks, 4 are genuine step-1 misses, and all 4 were
  verified to produce no dataset row — so zero mislabelled negatives.

---

## 6. Git — read before committing

**The entire session's work is uncommitted**, and it is large: ~1,491 deletions
(the old dataset, agent results and logs), ~260 modifications, ~36 new files.
`main` is also **3 commits ahead of `origin/main` and unpushed**. The user's
standing instruction has been not to commit yet, so confirm before you do.

The deletions are safe: everything deleted is on a backup branch, and the backup
was verified complete by comparing byte sizes and per-directory file counts
against the backup commit before anything was removed.

Legacy branches, all on `origin` — naming rule is date-first, with the year:

```
2026.8.7_legacy_pipeline
2026.8.17_legacy_agent_experiment
2026.9.1_legacy_spellbook
2026.9.3_legacy_multi_issue_experiment
```

Note `2026.9.1_legacy_spellbook`'s tip is actually dated 2026-08-26; the name
follows the old branch name's date, per the user's rule, not the tip date.

---

## 7. Standing instructions from the user

- Run the agent experiment on the **Claude Code subscription — never an API
  key**. Parallel 8 was authorised for the dataset steps; 4 for this agent run.
- Use the **`contract-risk` conda env**. Installing packages is fine; avoid
  changing versions `requirements` pins.
- **Keep only real experiment logs.** Comparison runs, tests and scratch work go
  in a temp folder and get deleted — none of it belongs in `output/`.
- The user reads and writes Chinese in this project; docs are maintained in both
  English and `_ZH` versions. Keep them in sync.
