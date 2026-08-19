"""Step 0b — screen out two-column scans (one cheap call per contract).

`ocrmypdf` reads a two-column page straight across, so every line carries the
left and right columns run together. Later steps cannot see this: the anchors
match, the line range is real, and the "clause" is two unrelated halves
alternating line by line (docs/DATASET.md §3).

Two detectors, and either one rejects. They fail differently — the model missed
two policies whose two-column part is a fraction of a huge filing, the gutter
rule misses documents whose left column is often empty — so the union beats
both. Sonnet at low effort: the question is shallow and visual.

Nothing is deleted. A rejected contract keeps its file and registry row;
layout.json records the verdict and the evidence, and step 1 skips it.

Input : output/contracts.json, output/contracts/<cid>.md
Output: output/layout.json  (resumable)

Usage:
    python src/step0b_layout.py [--case CITATION] [--contract CONTRACT_ID]
                               [--force]
"""
import argparse
import re

import lib

OUT = lib.OUT / "layout.json"

MODEL = "claude-sonnet-5"
EFFORT = "low"

# ------------------------------------------------------------ the gutter ----
# A two-column page leaves a GUTTER: blank space at the same column position
# line after line. Contracts are full of meaningless aligned whitespace, so the
# discriminator is PERSISTENCE — a body column holds its gutter for dozens of
# lines, a letterhead for five.
#
# Measured over all 117 contracts: the four true two-column scans score
# 0.21-0.36, everything else 0.08 or below. Hence GUTTER = 0.15, with nothing
# within 0.13 of it.
GAP = re.compile(r"\S(\s{3,})\S")
WIDE_LINE = 45    # a line shorter than this cannot show a body-width gutter
TOL = 6           # a column wanders a few characters down the page
RUN = 6           # this many consecutive lines make a column, not a header
GUTTER = 0.15     # coverage at or above this is two-column, whatever the model says


def gutter(text):
    """(fraction of substantial lines in a sustained run, longest run).

    GUTTER was measured at these WIDE_LINE/TOL/RUN values; change one and
    re-measure before trusting the threshold.
    """
    lines = [ln.rstrip() for ln in text.split("\n")]
    idx = [i for i, ln in enumerate(lines) if len(ln) >= WIDE_LINE]
    if len(idx) < 20:
        return 0.0, 0
    at = {i: [m.start(1) + len(m.group(1)) // 2 for m in GAP.finditer(lines[i])]
          for i in idx}

    covered, longest, cur, anchor = set(), 0, 0, None
    for k, i in enumerate(idx):
        near = k and i - idx[k - 1] <= 2
        if near and anchor is not None and any(abs(c - anchor) <= TOL for c in at[i]):
            cur += 1
        else:
            if cur >= RUN:
                covered |= set(range(k - cur, k))
                longest = max(longest, cur)
            anchor = at[i][0] if at[i] else None
            cur = 1 if anchor is not None else 0
    if cur >= RUN:
        covered |= set(range(len(idx) - cur, len(idx)))
        longest = max(longest, cur)
    return len(covered) / len(idx), longest

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
    args = ap.parse_args()

    registry = lib.read_json(lib.OUT / "contracts.json", {})
    done = lib.read_json(OUT, {})

    # No `cid in done -> skip` here: resumability wraps the CALL alone, so the
    # free gutter score is recomputed for every contract on every run.
    for cid, entry in sorted(registry.items()):
        if (args.case and entry["citation"] != args.case) or \
           (args.contract and cid != args.contract):
            continue
        text = (lib.ROOT / entry["file"]).read_text(encoding="utf-8")
        total = text.count("\n") + 1
        cov, longest = gutter(text)

        prev = done.get(cid)
        if prev and not args.force:
            # `model_two_column` is absent from a verdict written before the
            # gutter existed; back then `two_column` WAS the model's answer.
            said = prev.get("model_two_column", prev["two_column"])
            finding, evidence = prev["finding"], prev["evidence"]
            shown = prev["lines_shown"]
        else:
            windows = sample(text)
            shown = sum(len(w[1]) for w in windows)
            print(f"layout {cid}  ({total:,} lines, {shown} shown, "
                  f"gutter {cov:.2f})")
            answer = lib.ask("layout", cid, effort=EFFORT, model=MODEL,
                             citation=entry["citation"], contract_id=cid,
                             total_lines=f"{total:,}",
                             document=render(windows, total))
            if answer is None:
                continue
            said, finding = answer["two_column"], answer["finding"]
            evidence = answer["evidence"]

        # Either detector rejects.
        done[cid] = {"citation": entry["citation"],
                     "two_column": bool(said) or cov >= GUTTER,
                     "model_two_column": bool(said),
                     "gutter": round(cov, 3), "gutter_run": longest,
                     "finding": finding, "evidence": evidence,
                     "lines_shown": shown, "lines_total": total}
        lib.write_json(OUT, done)
        if done[cid]["two_column"]:
            by = "model+gutter" if said and cov >= GUTTER else \
                 "gutter only" if cov >= GUTTER else "model only"
            print(f"  TWO-COLUMN — rejected ({by}, gutter {cov:.2f}): {finding}")
        elif not prev or args.force:
            print(f"  ok: {finding}")

    bad = [c for c, v in done.items() if v["two_column"]]
    print(f"{len(done)} contracts screened | {len(bad)} rejected as two-column")
    for cid in sorted(bad):
        print(f"  - {cid}")


if __name__ == "__main__":
    main()
