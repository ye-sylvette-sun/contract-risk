"""What experiment 3's two variants share.

The two variants differ in exactly one thing — whether the model can see the
contract the provision came from:

    exp3.py      Claude Agent SDK session, contract.md in the working directory
    exp3_api.py  one stateless API call, provision text only, no contract

Everything else has to be identical or the comparison measures nothing, so the
example selection, the gold mapping, the flag threshold and the metrics all live
here and neither variant re-implements them.

This module must not import either variant: exp3.py strips ANTHROPIC_API_KEY at
import time (the agent SDK bills the subscription), which is the opposite of
what exp3_api.py needs.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

FLAG = 0.5                      # a category is flagged at this probability

TYPES = {
    "1.1": "Category 1.1 — lexical ambiguity / vagueness",
    "1.3": "Category 1.3 — general-vs-specific / list scope",
    "2.1": "Category 2.1 — directly conflicting clauses",
    "2.2": "Category 2.2 — whole-instrument incoherence",
}

FIELDS = ["contract_id", "citation", "clause_id", "clause_name", "label",
          "gold", "pred", "prob_cat1", "prob_cat2",
          "reasoning_cat1", "reasoning_cat2", "ok"]


# ------------------------------------------------------------------- gold ---
def gold_of(row):
    if row["label"] != "POSITIVE":
        return "not_risky"
    return "risky_cat1" if row["taxonomy"].startswith("1") else "risky_cat2"


def pred_of(p1, p2):
    """Both below the flag -> not risky; otherwise the likelier category."""
    if p1 < FLAG and p2 < FLAG:
        return "not_risky"
    return "risky_cat1" if p1 >= p2 else "risky_cat2"


def clamp(x, default=0.0):
    try:
        return min(max(float(x), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------- examples ---
def load_rows():
    with (lib.OUT / "dataset.csv").open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def pick_examples(rows):
    """One (risky, clean) pair per risk type, chosen without hand-picking.

    Among the contracts carrying a positive of a type, take the one that costs
    the fewest positives to withhold from evaluation — positives are the scarce
    class, and every example contract has to leave the eval set. Ties go to the
    smaller contract. Within it, take the first positive in document order, and
    pair it with the negative whose text length is closest, so length cannot be
    what tells the two apart.
    """
    by_contract = defaultdict(list)
    for r in rows:
        by_contract[r["contract_id"]].append(r)

    examples = []
    for code in sorted(TYPES):
        cands = [cid for cid, g in by_contract.items()
                 if any(x["label"] == "POSITIVE" and x["taxonomy"] == code
                        for x in g)]
        if not cands:
            raise SystemExit(f"no contract in dataset.csv carries a {code} positive")
        cid = min(cands, key=lambda c: (
            sum(1 for x in by_contract[c] if x["label"] == "POSITIVE"),
            len(by_contract[c]), c))
        group = by_contract[cid]
        pos = next(x for x in group
                   if x["label"] == "POSITIVE" and x["taxonomy"] == code)
        negs = [x for x in group if x["label"] == "NEGATIVE"]
        if not negs:
            raise SystemExit(f"{cid} has no negative to pair with the {code} example")
        neg = min(negs, key=lambda x: (abs(len(x["clause_text"])
                                           - len(pos["clause_text"])),
                                       x["clause_id"]))
        examples.append({
            "code": code, "type_name": TYPES[code], "contract_id": cid,
            "citation": pos["citation"], "file": f"example_{code.replace('.', '_')}.md",
            "source": pos["contract_file"],
            "risky": {"name": pos["clause_name"],
                      "text": " ".join(pos["clause_text"].split()),
                      "opinion": " ".join(pos["opinion_comment"].split())},
            "clean": {"name": neg["clause_name"],
                      "text": " ".join(neg["clause_text"].split())},
        })
    return examples


def example_block(examples, with_files=True):
    """The four worked pairs as prompt text.

    `with_files` names the example contract on disk — true for the agent, which
    can open it, false for the API call, which has no files.
    """
    head = ("WORKED EXAMPLES — four risk types, one pair each. Every pair comes "
            "from a single real contract: the provision a court held to carry "
            "the risk, and a provision of the same contract that carries none. "
            "The court's own words about the risky one follow. Study all four "
            "before judging.")
    if with_files:
        head += (" Each pair's full contract is in your working directory under "
                 "the named file.")
    out = [head]
    for e in examples:
        tail = f" [file: {e['file']}]" if with_files else ""
        out.append(f"\n\n=== {e['type_name']} — {e['citation']}{tail} ===")
        out.append(f"\nRISKY — \"{e['risky']['name']}\"\n\"\"\"\n"
                   f"{e['risky']['text']}\n\"\"\"")
        out.append(f"\nHow the court construed it (verbatim from the opinion):\n"
                   f"\"\"\"\n{e['risky']['opinion']}\n\"\"\"")
        out.append(f"\nNOT RISKY, same contract — \"{e['clean']['name']}\"\n"
                   f"\"\"\"\n{e['clean']['text']}\n\"\"\"")
    return "\n".join(out)


def provision_block(clauses):
    """The target provisions, ids in square brackets — same in both variants."""
    return "\n".join(f'\n[{c["clause_id"]}] "{c["clause_name"]}"\n"""\n'
                     f'{" ".join(c["clause_text"].split())}\n"""'
                     for c in clauses)


