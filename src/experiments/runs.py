"""What a risk-detection run reads and what it writes.

The dataset rows an experiment is given, the `preds.csv` row it produces, and
the scoring both sides agree on. Shared, so the run and everything that reads
its output cannot disagree about what a column means.

There was a second arm — a one-shot LLM API call per contract — that this file
was carved out of when that arm was retired. Only the agent run remains; the
history is on `2026.9.3_legacy_multi_issue_experiment`.

Imported by:
    risk_detect_agent.py      the run
    compare_risk_detect.py    scoring one or two runs
"""
import csv
import json
import statistics
import os
import random
import re
import sys
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402
import predictions  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# The probability at which `pred` is called risky. A reporting default, not a
# property of the run: every figure sweeps the threshold, and this only fixes
# what the `pred` column says.
FLAG = 0.5

# `gold_subtype` is ground truth and free to carry; there is no predicted
# subtype — only the two risk types are asked for.
#
# `gold_type1` / `gold_type2` are separate columns rather than one label because
# a positive can be BOTH: a case filed under several Westlaw keys can have a
# clause whose dispute turned on two risk types at once. `gold` stays as the
# coarse risky/not_risky so the binary panel needs no lookup.
#
# `issues` carries the model's whole issue list as compact JSON. `prob_type1`
# and `prob_type2` are derived from it — the strongest issue of each type — and
# are what the panels score, so the scoring path never parses the list again.
FIELDS = ["contract_id", "citation", "clause_id", "clause_name",
          "label", "taxonomy", "taxonomy_provenance",
          "gold", "gold_type1", "gold_type2", "gold_subtype", "pred",
          "prob_type1", "prob_type2", "n_issues_type1", "n_issues_type2",
          "issues", "ok"]

# Reading a judgment the agent wrote by hand lives in `predictions.py`, which is
# also mounted into the container, so host and container cannot disagree.
issues_of = predictions.issues_of
probs_of = predictions.probs_of


# ------------------------------------------------------------------ gold ----
def gold_of(row):
    """The binary label. Positive means a court construed the clause at all."""
    return "not_risky" if row["label"] != "POSITIVE" else "risky"


def gold_types(row):
    """(is type 1, is type 2) — NOT exclusive.

    `taxonomy` is a comma-separated list of codes since step 1 began letting a
    multi-key case give a clause more than one, so a positive can be both.
    """
    if row["label"] != "POSITIVE":
        return 0, 0
    codes = [c.strip() for c in row["taxonomy"].split(",") if c.strip()]
    return (int(any(c.startswith("1") for c in codes)),
            int(any(c.startswith("2") for c in codes)))


def gold_fine(row):
    return row["taxonomy"] if row["label"] == "POSITIVE" else "none"


