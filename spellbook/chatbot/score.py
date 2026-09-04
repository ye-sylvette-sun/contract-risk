"""Score the Spellbook results against gold, beside the two existing arms.

Reads every non-empty spellbook/chatbot/output/<cid>.json, maps the opaque provision
ids back to dataset clause ids through the id map stored with the one-shot
run, and scores the same three panels as src/experiments/compare_risk_detect.py.

A provision Spellbook did not judge is scored as not_risky at probability 0 --
never dropped. Dropping it would forgive the omission and, on a positive,
inflate recall.

The llm_api and agent rows are restricted to the SAME contracts, so the three
are comparable; a run scored on different documents differs as much in which
contracts it covered as in anything about the method.

Usage:
    python spellbook/chatbot/score.py
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent          # spellbook/chatbot/
ROOT = HERE.parents[1]                          # the repo root
sys.path.insert(0, str(ROOT / "src" / "experiments"))
import predictions  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# No fixed threshold. Each arm is given the operating point that reaches
# TARGET recall on the risky-vs-not panel, and every panel is then reported
# there. A fixed cut-off compares arms at whatever recall each happens to
# deliver; this asks the question a reviewer actually has -- "to catch four
# fifths of what was litigated, how much of the contract must I read?"
TARGET = 0.80


# A backslash before anything that is not a legal JSON escape. Spellbook
# sometimes emits `"judgments"\: [` -- every colon escaped -- which is invalid
# JSON AND defeats the salvage regexes, since they look for `"key"\s*:`. The
# legal escapes (" \ / b f n r t u) are left alone, so a properly escaped inner
# quote inside a reasoning string survives untouched.
BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def clean(text):
    """Undo escaping Spellbook applies that JSON does not allow."""
    return BAD_ESCAPE.sub("", text)


def results_for(cid, out):
    """One contract's judgments, from wherever they were pasted.

    output/<cid>.json first; failing that the raw paste in output/raw/<cid>.txt,
    read through the same salvage reader the agent arm uses -- so a paste with
    unescaped inner quotes scores without being repaired first.

    Returns (judgments, source label, note). `judgments` is None when nothing
    was pasted, and an EMPTY LIST when something was pasted but no judgment
    could be read out of it -- the caller must report that rather than skip it,
    or a corrupt paste silently leaves a contract out of the comparison.
    """
    for path, label in ((out / f"{cid}.json", "json"),
                        (out / "raw" / f"{cid}.txt", "raw")):
        if not path.exists() or path.stat().st_size <= 2:
            continue
        text = clean(path.read_text(encoding="utf-8", errors="replace"))
        judgments, note = predictions.salvage(text)
        return judgments, label, note
    return None, None, None


def f(x):
    try:
        return min(max(float(x), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def roc_auc(sc, lb):
    if not any(lb) or all(lb):
        return float("nan")
    order = sorted(range(len(sc)), key=lambda i: sc[i])
    ranks = [0.0] * len(sc)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and sc[order[j]] == sc[order[i]]:
            j += 1
        for k in range(i, j):
            ranks[order[k]] = (i + j - 1) / 2.0 + 1.0
        i = j
    p = sum(lb)
    return (sum(r for r, y in zip(ranks, lb) if y) - p * (p + 1) / 2.0) / (p * (len(lb) - p))


def pr_auc(sc, lb):
    if not any(lb):
        return float("nan")
    order = sorted(range(len(sc)), key=lambda i: -sc[i])
    tp = fp = 0
    ap = prev = 0.0
    for i in order:
        tp, fp = (tp + 1, fp) if lb[i] else (tp, fp + 1)
        rec = tp / sum(lb)
        ap += tp / (tp + fp) * (rec - prev)
        prev = rec
    return ap


def threshold_at_recall(scores, labels, target=TARGET):
    """The HIGHEST threshold still reaching `target` recall, or None.

    Highest, not lowest: a higher threshold flags less, and what is being
    measured is what the recall costs the reader. Swept on the 0.01 grid the
    probabilities themselves are stated on.
    """
    best = None
    n_pos = sum(labels)
    if not n_pos:
        return None
    for i in range(101):
        t = i / 100
        tp = sum(1 for s, y in zip(scores, labels) if y and s >= t)
        if tp / n_pos >= target:
            best = t
    return best


def panel(rows, which):
    """One-vs-rest, and the two risk types are NOT exclusive — a clause whose
    case was filed under several Westlaw keys can be gold for both, and then
    counts as a positive in both panels."""
    if which == "risky":
        return ([max(f(r["p1"]), f(r["p2"])) for r in rows],
                [1 if r["gold"] != "not_risky" else 0 for r in rows])
    key = "p1" if which == "type1" else "p2"
    return ([f(r[key]) for r in rows],
            [int(r.get(f"gold_{which}") in (1, "1")) for r in rows])


def report(name, rows):
    """Every panel at the threshold THIS arm needs for TARGET recall overall.

    Each arm gets its own operating point. A single fixed cut-off would compare
    them at whatever recall each happened to deliver there, which is not a
    question anyone asks; this one is -- to catch TARGET of what was litigated,
    how much of the contract does each ask you to read?
    """
    sc, lb = panel(rows, "risky")
    t = threshold_at_recall(sc, lb)
    unreachable = t is None
    if unreachable:
        t = 0.0
    print(f"\n  {name}  ({len(rows)} provisions)  threshold {t:.2f}"
          f" for {TARGET:.0%} recall risky-vs-not"
          + ("  ! TARGET NOT REACHABLE AT ANY THRESHOLD" if unreachable else ""))
    pc, rc = f"P@{t:g}", f"R@{t:g}"
    print(f"    {'panel':7}{'pos':>5}{'ROC':>8}{'PR':>8}{pc:>7}{rc:>7}"
          f"{'F1':>7}{'TP':>4}{'FP':>5}{'FN':>4}{'flag':>8}")
    for which in ("risky", "type1", "type2"):
        sc, lb = panel(rows, which)
        n = sum(lb)
        if not n:
            print(f"    {which:7}{0:>5}     --      --     --     --     --"
                  f"   -    -   -   {sum(s >= t for s in sc) / len(sc):6.1%}"
                  f"   (no positives in these contracts)")
            continue
        fl = [s >= t for s in sc]
        tp = sum(1 for a, y in zip(fl, lb) if a and y)
        fp = sum(fl) - tp
        fn = n - tp
        p = tp / sum(fl) if sum(fl) else 0.0
        r = tp / n
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        print(f"    {which:7}{n:>5}{roc_auc(sc, lb):8.3f}{pr_auc(sc, lb):8.3f}"
              f"{p:7.2f}{r:7.2f}{f1:7.2f}{tp:4}{fp:5}{fn:4}"
              f"{sum(fl) / len(sc):8.1%}")
    return t


def arm_rows(path, cids):
    """One existing arm's predictions, restricted to `cids`, blanks zeroed."""
    keep = {}
    for r in csv.DictReader(open(ROOT / "output" / path, encoding="utf-8")):
        if r["contract_id"] not in cids:
            continue
        k = (r["contract_id"], r["clause_id"])
        prev = keep.get(k)
        if prev is not None and (prev["ok"] == "1" or r["ok"] != "1"):
            continue
        keep[k] = r
    return [{"gold": r["gold"],
             "gold_type1": r.get("gold_type1"), "gold_type2": r.get("gold_type2"),
             "p1": r["prob_type1"] if r["ok"] == "1" else 0,
             "p2": r["prob_type2"] if r["ok"] == "1" else 0}
            for r in keep.values()]


