"""Step 1 — locate every clause of a contract (one call per contract).

**This step, and only this step, decides where a clause starts and ends.**

It is deliberately first, and it is deliberately blind: it sees one contract and
nothing else. No opinion, no Westlaw key, no hint that the document was ever
litigated. A clause boundary is a fact about the drafting — the smallest unit
that states a complete obligation on its own — and nothing about which clause a
court happened to construe may move it.

That ordering is not cosmetic. When the dispute step ran first it drew its own
boundaries, and they drifted with what the court had said — fragmenting a
provision so each piece could carry a defect, or widening one to hold two. Drift
like that separates positives from negatives for a reason unrelated to drafting
risk, and a classifier can learn it.

Every clause gets an id — `c001`, `c002`, ... in document order — and step 2
answers only in those ids. It has no way to report a span of its own.

The model returns a line range and two anchors per clause and writes no text.
What it cannot be checked on is a range that starts and ends correctly but
swallows an intervening clause — both anchors match and the extraction silently
contains too much (docs/DATASET.md §6). The detectors below are FLAGS only,
printed and stored, so they can be measured before anyone rejects on them.

Input : output/cases.json, output/contracts.json, output/contracts/*.md,
        output/layout.json
Output: output/inventory.json  (resumable — re-runs only what is missing)

Usage:
    python src/step1_inventory.py [--case CITATION] [--contract CONTRACT_ID]
"""
import argparse
import concurrent.futures as cf
import statistics

import lib

OUT = lib.OUT / "inventory.json"
SHARDS = "inventory"     # output/inventory/<contract_id>.json while running

# A clause this many times the contract's median length is flagged. Reporting
# only — nothing is rejected on it.
#
# Raised from 6 after reading ten flagged clauses by hand: at 6, precision 0.20;
# at 10, 0.33 with recall still 100%; above 10 recall breaks and precision does
# not improve, because a merged clause and a good one both sit at 10.8x the
# median. Truth set is ten clauses in two contracts — re-derive when more have
# been read.
WIDE = 10


def flags(kept):
    """The §7 detectors. Reporting only — none of these rejects a clause.

    `kept` is already in document order, sorted by span before this is called.

    A third detector used to live here — clauses reported out of document order.
    It was dropped when the sort went in: sorting is what fixes the order, and
    the artifact no longer depends on the sequence the model happened to list
    them in, so the check had nothing left to report.
    """
    out = []
    for a, b in zip(kept, kept[1:]):
        # Two sub-clauses the OCR ran onto one line legitimately share a line,
        # so only a real character overlap counts.
        if b["span"][0] < a["span"][1]:
            out.append(f"{a['name']!r} and {b['name']!r} overlap at characters "
                       f"{b['span'][0]}-{a['span'][1]}")

    if len(kept) >= 3:
        lengths = [c["span"][1] - c["span"][0] for c in kept]
        median = statistics.median(lengths)
        for c, n in zip(kept, lengths):
            if median and n > WIDE * median:
                out.append(f"{c['name']!r} is {n:,} characters, {n / median:.0f}x "
                           f"the median {median:,.0f} — check for over-capture")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="inventory one citation's contracts only")
    ap.add_argument("--contract", help="inventory one contract_id only")
    ap.add_argument("--parallel", type=int, default=4,
                    help="contracts inventoried at once (default 4)")
    args = ap.parse_args()

    cases = lib.read_json(lib.OUT / "cases.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    layout = lib.read_json(lib.OUT / "layout.json", {})
    done = {**(lib.read_json(OUT, {}) or {}), **lib.read_shards(SHARDS)}

    # EVERY registered contract of an in-scope case. A case often files several
    # agreements and the court reaches only some; the others were before the
    # court and not construed, which is exactly what a negative is, and they
    # supply the contracts where the right answer is "nothing here".
    #
    # Two-column contracts are excluded here and so never reach step 2 either —
    # the exclusion happens once, in one place.
    targets = {cid: e["citation"] for cid, e in registry.items()
               if e["citation"] in cases
               and not layout.get(cid, {}).get("two_column")}

    todo = [(cid, cit) for cid, cit in sorted(targets.items())
            if not (args.case and cit != args.case)
            and not (args.contract and cid != args.contract)
            and cid not in done]

    def inventory_one(cid, citation):
        """One contract. The whole report is assembled and printed in one call,
        so concurrent workers cannot interleave inside a contract's output."""
        entry = registry[cid]
        text = (lib.ROOT / entry["file"]).read_text(encoding="utf-8")

        why = lib.out_of_bounds(text)
        if why:
            lib.write_shard(SHARDS, cid, cid,
                            {"citation": citation, "note": f"skipped: {why}",
                             "clauses": [], "rejected": [], "flags": []})
            print(f"skip   {cid}: {why}", flush=True)
            return

        answer = lib.ask("inventory", cid, citation=citation, contract_id=cid,
                         document=lib.numbered(text))
        if answer is None:
            return

        kept, rejected = [], []
        for clause in answer["clauses"]:
            found, why = lib.locate(text, clause["start_line"],
                                    clause["end_line"], clause["head"],
                                    clause["tail"])
            if why:
                rejected.append({"name": clause["name"], "head": clause["head"],
                                 "tail": clause["tail"], "reason": why})
                continue
            kept.append({"name": clause["name"],
                         "claimed_lines": [clause["start_line"],
                                           clause["end_line"]],
                         "lines": found["lines"], "span": found["span"],
                         "score": found["score"], "head": clause["head"],
                         "tail": clause["tail"], "text": found["text"]})

        # Document order, fixed here rather than left to the order the model
        # happened to list them in, so the artifact does not depend on it.
        kept.sort(key=lambda c: c["span"])
        rejected.sort(key=lambda r: (r["name"], r["head"]))

        # The id every later step names a clause by. It encodes document order
        # and nothing else, so it cannot carry a hint about the label the way
        # `pos1`/`neg1` once did — and it is assigned before anything in the
        # pipeline knows which clauses were disputed.
        for n, c in enumerate(kept, 1):
            c["clause_id"] = f"c{n:03d}"

        snapped = sum(1 for c in kept if c["lines"] != c["claimed_lines"])
        marks = flags(kept)
        lib.write_shard(SHARDS, cid, cid,
                        {"citation": citation, "note": "", "clauses": kept,
                         "rejected": rejected, "flags": marks})
        out = [f"inventory {cid}  ({entry['chars']:,} chars)",
               f"  {len(kept)} clauses located ({snapped} snapped, "
               f"{len(rejected)} rejected)"]
        out += [f"    - rejected {r['name']!r}: {r['reason']}" for r in rejected]
        out += [f"    ? {m}" for m in marks]
        print("\n".join(out), flush=True)

    if todo:
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futs = [pool.submit(inventory_one, cid, cit) for cid, cit in todo]
            for fut in cf.as_completed(futs):
                fut.result()

    done = lib.merge_shards(SHARDS, OUT)
    located = sum(len(v["clauses"]) for v in done.values())
    rejected = sum(len(v["rejected"]) for v in done.values())
    flagged = sum(1 for v in done.values() if v["flags"])
    print(f"{len(done)} contracts | {located} clauses located | "
          f"{rejected} rejected | {flagged} contracts flagged")


if __name__ == "__main__":
    main()
