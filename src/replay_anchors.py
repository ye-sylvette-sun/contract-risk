"""Score the locator against stored logs, at zero API cost. See docs/DATASET.md.

Two kinds of log, picked per clause:

* **replay** — clauses carrying `head`/`tail`. The anchors the model actually
  returned are re-run through `locate()`, which is how ANCHOR_MATCH and SLACK
  are tuned against real answers.
* **synthesised** — clauses carrying full `text`. Anchors are sliced from it, so
  `--anchor-words` can be swept.

The logs do NOT store the document that was sent — it is the corpus text, many
times the size of everything else in the log, and it is already on disk. The
source is read from `output/contracts/` instead, resolved through
`output/contracts.json`, so the corpus must be present. A log that still carries
its own `document` (an archive from before this changed) is scored from that
instead, so old log directories keep working.

It measures what fraction of anchors match, how often snapping moves a boundary
and by how much, and how often the extracted text differs from the raw source
window. A difference is not automatically a fault — the anchors cut INSIDE the
first and last line, so a heading's markdown is legitimately left out. What it
catches is a span that lost real words.

Two-column interleave has no cheap detector and is not attempted here.

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
    """{contract_id: text} for a log that still carries its own document.

    A per-case log carries the opinion and several tagged contracts; a per-file
    log carries one untagged document. Both are recovered the same way. Only
    archived logs reach this now — see `sources()`.
    """
    parts = TAGGED.split(log["document"])
    if len(parts) == 1:
        return {"": unnumber(log["document"])}
    return {parts[i]: unnumber(parts[i + 1]) for i in range(1, len(parts), 2)}


def corpus(registry, cache):
    """contract_id -> the filed text, read once and kept.

    `locate()` wants the source, not the numbered form the model was shown, so
    reading the corpus file is not an approximation of what the log used to
    hold: it is the same string the numbering was built from.
    """
    def read(cid):
        if cid not in cache:
            entry = registry.get(cid)
            path = (lib.ROOT / entry["file"]) if entry else                 lib.OUT / "contracts" / f"{cid}.md"
            cache[cid] = path.read_text(encoding="utf-8") if path.exists() else None
        return cache[cid]
    return read


def sources(log, path, read):
    """{contract_id: text} for one log, from the corpus or the log itself.

    An `inventory` log is named for the one contract it covers; an `extract` log
    covers a case, and every clause in it names its own `contract_id`, so the
    answer says which files to open.
    """
    if "document" in log:                       # an archived log, self-contained
        return documents(log)
    found = answer(log) or {}
    items = found.get("clauses") or found.get("provisions") or []
    ids = {c.get("contract_id") for c in items if c.get("contract_id")}
    if ids:
        return {cid: read(cid) for cid in ids}
    return {"": read(path.stem)}                # inventory: one contract, unnamed


def answer(log):
    """The parsed response of one log, or None if it is not a locator answer.

    The log directory also holds run manifests and the agent run's own records,
    which carry no `response` at all. Until the document stopped being stored,
    those were filtered out by its absence; now they are rejected here.
    """
    if log.get("stop_reason") == "refusal":
        return None
    blocks = log.get("response")
    if not isinstance(blocks, list):
        return None

    def texts(bs):
        """The answer text, whichever provider's reply shape this is.

        Anthropic returns `{"type": "text", "text": ...}` at the top level;
        OpenAI returns reasoning blocks and one `{"type": "message"}` whose
        `content` holds `{"type": "output_text", "text": ...}`. Both are logged
        verbatim, so both have to be read here — a locator answer from the
        dataset build has been OpenAI-shaped since the build moved providers.
        """
        for b in bs:
            if not isinstance(b, dict):
                continue
            if b.get("type") in ("text", "output_text") and b.get("text"):
                yield b["text"]
            elif isinstance(b.get("content"), list):
                yield from texts(b["content"])

    text = next(texts(blocks), None)
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

    registry = lib.read_json(lib.OUT / "contracts.json", {}) or {}
    read = corpus(registry, {})

    for path in paths:
        log = lib.read_json(path)
        if not log:
            continue
        found = answer(log)
        if not found:
            continue
        texts = {k: v for k, v in sources(log, path, read).items() if v}
        if not texts:
            continue
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
