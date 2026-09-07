"""Step 2 — locate every clause of a contract (one call per contract).

Runs on every contract step 1 was shown: those a positive came from (so a
positive is never left without negatives from its own document) and those that
produced none (whose clauses are negatives in their own right).

The model returns a line range and two anchors per clause and writes no text.
What it cannot be checked on is a range that starts and ends correctly but
swallows an intervening clause — both anchors match and the extraction silently
contains too much (docs/DATASET.md §6). The detectors below are FLAGS only,
printed and stored, so they can be measured before anyone rejects on them.

Input : output/clauses.json, output/contracts.json, output/contracts/*.md
Output: output/inventory.json  (resumable — re-runs only what is missing)

Usage:
    python src/step2_inventory.py [--case CITATION] [--contract CONTRACT_ID]
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
    ap.add_argument("--case", help="inventory one citation's winners only")
    ap.add_argument("--contract", help="inventory one contract_id only")
    ap.add_argument("--parallel", type=int, default=4,
                    help="contracts inventoried at once (default 4)")
    args = ap.parse_args()

    clauses = lib.read_json(lib.OUT / "clauses.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    layout = lib.read_json(lib.OUT / "layout.json", {})
    done = {**(lib.read_json(OUT, {}) or {}), **lib.read_shards(SHARDS)}

    # EVERY contract step 1 was shown, for every case it processed — not only
    # the ones a positive came out of.
    #
    # The winners have to be here: a positive is never left without negatives cut
    # from its own document. The rest are here because their clauses are
    # negatives too. A case often files several agreements and the court reaches
    # only some of them; the others were before the court, were not construed,
    # and that is exactly what a negative is. Dropping them threw away about a
    # fifth of the corpus for no reason beyond how the pipeline happened to be
    # wired, and it also removed every contract in which the right answer is
    # "nothing here" — the case that most sharply tests over-flagging.
    #
    # A two-column contract is still excluded: step 1 never saw it either.
    targets = {}
    for citation, case in clauses.items():
        for c in case["clauses"]:
            targets.setdefault(c["contract_id"], citation)
    for cid, entry in registry.items():
        if entry["citation"] in clauses and \
                not layout.get(cid, {}).get("two_column"):
            targets.setdefault(cid, entry["citation"])

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
