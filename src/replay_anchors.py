"""Score the locator against stored logs, at zero API cost. See docs/DATASET.md.

Two kinds of log can be scored, and the script picks per clause:

* **replay** — a log from this pipeline, whose clauses carry `head` and `tail`.
  The anchors the model actually returned are re-run through `locate()`, which
  is how ANCHOR_MATCH and SLACK are tuned against real answers.
* **synthesised** — a log whose clauses carry full `text` instead. The first and
  last ANCHOR_WORDS words of that text are exactly what the prompts ask for, so
  anchors are sliced out of it and `--anchor-words` can be swept.

Either way the whole locator — matching, snapping, slicing, normalising — is
scored offline, at no API cost.

Each log also stores the numbered document that was sent, so the text those line
numbers index is recovered from the log itself. Nothing outside the log
directory is needed, and the OCR inputs do not have to be on disk.

What it measures:

  1. what fraction of anchors match at the claimed boundary;
  2. how often snapping moves a boundary, and by how many lines;
  3. how often the extracted text differs from the raw source window. A
     difference is not automatically a fault: the anchors cut INSIDE the first
     and last line, so a heading's markdown (`### **`) is legitimately left out
     of the span. What it catches is a span that lost real words.

Two-column interleave has no cheap detector and is not attempted here. It has to
be counted by hand, and docs/DATASET.md requires it be reported, not hidden.

Usage:
    python src/replay_anchors.py --logs output/llm_logs
    python src/replay_anchors.py --logs <dir> --anchor-words 6   # synthesised only
"""
import argparse
import json
import re
from pathlib import Path

import lib

NUMBERED = re.compile(r"^ *(\d+)│(.*)$")

# The marker that opens a tagged document block. Both this pipeline's form
# (`---------- CONTRACT X START ----------`) and the older `--- contract_id: X`
# parse, so an archived log directory can still be scored.
TAGGED = re.compile(r"^(?:--- contract_id: |-{5,} CONTRACT )(\S+)"
                    r"(?: START -{5,})?\s*$", re.M)


def unnumber(chunk):
    """The source text a numbered block was made from, line for line."""
    return "\n".join(m.group(2) for ln in chunk.split("\n")
                     if (m := NUMBERED.match(ln)))


def documents(log):
    """{contract_id: text} for every numbered document in one log.

    A per-case log carries the opinion and several tagged contracts; a per-file
    log carries one untagged document. Both are recovered the same way.
    """
    parts = TAGGED.split(log["document"])
    if len(parts) == 1:
        return {"": unnumber(log["document"])}
    return {parts[i]: unnumber(parts[i + 1]) for i in range(1, len(parts), 2)}


def answer(log):
    """The parsed response of one log, or None if it did not land."""
    if log.get("stop_reason") == "refusal":
        return None
    text = next((b["text"] for b in log["response"] if b["type"] == "text"), None)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def anchors(text, n):
    """The head and tail a model would have copied, sliced out of its own text."""
    words = text.split()
    if len(words) < 2 * n:
        return " ".join(words), ""
    return " ".join(words[:n]), " ".join(words[-n:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", required=True, type=Path,
                    help="a directory of <step>/<call_id>.json logs")
    ap.add_argument("--anchor-words", type=int, default=lib.ANCHOR_WORDS)
    ap.add_argument("--show", type=int, default=5,
                    help="how many failures of each kind to print")
    args = ap.parse_args()

    paths = sorted(args.logs.rglob("*.json"))
    if not paths:
        raise SystemExit(f"no logs under {args.logs}")

    n = seen = matched = moved = clean = 0
    mode = {'replay': 0, 'synthesised': 0}
    lines_moved, scores, misses, dirty = [], [], [], []

    for path in paths:
        log = lib.read_json(path)
        if not log or "document" not in log:
            continue
        found = answer(log)
        if not found:
            continue
        texts = documents(log)
        items = found.get("clauses") or found.get("provisions") or []

        for c in items:
            key = c.get("contract_id", "") if len(texts) > 1 else next(iter(texts))
            text = texts.get(key)
            if text is None:
                continue
            if c.get("head"):                       # this pipeline's own logs
                head, tail = c["head"], c.get("tail", "")
                mode["replay"] += 1
            elif c.get("text"):                     # a log that carries clause text
                head, tail = anchors(c["text"], args.anchor_words)
                mode["synthesised"] += 1
            else:
                continue
            seen += 1
            start, end = c["start_line"], c["end_line"]
            got, why = lib.locate(text, start, end, head, tail)
            if why:
                misses.append(f"{path.parent.name}/{path.stem}  "
                              f"{c.get('clause_name') or c.get('name')}: {why}")
                continue
            matched += 1
            scores.append(got["score"])
            if got["lines"] != [start, end]:
                moved += 1
                lines_moved.append(abs(got["lines"][0] - start)
                                   + abs(got["lines"][1] - end))
            raw = lib.normalise(lib.window(text.split("\n"), start, end))
            if got["text"] == raw:
                clean += 1
            elif len(dirty) < 200:
                dirty.append((path.stem, c.get("clause_name") or c.get("name"),
                              raw, got["text"]))
        n += 1

    if not seen:
        raise SystemExit(f"{len(paths)} logs read, no clause text in any of them")

    how = ", ".join(f"{v} {k}" for k, v in mode.items() if v)
    print(f"{n} logs | {seen} clauses ({how}) | "
          f"ANCHOR_MATCH={lib.ANCHOR_MATCH} SLACK={lib.SLACK}"
          + (f" ANCHOR_WORDS={args.anchor_words}" if mode["synthesised"] else "")
          + "\n")
    print(f"1. anchors that match     {matched:5d} / {seen}  "
          f"({matched / seen * 100:.1f}%)")
    if scores:
        exact = sum(1 for s in scores if s == 1.0)
        print(f"   score                  {min(scores):.2f} worst, "
              f"{sum(scores) / len(scores):.3f} mean, {exact} exact "
              f"({exact / len(scores) * 100:.1f}%)")
    print(f"2. boundaries snapped     {moved:5d} / {matched}"
          + (f"  ({sum(lines_moved) / len(lines_moved):.1f} lines on average, "
             f"{max(lines_moved)} worst)" if lines_moved else ""))
    print(f"3. extraction == raw      {clean:5d} / {matched}  "
          f"({clean / matched * 100:.1f}%)" if matched else "")
    print("4. two-column interleave  not detectable — count by hand (docs/DATASET.md)")

    for label, rows in (("did not locate", misses[:args.show]),
                        ("differs from the raw window (check for lost words, not for markdown)",
                         [f"{a}  {b}\n     raw: {c[:110]}\n     got: {d[:110]}"
                          for a, b, c, d in dirty[:args.show]])):
        if rows:
            print(f"\n{label}:")
            for r in rows:
                print(f"  - {r}")


if __name__ == "__main__":
    main()