def eval_groups(rows, examples):
    """Provisions to judge, grouped by contract, example contracts removed."""
    held_out = {e["contract_id"] for e in examples}
    groups = defaultdict(list)
    for r in rows:
        if r["contract_id"] not in held_out:
            groups[r["contract_id"]].append(r)
    return dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))


# --------------------------------------------------------------- writing ---
def done_ids(path):
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["contract_id"] for r in csv.DictReader(fh) if r.get("ok") == "1"}


def rows_of(contract_id, clauses, judgments):
    """One output row per target provision, judged or not."""
    by_id = {str(j.get("clause_id")): j for j in judgments}
    out = []
    for c in clauses:
        j = by_id.get(str(c["clause_id"]))
        ok = j is not None and "prob_cat1" in j and "prob_cat2" in j
        p1 = clamp(j.get("prob_cat1")) if ok else ""
        p2 = clamp(j.get("prob_cat2")) if ok else ""
        out.append({
            "contract_id": contract_id, "citation": c["citation"],
            "clause_id": c["clause_id"], "clause_name": c["clause_name"],
            "label": c["label"], "gold": gold_of(c),
            "pred": pred_of(p1, p2) if ok else "",
            "prob_cat1": p1, "prob_cat2": p2,
            "reasoning_cat1": j.get("reasoning_cat1", "") if ok else "",
            "reasoning_cat2": j.get("reasoning_cat2", "") if ok else "",
            "ok": "1" if ok else "0"})
    return out


# ---------------------------------------------------------------- metrics ---
def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def load_preds(path):
    """Usable predictions, first write wins — the runs append, not overwrite."""
    seen, rows = set(), []
    if not path.exists():
        return rows
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            k = (r["contract_id"], r["clause_id"])
            if r.get("ok") == "1" and k not in seen:
                seen.add(k)
                rows.append(r)
    return rows


def metrics(path, title="experiment 3", quiet=False):
    rows = load_preds(path)
    if not rows:
        print(f"no usable predictions in {path.name} yet")
        return None

    report = {"n": len(rows), "channels": {}}
    out = [f"\n=== {title} — {len(rows)} provisions judged ===",
           f"{'channel':16} {'P':>6} {'R':>6} {'F1':>6} {'TP':>4} {'FP':>4} "
           f"{'FN':>4} {'support':>8}"]
    for name, gold_is, pred_is in (
            ("category 1", lambda r: r["gold"] == "risky_cat1",
             lambda r: r["pred"] == "risky_cat1"),
            ("category 2", lambda r: r["gold"] == "risky_cat2",
             lambda r: r["pred"] == "risky_cat2"),
            ("risky vs not", lambda r: r["gold"] != "not_risky",
             lambda r: r["pred"] != "not_risky")):
        tp = sum(1 for r in rows if gold_is(r) and pred_is(r))
        fp = sum(1 for r in rows if pred_is(r) and not gold_is(r))
        fn = sum(1 for r in rows if gold_is(r) and not pred_is(r))
        p, rc, f1 = prf(tp, fp, fn)
        sup = sum(1 for r in rows if gold_is(r))
        report["channels"][name] = {"precision": p, "recall": rc, "f1": f1,
                                    "tp": tp, "fp": fp, "fn": fn, "support": sup}
        out.append(f"{name:16} {p:6.2f} {rc:6.2f} {f1:6.2f} {tp:4} {fp:4} {fn:4} "
                   f"{sup:8}")
    n_risky = report["channels"]["risky vs not"]["support"]
    out.append(f"\nprevalence: {n_risky}/{len(rows)} = "
               f"{n_risky / len(rows):.3f} risky")
    if not quiet:
        print("\n".join(out))
    return report
