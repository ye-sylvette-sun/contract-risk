"""Step 0b — screen out two-column scans (one cheap call per contract).

`ocrmypdf` reads a two-column page straight across, so every line carries the
left and right columns run together. Later steps cannot see this: the anchors
match, the line range is real, and the "clause" is two unrelated halves
alternating line by line (docs/DATASET.md §3).

The model decides, cheap and at low effort: the question is shallow and visual.
It is shown windows sampled across the whole document, and it must quote the
interleaved lines back VERBATIM with their line numbers — so every rejection can
be looked up in the file rather than taken on trust.

Nothing is deleted. A rejected contract keeps its file and registry row;
layout.json records the verdict and the evidence, and step 1 skips it.

Input : output/contracts.json, output/contracts/<cid>.md
Output: output/layout.json  (resumable)

Each contract is judged independently, so they run concurrently and each
worker writes only its own file under output/layout/. Those are folded into
layout.json when the step finishes and deleted once the merge is verified, which
makes the artifact identical whatever `--parallel` was set to.

Usage:
    python src/step0b_layout.py [--case CITATION] [--contract CONTRACT_ID]
                               [--force] [--parallel N]
"""
import argparse
import concurrent.futures as cf

import lib

OUT = lib.OUT / "layout.json"
SHARDS = "layout"        # output/layout/<contract_id>.json while running

MODEL = lib.CHEAP_MODEL
EFFORT = "low"

# Windows of consecutive lines, spread over the whole file: the signature is a
# break recurring line after line, and a single window at the front would see
# only front matter.
SPAN = 24              # consecutive lines per window
SHARE = 0.25           # of the document...
FLOOR, CEIL = 400, 1200  # ...but never fewer or more lines than this
MIN_LINE = 40          # start a window on a line at least this long, not a blank


def sample(text):
    """[(first line number, lines)] — the windows shown to the model.

    Sampled rather than sent whole: the largest contract is 677k characters,
    and this keeps every part of it equally likely to be looked at.
    """
    lines = text.split("\n")
    want = int(min(max(len(lines) * SHARE, FLOOR), CEIL))
    if len(lines) <= want:
        return [(1, lines)]

    out, end = [], 0
    n = max(1, want // SPAN)
    for k in range(n):
        i = max(int(k * len(lines) / n), end)
        # spend the window on text, not on the blank run before it
        i = next((x for x in range(i, min(i + SPAN, len(lines)))
                  if len(lines[x].rstrip()) >= MIN_LINE), i)
        if i + SPAN > len(lines):
            break
        out.append((i + 1, lines[i:i + SPAN]))
        end = i + SPAN
    return out


def render(windows, total):
    """The windows as the model sees them. Line numbers are the file's own, so
    quoted evidence can be looked up."""
    parts, prev = [], 0
    for start, lines in windows:
        if start > prev + 1:
            parts.append(f"      ... lines {prev + 1}-{start - 1} not shown ...")
        parts.append("\n".join(f"{start + i:5d}│{ln}"
                               for i, ln in enumerate(lines)))
        prev = start + len(lines) - 1
    if prev < total:
        parts.append(f"      ... lines {prev + 1}-{total} not shown ...")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="screen one citation's contracts only")
    ap.add_argument("--contract", help="screen one contract_id only")
    ap.add_argument("--force", action="store_true",
                    help="re-screen contracts that already have a verdict")
    ap.add_argument("--parallel", type=int, default=4,
                    help="contracts screened at once (default 4)")
    args = ap.parse_args()

    registry = lib.read_json(lib.OUT / "contracts.json", {})
    # Both sources: the artifact from a finished run, and shards a run that died
    # before merging left behind.
    done = {**(lib.read_json(OUT, {}) or {}), **lib.read_shards(SHARDS)}

    todo = [(cid, e) for cid, e in sorted(registry.items())
            if not (args.case and e["citation"] != args.case)
            and not (args.contract and cid != args.contract)
            and (args.force or cid not in done)]

    def screen(cid, entry):
        """One contract. Output is built whole and printed once, so four
        workers cannot interleave halfway through a verdict."""
        text = (lib.ROOT / entry["file"]).read_text(encoding="utf-8")
        total = text.count("\n") + 1
        windows = sample(text)
        shown = sum(len(w[1]) for w in windows)
        answer = lib.ask("layout", cid, effort=EFFORT, model=MODEL,
                         citation=entry["citation"], contract_id=cid,
                         total_lines=f"{total:,}",
                         document=render(windows, total))
        if answer is None:
            return None
        verdict = {"citation": entry["citation"],
                   "two_column": bool(answer["two_column"]),
                   "finding": answer["finding"],
                   "evidence": answer["evidence"],
                   "lines_shown": shown, "lines_total": total}
        lib.write_shard(SHARDS, cid, cid, verdict)
        print(f"layout {cid}  ({total:,} lines, {shown} shown)\n"
              + (f"  TWO-COLUMN — rejected: {answer['finding']}"
                 if verdict["two_column"] else f"  ok: {answer['finding']}"),
              flush=True)
        return verdict

    if todo:
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futs = [pool.submit(screen, cid, e) for cid, e in todo]
            for fut in cf.as_completed(futs):
                fut.result()

    done = lib.merge_shards(SHARDS, OUT)
    bad = [c for c, v in done.items() if v["two_column"]]
    print(f"{len(done)} contracts screened | {len(bad)} rejected as two-column")
    for cid in sorted(bad):
        print(f"  - {cid}")


if __name__ == "__main__":
    main()
