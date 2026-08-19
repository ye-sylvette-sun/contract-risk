"""Prove the agent harness is isolated, before spending a run on it.

The first agent run was defended after the fact: the trajectories were searched
for memory, `CLAUDE.md` and skills, none was found, and the conclusion was that
nothing had leaked in. That argument happened to be right and was worth nothing,
because it was made about a run that had already been paid for, on a machine
that happened to have nothing to leak. `setting_sources=None` — which loads user,
project and local settings — sat under a comment claiming the opposite for the
whole of it.

So this runs ONE real session, under the real options, on the smallest
outstanding contract, and then audits its own trajectory. Nothing here is a
mock: the session is billed, its predictions are written to the real files, and
the full run continues from it rather than repeating it.

The checks, each a way the CLI can take on context that never appears in the
conversation:

    MEMORY      no memory file injected into the first user turn
    CLAUDE_MD   no CLAUDE.md from ~/.claude or any ancestor directory
    SKILLS      no skill listing, no Skill tool, no mcp__* tool
    REMINDERS   <system-reminder> blocks only as the CLI's wrapper around a
                tool result — never as a free-standing instruction
    MODEL       exactly one model billed, and it is the one we asked for
    CLI         one CLI version across every record, equal to the installed one
    TOOLS       no tool outside the four, and every attempt at one refused
    PATHS       no file read or written outside the contract's workspace
    MANIFEST    a run manifest exists and records the environment sweep

Any failure exits non-zero. The output goes in the repo beside the manifests.

Usage:
    python src/experiments/preflight.py              # run one contract, audit it
    python src/experiments/preflight.py --audit CID  # audit an existing session
"""
import argparse
import asyncio
import json
import re
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import exp3_agent as ag  # noqa: E402

api = ag.api

# Distinctive strings from the three context channels that do not appear in the
# conversation unless something loaded them. `MEMORY.md` and `CLAUDE.md` are the
# filenames the CLI names when it injects one; the skills probes are the wording
# of the skills listing and of the tool that uses it.
PROBES = {
    "MEMORY": [r"MEMORY\.md", r"persistent file-based memory"],
    "CLAUDE_MD": [r"CLAUDE\.md"],
    "SKILLS": [r"available skills", r"Skill tool", r"skills are available",
               r'"name"\s*:\s*"Skill"', r"mcp__"],
}
REMINDER = re.compile(r"<system-reminder>", re.I)