def _f(x, default=0.0):
    try:
        return min(max(float(x), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def _int(x, default=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def pred_from_probs(p1, p2):
    """Coarse label at FLAG. Both below -> not_risky; else the likelier type."""
    if p1 < FLAG and p2 < FLAG:
        return "not_risky"
    return "risky_type1" if p1 >= p2 else "risky_type2"


def pred_row(cid, c, judgment):
    """One `preds.csv` row for one provision. `judgment` is None if unanswered.

    The single place a row is built, so the run, its gap-filling pass and
    everything that scores the output cannot disagree about what a column means.
    An unanswered provision is written with `ok=0` and blank probabilities —
    never dropped and never silently scored, which the reader then treats as
    not_risky at 0.
    """
    g1, g2 = gold_types(c)
    issues = issues_of(judgment) if judgment else []
    p1, p2 = probs_of(judgment) if judgment else ("", "")
    return {
        "contract_id": cid, "citation": c["citation"],
        "clause_id": c["clause_id"], "clause_name": c["clause_name"],
        "label": c["label"], "taxonomy": c["taxonomy"],
        "taxonomy_provenance": c.get("taxonomy_provenance", ""),
        "gold": gold_of(c), "gold_type1": g1, "gold_type2": g2,
        "gold_subtype": gold_fine(c),
        "pred": pred_from_probs(p1, p2) if judgment else "",
        "prob_type1": p1, "prob_type2": p2,
        "n_issues_type1": sum(1 for t, _p, _x in issues if t == 1) if judgment else "",
        "n_issues_type2": sum(1 for t, _p, _x in issues if t == 2) if judgment else "",
        "issues": json.dumps(judgment.get("issues") or [], ensure_ascii=False)
                  if judgment else "",
        "ok": "1" if judgment else "0",
    }


def flat(text):
    return " ".join(str(text).split())


# Sentence-enders that are really abbreviations. A split after these is what put
# an excerpt's first words in the middle of `See Galli v. Metz, 973 F.2d 145`.
_ABBREV = {"v", "no", "inc", "co", "corp", "cir", "supp", "ct", "ed", "rev",
           "stat", "univ", "cal", "mass", "id", "ex", "art", "sec", "para",
           "assn", "dept", "natl", "intl", "pp", "vol", "ch", "fed", "f", "u",
           "s", "n", "y", "e", "d", "l", "p", "a", "r", "i"}

# What a citation looks like. A passage full of them is the court reciting the
# law of contract construction, which every opinion does and which says nothing
# about the provision in hand.
_CITE = re.compile(r'\d+\s?F\.\s?(?:Supp\.?|\dd|App)|\d+\s?[APN]\.\s?\dd|'
                   r'\b\d+\s?U\.S\.|Cir\.\s?\d|\(\d{4}\)|quotation marks omitted|'
                   r'internal citations|\bSee\b|\bid\.\b|§', re.I)

# A window that opens on one of these opens mid-citation.
_LEAD = re.compile(r'^(?:Exhibit|Ex\.|Id\.|See|Cf\.|Compare|Accord|Supra)\b'
                   r'|^.{0,34}?\bat\s+¶?\s*\d')

# Words that mark the court describing a CONTEST rather than stating a holding.
#
# `(?<!un)ambigu` because the negated form means the opposite and is what a
# holding says. Counting "unambiguously" as a dispute cue is what put the 2.2
# example's excerpt on `Accordingly, the Court finds ... unambiguously permits`
# — the court's disposition — instead of on `the two provisions appear to
# conflict`, which is the incoherence the example exists to show.
_CUES = re.compile(r"(?<!un)ambigu|disput|\bargues?\b|\bcontends?\b|\basserts\b"
                   r"|must determine|\bconflict|does not define|\bundefined\b"
                   r"|silent as to", re.I)

# The court disposing of the question. A worked example wants the defect, not
# the answer to it: an excerpt of the holding teaches the model to write
# verdicts, and `issue` is not a verdict.
_HOLDING = re.compile(r"\baccordingly\b|the Court (?:finds|concludes|therefore)"
                      r"|for the foregoing reasons|\bwe hold\b"
                      r"|\bis (?:DENIED|GRANTED)\b"
                      r"|it is clear from its plain language", re.I)

_STOP = set("""the a an and or of to in on for with that this these those is are
was were be been being it its as by at from any all such other than not no if
then shall will may must under upon which who whom whose their there here what
when where how each both same own more most some only very can also into over
""".split())


def content_terms(*texts):
    """The distinctive words of a provision — what a passage about it echoes."""
    out = set()
    for t in texts:
        for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", t or ""):
            if w.lower() not in _STOP:
                out.add(w.lower())
    return out


def _bounds(text):
    """Offsets a sentence may begin at, abbreviations excluded."""
    b = [0]
    for m in re.finditer(r'(?<=[.?!])\s+(?=[“"‘\'(A-Z])|\n+', text):
        tail = re.findall(r"[A-Za-z']+\.?$", text[:m.start()].rstrip())
        if tail and tail[0].rstrip('.').lower() in _ABBREV:
            continue
        b.append(m.end())
    b.append(len(text))
    return sorted(set(b))


def court_excerpt(comment, terms, cap=700, floor=300):
    """The court's own words on THIS provision's defect, as ONE contiguous run.

    Contiguous by construction — the return value is a verbatim substring of
    `comment`, never sentences stitched from different places, which would read
    as the court saying something it did not say in that order.

    Scored on how much of the provision's own vocabulary the window echoes,
    because a passage that argues about a clause quotes it; plus a smaller
    weight on words that mark a contest, so the excerpt lands on what was
    disputed rather than on the holding; minus citations, which is what
    separates the argument from the recital of law around it. Cue words alone
    were tried first and chose boilerplate twice out of three: a standard-of-
    review paragraph is the densest "ambiguous … construe … interpret" text in
    any opinion and is about no provision at all.
    """
    text = (comment or "").strip()
    if len(text) <= cap:
        return text
    bounds = _bounds(text)
    best = None
    for i, a in enumerate(bounds[:-1]):
        for b in bounds[i + 1:]:
            n = b - a
            if n > cap:
                break
            if n < floor:
                continue
            w = text[a:b]
            wl = w.lower()
            score = (sum(1 for t in terms if t in wl)
                     + 0.6 * len(_CUES.findall(w))
                     - 1.5 * len(_CITE.findall(w))
                     - 1.0 * len(_HOLDING.findall(w))
                     - (2.0 if _LEAD.match(w) else 0.0))
            key = (score, n, -a)          # ties: the fuller window, then earlier
            if best is None or key > best[0]:
                best = (key, a, b)
    return text[best[1]:best[2]].strip() if best else text[:cap]


# The three worked contracts, named outright. One carries only risk type 1, one
# only risk type 2, one both — the third shape had no instance at all before,
# and it is the one that shows a contract failing in two different ways.
#
# Named rather than derived. Every scoring rule tried here — shortest opinion,
# cheapest per defect taught, fewest provisions, smallest share of the
# evaluation set — picked a different three, and none of them expressed what
# actually makes a contract worth teaching from. These were chosen by reading
# them: short enough to show whole, every recorded defect carrying the court's
# own words, and opinion passages short enough that three of them do not crowd
# out the contract being judged.
#
# The type-1 example teaches FOUR defects to the type-2 example's two, so the
# three together run 5 type-1 to 3 type-2. Gold runs 2.3 to 1 (149 type-1
# defects against 65), and a set of examples weighted the other way is one
# suspect in the type-1 recall ceiling of 0.66 measured over 30 contracts —
# a third of type-1 gold provisions never got a type-1 entry at any
# probability. Suspect, not cause: the type test and the widened type-2
# definition changed in the same round and would explain it equally well.
#
# Re-weighting them was TRIED and did not work. Swapping the type-2 example
# for a one-defect contract took the examples from 4:3 to 5:2, and over the
# same 29 contracts the model's own output went from 0.97:1 to 0.94:1 — it
# does not copy the examples' type mix. Type-1 recall moved 0.66 to 0.70
# while type-2 fell 0.74 to 0.49, both inside the noise floor. So the mix
# here is 4:3 again, and the type-1 ceiling is a problem for the rules in
# prompts/risk_detect.md, not for which contracts teach.
EXAMPLE_CONTRACTS = {
    "type1": "20FSupp3d709_nols_student_agreement_including",
    "type2": "118FSupp3d802_membership_agreement",
    "mixed": "252FSupp3d52_guaranty_agreement",
}


KIND_NAME = {
    "type1": "risk type 1 only — every defect is in the provision's own words",
    "type2": "risk type 2 only — every defect is relational",
    "mixed": "both risk types, in one contract",
}


def gold_issues(row):
    """This row's recorded defects, each with the court's own passage attached."""
    return [g for g in json.loads(row.get("issues") or "[]")
            if (g.get("opinion_comment") or "").strip()]


def distinct_defects(positives):
    """A contract's defects, deduplicated on the issue text.

    One recorded defect can sit on several provisions: a drafter repeats a
    paragraph and step 2 records the same finding against each copy. Counting
    the copies would let a contract that teaches ONE defect twice look richer
    than one that teaches two.
    """
    return {g["issue"].strip() for r in positives for g in gold_issues(r)}


def example_kind(issues):
    """`type1`, `type2` or `mixed`, from the coarse types this contract's gold has."""
    coarse = {str(g["risk_type"])[0] for g in issues}
    return "mixed" if coarse == {"1", "2"} else "type" + coarse.pop()


def load_examples(rows):
    """The three contracts of EXAMPLE_CONTRACTS, with everything they teach.

    Load, not pick: WHICH contracts teach is settled by the constant above.
    What is left here is reading them out, checking they are what the constant
    says they are, and choosing the one uncontested provision each is shown
    beside.

    Whole contracts, not one clause each. Holding a contract out costs the
    evaluation set every provision in it, so each one shows EVERY provision a
    court construed and EVERY distinct defect the court found in it. Teaching
    from one clause per taxonomy code threw the rest of those contracts away for
    nothing.

    Each is paired with a provision from the SAME contract that no court
    construed, so the contrast is within one document. The pair is a scale, not
    a right answer.

    Raises rather than skipping. A named contract that is missing, or whose gold
    is not the kind it is filed under, means the dataset moved under the
    examples — and teaching from two contracts instead of three, or from a
    type-1 contract labelled type 2, is exactly the failure a quiet `continue`
    would hide until the run was over.
    """
    by_contract = defaultdict(list)
    for r in rows:
        by_contract[r["contract_id"]].append(r)

    examples = []
    for kind, cid in EXAMPLE_CONTRACTS.items():
        pool = by_contract.get(cid)
        if not pool:
            raise KeyError(f"worked example {cid} is not in the dataset")
        pos = sorted((r for r in pool if r["label"] == "POSITIVE"),
                     key=lambda r: r["clause_id"])
        issues = [g for r in pos for g in gold_issues(r)]
        if not issues:
            raise ValueError(f"worked example {cid} carries no defect with the "
                             f"court's own words")
        # Every defect must carry the court's words, not merely most of them: a
        # provision shown with one of its two defects silently teaches that one
        # defect per provision is the answer.
        if sum(len(json.loads(r.get("issues") or "[]")) for r in pos) != len(issues):
            raise ValueError(f"worked example {cid} has a defect with no "
                             f"opinion passage attached")
        got = example_kind(issues)
        if got != kind:
            raise ValueError(f"worked example {cid} is {got}, filed as {kind}")

        # The foil is the CLOSEST IN LENGTH to the construed provisions, not the
        # longest. Length is the strongest baseline signal in this corpus (AUC
        # 0.731), so contrasting a construed provision with the longest thing
        # nobody sued over teaches length as much as it teaches drafting — and
        # the longest provision is often a page of boilerplate that costs the
        # prompt more than it is worth.
        foils = [r for r in pool if r["label"] == "NEGATIVE"]
        target = statistics.median(len(r["clause_text"]) for r in pos)
        foil = (min(foils, key=lambda r: (abs(len(r["clause_text"]) - target),
                                          r["clause_id"]))
                if foils else None)
        examples.append({
            "kind": kind,
            "contract_id": cid,
            "citation": pos[0]["citation"],
            "positives": pos,
            "foil": foil,
            "n_pos": len(pos),
            "n_neg": len(foils),
            "n_defects": len(distinct_defects(pos)),
            "codes": sorted({g["risk_type"] for g in issues}),
        })
    return examples


def held_out(rows, examples):
    """Every contract the worked examples make unusable for evaluation.

    Not just the three example contracts: every contract filed in the same CASE.
    A case's other documents are mounted into the workspace as `context/`, so
    judging a sibling of an example would put that example's own contract in
    front of the model with its construed provisions and the court's words about
    them already given away in `examples/`. The defects are usually the same
    dispute seen from another document, which is precisely what makes them a
    leak rather than a coincidence.

    Costs more than it looks: dropping one example contract can drop several
    hundred provisions with it.
    """
    cases = {e["citation"] for e in examples}
    return {r["contract_id"] for r in rows if r["citation"] in cases}


def anonymise(clauses):
    """Present the provisions under opaque ids, in the order they appear.

    The dataset's `pos1`/`neg14` ids would put the gold label on the door of
    every provision and group the answers at the top of the list. So the model
    sees `c001`, `c002`, ... in `source_span` order — the sequence a reader
    meets them in, carrying no signal, each provision beside its neighbours,
    which is what a risk type 2 judgement needs.

    The mapping is translated back before anything reaches the predictions file,
    so `clause_id` in the output still joins to `dataset.csv` unchanged.

    Returns (provisions in document order, real id -> opaque, opaque -> real).
    """
    order = sorted(clauses, key=lambda c: int(c["source_span"].split("-")[0]))
    real_of = {f"c{i:03d}": c["clause_id"] for i, c in enumerate(order, 1)}
    return order, {v: k for k, v in real_of.items()}, real_of


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def by_contract(rows):
    """contract_id -> every clause of it, largest first so a bad call fails early."""
    g = defaultdict(list)
    for r in rows:
        g[r["contract_id"]].append(r)
    return OrderedDict(sorted(g.items(), key=lambda kv: -len(kv[1])))


def done_contracts(path, groups=None):
    """Contracts that need no further call.

    `groups` makes "done" mean COMPLETE. Without it one scored provision marks a
    contract finished, and a call that answered 1 of 85 is never revisited.
    """
    if not path.exists():
        return set()
    scored = defaultdict(set)
    for r in load_rows(path):
        if r["ok"] == "1":
            scored[r["contract_id"]].add(r["clause_id"])
    if groups is None:
        return set(scored)
    return {cid for cid, ids in scored.items()
            if len(ids) >= len(groups.get(cid, ()))}


# --------------------------------------------------------------- metrics ----
def roc_auc(scores, labels):
    """Rank-based ROC-AUC (Mann-Whitney U), ties averaged."""
    if not any(labels) or all(labels):
        return float("nan")
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and scores[order[j]] == scores[order[i]]:
            j += 1
        for k in range(i, j):
            ranks[order[k]] = (i + j - 1) / 2.0 + 1.0
        i = j
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    s = sum(r for r, y in zip(ranks, labels) if y)
    return (s - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
