"""Repair the JSON Spellbook writes, in place.

Spellbook quotes the contract inside a reasoning string without escaping the
inner quotes, so the paste is not valid JSON:

    "reasoning_cat1":"The recitals contain the vague phrase "no-cost ...

That is the same failure the agent arm hits, so this reuses the salvage reader
the experiment already ships (src/experiments/predictions.py): a failed parse
falls back to object-by-object, then field-by-field with patterns anchored on
the NEXT key, which is what makes them immune to unescaped quotes.

The raw paste is copied to spellbook/output/raw/<name>.txt before anything is
rewritten, and never overwritten once saved.

Usage:
    python spellbook/chatbot/fix_json.py                 # every file in output/
    python spellbook/chatbot/fix_json.py <name>          # just one
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # spellbook/chatbot/
ROOT = HERE.parents[1]                          # the repo root
sys.path.insert(0, str(ROOT / "src" / "experiments"))
import predictions  # noqa: E402

OUT = HERE / "output"
RAW = OUT / "raw"
KEYS = ("clause_id", "issues", "issue_text", "type", "prob")


def prob(x):
    """The regex path returns strings; the schema asked for numbers."""
    try:
        return round(min(max(float(x), 0.0), 1.0), 4)
    except (TypeError, ValueError):
        return None


def fix(path):
    # Paste into either one. output/<name>.json is checked first; if it is
    # still empty, the raw paste is taken from output/raw/<name>.txt instead,
    # and is then the original, so it is not backed up over itself.
    raw = path.read_text(encoding="utf-8", errors="replace")
    from_raw = False
    if not raw.strip():
        pasted = RAW / f"{path.stem}.txt"
        raw = pasted.read_text(encoding="utf-8", errors="replace") \
            if pasted.exists() else ""
        from_raw = bool(raw.strip())
    if not raw.strip():
        print(f"  {path.name}: empty, skipped")
        return

    judgments, note = predictions.salvage(raw)
    clean, dropped = [], []
    for j in judgments:
        p1, p2 = prob(j.get("prob_cat1")), prob(j.get("prob_cat2"))
        cid = str(j.get("clause_id", "")).strip()
        if not cid or p1 is None or p2 is None:
            dropped.append(cid or "?")
            continue
        clean.append({"clause_id": cid,
                      "reasoning_cat1": str(j.get("reasoning_cat1", "")),
                      "reasoning_cat2": str(j.get("reasoning_cat2", "")),
                      "prob_cat1": p1, "prob_cat2": p2})

    if not clean:
        print(f"  {path.name}: nothing recoverable — left untouched ({note})")
        return

    RAW.mkdir(parents=True, exist_ok=True)
    backup = RAW / f"{path.stem}.txt"
    if not from_raw and not backup.exists():     # never clobber the original
        backup.write_text(raw, encoding="utf-8")

    path.write_text(json.dumps({"judgments": clean}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))   # refuse to claim success blind

    ids = [j["clause_id"] for j in clean]
    dupes = len(ids) - len(set(ids))
    print(f"  {path.name}: {len(clean)} judgments, {ids[0]}..{ids[-1]}"
          + (f", {dupes} duplicate id(s)" if dupes else "")
          + (f", DROPPED {dropped}" if dropped else "")
          + (f"  [{note}]" if note else "  [already valid]"))


def main():
    targets = ([OUT / f"{sys.argv[1].removesuffix('.json')}.json"] if len(sys.argv) > 1
               else sorted(OUT.glob("*.json")))
    print(f"{len(targets)} file(s)")
    for p in targets:
        if p.exists():
            fix(p)
        else:
            print(f"  {p.name}: not found")


if __name__ == "__main__":
    main()
