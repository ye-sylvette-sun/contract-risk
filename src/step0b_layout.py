"""Step 0b — screen out two-column scans (one cheap call per contract).

Some filings were printed in two side-by-side columns, and `ocrmypdf` reads such
a page straight across: every output line carries the left column and the right
column run together, from two passages that have nothing to do with each other.
The text is not damaged in a way any later step can repair, and it is not
detectably wrong to a step that reads one clause at a time — the anchors match,
the line range is real, and the extracted "clause" is two unrelated halves of
two clauses, alternating line by line — the failure the rest of the design
cannot see (docs/DATASET.md §3, step 0b). This step is where it is caught: before step 1 spends a
call on the document, and before anything cut from it reaches the dataset.

It is a screen, not a judgement about the contract, so it does not run on MODEL.
It runs on Sonnet at low effort: the question is shallow and visual, and the
answer is one boolean. A full 117-contract screen costs a few dollars, against
roughly $0.60 for a single step-1 call on one of the larger cases.

Why an LLM and not a rule. A deterministic gutter detector was written and
measured first (a run of blank space at the same column position on consecutive
lines). It separates cleanly at the top — the worst offender scores highest —
but it cannot be made to catch a document whose left column is often empty
without also flagging a quarter of the corpus, including contracts that have
already been inventoried and read fine. The thresholds both halves use are set
from those measurements, and are the constants at the top of this file. The model
sees what the rule cannot: that the two halves of the line are
unrelated *text*, which is the actual definition of the fault.

Nothing here is deleted. A rejected contract keeps its file and its registry
row; layout.json records the verdict, the model's evidence for it, and what it
cost, and step 1 skips the contract. A verdict can be re-read, and a wrong one
can be undone by editing one boolean.

Input : output/contracts.json, output/contracts/<cid>.md
Output: output/layout.json  (resumable — re-runs only what is missing)

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
# The deterministic half. A page set in two columns leaves a GUTTER: a run of
# blank space at the same column position on line after line. Contracts are full
# of aligned white space that means nothing — indents, letterheads, signature
# blocks, tables — so the discriminator is PERSISTENCE. A real body column holds
# its gutter for dozens of consecutive lines; a letterhead holds it for five.
#
# Measured over all 117 contracts, against every case confirmed by hand:
#
#     0.36  170FSupp3d754_ambit_pennsylvania_northeast_llc   two-column
#     0.24  33FSupp3d110_homeowner_s_policy_the              two-column
#     0.24  170FSupp3d754_ambit_northeast_llc_pennsylvania   two-column
#     0.21  278FSupp3d1165_motion_picture_television_prod    two-column
#     ----------------------------------------------------- GUTTER = 0.15
#     0.08  894F3d509_delaware_river_basin_compact           clean
#     0.08  809FSupp2d582_april_28_2008_letter               clean
#      ...  113 more, all 0.08 or below
#
# Four flagged, four true, nothing else within 0.13 of the line. Its blind spot
# is a document whose LEFT column is often empty — the run breaks where there is
# no internal gap — which is why it does not replace the model call.
GAP = re.compile(r"\S(\s{3,})\S")
WIDE_LINE = 45    # a line shorter than this cannot show a body-width gutter
TOL = 6           # a column wanders a few characters down the page
RUN = 6           # this many consecutive lines make a column, not a header
GUTTER = 0.15     # coverage at or above this is two-column, whatever the model says


def gutter(text):
    """(fraction of substantial lines inside a sustained column run, longest run).

    WIDE_LINE, TOL and RUN are the values the table above was measured at.
    Change one and the threshold means something else — re-measure before
    trusting it.
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

# What the model is shown. Consecutive lines, in windows spread from the first
# line of the file to the last, because the signature of an interleaved scan is
# that the SAME break recurs line after line — a line on its own shows nothing,
# and a single window at the front of the document would see only front matter.
SPAN = 24              # consecutive lines per window
SHARE = 0.25           # of the document...
FLOOR, CEIL = 400, 1200  # ...but never fewer or more lines than this
MIN_LINE = 40          # start a window on a line at least this long, not a blank


def sample(text):
    """[(first line number, lines)] — the windows shown to the model.

    A whole document would be more thorough and is affordable at Sonnet rates,
    but the largest contract here is 676,993 characters and the question is a
    needle: whether any run of lines interleaves. Windows spread evenly over the
    file keep the cost flat and, more importantly, keep every part of a long
    document equally likely to be looked at.
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
    """The windows as the model sees them: real line numbers, gaps marked.

    The line numbers are the file's own, not the sample's, so the evidence the
    model quotes back can be looked up in the file.
    """
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

    # Note what is NOT here: a `cid in done -> skip` rule. Resumability lives
    # further down, around the CALL alone, because the gutter is free and has to
    # be scored on every contract every run — that is what let it be added to
    # 117 stored verdicts without paying for any of them again.
    for cid, entry in sorted(registry.items()):
        if (args.case and entry["citation"] != args.case) or \
           (args.contract and cid != args.contract):
            continue
        text = (lib.ROOT / entry["file"]).read_text(encoding="utf-8")
        total = text.count("\n") + 1
        cov, longest = gutter(text)

        # The gutter costs nothing, so it is scored on every contract, every
        # run. A contract that already has a verdict is re-scored and its
        # combined answer updated WITHOUT a call: that is how the deterministic
        # half was added to 117 stored verdicts for free.
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

        # Either detector rejects. They fail differently — the model missed two
        # policies whose two-column part is a fraction of a 200,000-character
        # filing, and the gutter misses a document whose left column is often
        # empty — so the union is strictly better than either.
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