def collect():
    """(spellbook rows, contract ids covered, unjudged count, per-contract lines).

    Shared by the table and the figure so the two cannot disagree about which
    contracts are in and how a missing judgment is scored.
    """
    out = HERE / "output"
    candidates = sorted(p.stem for p in (HERE / "prompt").glob("*.txt"))

    # Gold is multi-label: `taxonomy` is a comma-separated list of codes, so a
    # positive can be both risk types at once.
    gold = defaultdict(dict)
    for r in csv.DictReader(open(ROOT / "output" / "dataset.csv", encoding="utf-8")):
        codes = [c.strip() for c in r["taxonomy"].split(",") if c.strip()]
        pos = r["label"] == "POSITIVE"
        gold[r["contract_id"]][r["clause_id"]] = {
            "gold": "risky" if pos else "not_risky",
            "gold_type1": int(pos and any(c.startswith("1") for c in codes)),
            "gold_type2": int(pos and any(c.startswith("2") for c in codes)),
        }

    sb, cids, missing_total = [], [], 0
    lines, unreadable = [], []
    for cid in candidates:
        judgments, src, note = results_for(cid, out)
        if judgments is None:
            continue
        if not judgments:
            unreadable.append(f"  {cid[:48]:50} pasted but NO judgment could be "
                              f"read ({note})")
            continue
        cids.append(cid)
        idmap = json.loads((ROOT / "output" / "risk_detect_llm_api" / f"{cid}.json")
                           .read_text(encoding="utf-8"))["_id_map"]
        got = {}
        for j in judgments:
            real = idmap.get(str(j.get("clause_id", "")).strip())
            if real and predictions.valid(j):
                got[real] = j
        for clause_id, g in gold[cid].items():
            j = got.get(clause_id)
            p1, p2 = predictions.probs_of(j) if j else (0, 0)
            sb.append({"cid": cid, "clause_id": clause_id, **g,
                       "p1": p1, "p2": p2})
        n_missing = len(gold[cid]) - len(got)
        missing_total += n_missing
        n_pos = sum(1 for g in gold[cid].values() if g["gold"] != "not_risky")
        lines.append(f"  {cid[:48]:50}{len(got):>8}{len(gold[cid]):>5}{n_pos:>7}"
                     f"  {src:4}"
                     + (f" ! {n_missing} unjudged, scored 0" if n_missing else ""))

    if not cids:
        sys.exit("no results yet — paste into spellbook/chatbot/output/<cid>.json "
                 "or spellbook/chatbot/output/raw/<cid>.txt")
    return sb, cids, missing_total, lines, len(candidates), unreadable


def main():
    sb, cids, missing_total, lines, n_all, unreadable = collect()
    print(f"{len(cids)} of {n_all} contract(s) have results\n")
    print(f"  {'contract':50}{'judged':>8}{'of':>5}{'gold+':>7}  from")
    print("\n".join(lines))
    if missing_total:
        print(f"\n  {missing_total} provision(s) unjudged overall, scored not_risky at 0")

    report("SPELLBOOK", sb)
    for name, path in (("llm_api", "risk_detect_llm_api_preds.csv"),
                       ("agent", "risk_detect_agent_preds.csv")):
        report(name, arm_rows(path, cids))


if __name__ == "__main__":
    main()