def records(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def blocks(rec):
    """Every content block of a record, whatever shape the message takes."""
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def text_of(block):
    if block.get("type") == "text":
        return block.get("text") or ""
    if block.get("type") == "tool_result":
        c = block.get("content")
        if isinstance(c, str):
            return c
        return "".join(x.get("text", "") for x in (c or [])
                       if isinstance(x, dict))
    return ""


class Audit:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        return ok

    def report(self):
        width = max(len(n) for n, _, _ in self.rows)
        print()
        for name, ok, detail in self.rows:
            print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
        bad = [n for n, ok, _ in self.rows if not ok]
        print()
        if bad:
            print(f"PREFLIGHT FAILED — {len(bad)} check(s): {', '.join(bad)}")
        else:
            print(f"PREFLIGHT PASSED — {len(self.rows)} checks, "
                  f"the harness is isolated by construction")
        return 1 if bad else 0


def audit(cid):
    """Audit one finished session. Returns a process exit code."""
    a = Audit()
    traj = ag.LOGS / f"{cid}.trajectory.jsonl"
    summary = ag.LOGS / f"{cid}.json"

    if not a.check("TRAJECTORY", traj.exists(), str(traj)):
        return a.report()
    recs = list(records(traj))
    a.check("TRAJECTORY", True, f"{len(recs)} records, {traj.name}")

    # ---- context channels ---------------------------------------------------
    whole = traj.read_text(encoding="utf-8")
    for name, patterns in PROBES.items():
        hits = [p for p in patterns if re.search(p, whole, re.I)]
        a.check(name, not hits,
                "clean" if not hits else f"matched {hits}")

    # ---- system-reminder blocks --------------------------------------------
    # The CLI wraps tool results in one. Anything else is an instruction that
    # reached the model from outside the conversation we wrote.
    stray = 0
    wrapped = 0
    for rec in recs:
        for b in blocks(rec):
            if REMINDER.search(text_of(b)):
                if b.get("type") == "tool_result":
                    wrapped += 1
                else:
                    stray += 1
    a.check("REMINDERS", stray == 0,
            f"{wrapped} wrapping tool results, {stray} free-standing")

    # ---- one model, one CLI -------------------------------------------------
    # Against the CLI the SDK actually spawns — which is the one bundled in its
    # wheel, not whatever `which claude` returns. Getting this wrong is how a
    # manifest ends up naming a version that never ran anything.
    versions = {r["version"] for r in recs if r.get("version")}
    import manifest as mf
    cli = mf._cli()
    spawned = (cli.get("cli_version") or "").split()[0] or None
    a.check("CLI", len(versions) == 1 and (spawned is None or spawned in versions),
            f"{sorted(versions)} in the trajectory, SDK spawns {spawned} "
            f"({'bundled' if '_bundled' in (cli.get('cli_path') or '') else cli.get('cli_path')})")

    if summary.exists():
        info = json.loads(summary.read_text(encoding="utf-8"))
        billed = sorted((info.get("model_usage") or {}).keys())
        a.check("MODEL", billed == [ag.MODEL],
                f"billed {billed}, asked for {ag.MODEL}")
    else:
        a.check("MODEL", False, f"no {summary.name}")

    # ---- the filesystem the session actually reached ------------------------
    # Read from the TRANSCRIPT, not from our own denial log: the question is
    # whether anything outside the workspace was ever served, and only the
    # transcript can answer that. An attempt that was refused is the hook
    # working — it is reported, and it passes, the same way the model's two
    # refused `Bash`/`Edit` calls in the first run were evidence for the
    # allow-list rather than against it.
    ws = (ag.WS / cid).resolve()
    results = {}
    for rec in recs:
        for b in blocks(rec):
            if b.get("type") == "tool_result":
                results[b.get("tool_use_id")] = b
    outside, served = [], []
    for rec in recs:
        for b in blocks(rec):
            if b.get("type") != "tool_use":
                continue
            ti = b.get("input") or {}
            for key in ag.PATH_KEYS:
                raw = ti.get(key)
                if not raw:
                    continue
                t = Path(raw)
                t = (ws / t).resolve() if not t.is_absolute() else t.resolve()
                if t != ws and ws not in t.parents:
                    outside.append(str(raw))
                    res = results.get(b.get("id")) or {}
                    if not res.get("is_error"):
                        served.append(str(raw))
    a.check("PATHS", not served,
            f"{len(outside)} attempt(s) outside the workspace, "
            f"{len(outside) - len(served)} refused"
            + (f"; SERVED {served}" if served else ""))

    # ---- tools --------------------------------------------------------------
    used, refused = {}, []
    for rec in recs:
        for b in blocks(rec):
            if b.get("type") == "tool_use":
                used[b.get("name")] = used.get(b.get("name"), 0) + 1
            if b.get("type") == "tool_result" and b.get("is_error"):
                t = text_of(b)
                if "No such tool available" in t:
                    refused.append(t.split(":")[1].strip().split(".")[0]
                                   if ":" in t else t[:40])
    outside = {k: v for k, v in used.items() if k not in ag.TOOLS}
    a.check("TOOLS", not outside,
            f"{used}" + (f", refused {refused}" if refused else ""))

    # ---- the manifest -------------------------------------------------------
    # The manifest for THIS contract's invocation, not merely the newest one.
    # The script is resumable, so a workspace can easily be older than the last
    # manifest on disk, and auditing a session against another run's provenance
    # would pass for the wrong reason.
    mans = [f for f in sorted(ag.LOGS.glob("run_manifest_*.json"))
            if cid in (json.loads(f.read_text(encoding="utf-8"))
                       .get("contract_order") or [])]
    if a.check("MANIFEST", bool(mans),
               mans[-1].name if mans else f"none covering {cid}"):
        m = json.loads(mans[-1].read_text(encoding="utf-8"))
        opts = m.get("options") or {}
        want = {"setting_sources": [], "skills": [], "strict_mcp_config": True}
        wrong = {k: opts.get(k) for k, v in want.items() if opts.get(k) != v}
        a.check("OPTIONS", not wrong,
                "setting_sources=[], skills=[], strict_mcp_config=True"
                if not wrong else f"wrong: {wrong}")
        got = m.get("env_set") or {}
        unset = [k for k in ag.HERMETIC if got.get(k) != ag.HERMETIC[k]]
        a.check("ENV", not unset and m.get("env_removed") is not None,
                f"{len(m.get('env_removed') or [])} variable(s) swept, "
                f"{len(ag.HERMETIC) - len(unset)}/{len(ag.HERMETIC)} flag(s) set"
                + (f"; NOT SET {unset}" if unset else ""))
    return a.report()


def smallest_outstanding():
    """The cheapest contract that still needs judging — the preflight's subject.

    Cheapest, because the point of the preflight is to exercise the harness, not
    to buy a data point. It is a real session all the same: its predictions are
    kept and the full run continues from it.
    """
    rows = api.load_rows(lib.OUT / "dataset.csv")
    examples = api.pick_examples(rows)
    taught = {e["row"]["contract_id"] for e in examples}
    groups = api.by_contract([r for r in rows if r["contract_id"] not in taught])
    done = api.done_contracts(ag.PREDS, groups)
    left = [(cid, cl) for cid, cl in groups.items() if cid not in done]
    if not left:
        return None
    return min(left, key=lambda kv: len(kv[1]))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", metavar="CONTRACT_ID",
                    help="audit a session already run, without running one")
    ap.add_argument("--contract", metavar="CONTRACT_ID",
                    help="preflight on this contract instead of the smallest")
    args = ap.parse_args()

    if args.audit:
        sys.exit(audit(args.audit))

    removed = ag.env()
    print(f"environment swept: {len(removed)} variable(s) removed"
          + (f" ({', '.join(removed)})" if removed else ""))

    cid = args.contract or smallest_outstanding()
    if not cid:
        print("nothing outstanding to preflight on")
        sys.exit(1)
    print(f"preflight on {cid}\n")
    asyncio.run(ag.run(Namespace(limit=0, shuffle=False, seed=0, rest_every=0,
                                 only=cid), removed))
    sys.exit(audit(cid))


if __name__ == "__main__":
    main()
