"""Prove the agent harness is isolated, before spending a run on it.

Runs ONE real session in a container, on the smallest outstanding contract, then
audits its own trajectory. Nothing is mocked: the session is billed, its
predictions are kept, and the full run continues from it.

    MEMORY / CLAUDE_MD / SKILLS   none of those channels reached the model
    REMINDERS   <system-reminder> only as the CLI's wrapper around a tool result
    MODEL       exactly one model billed, and it is the one asked for
    CLI         one CLI version everywhere, equal to the one the SDK spawns
    TOOLS       nothing outside the four, and every attempt refused
    PATHS       nothing served from outside the workspace
    ENV/OPTIONS the isolation options and flags were actually set

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
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import exp3_agent as ag  # noqa: E402
import isolation  # noqa: E402

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

    # Every session runs in a container, so the workspace is always /work — and
    # that is itself a check: one cwd, and it is the mount point.
    cwds = sorted({r.get("cwd") for r in recs if r.get("cwd")})
    info = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else {}
    a.check("CONTAINER", cwds == ["/work"], f"{info.get('image', '?')}, cwd {cwds}")
    ws = PurePosixPath("/work")

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
        # `models_seen` unions the first session AND every top-up round;
        # `model_usage` covers only the session it came from. Prefer the union,
        # or a stray model billed while finishing a short answer goes unseen.
        billed = sorted(info.get("models_seen")
                        or (info.get("model_usage") or {}).keys())
        a.check("MODEL", billed == [ag.MODEL],
                f"billed {billed}, asked for {ag.MODEL}")
    else:
        a.check("MODEL", False, f"no {summary.name}")

    # ---- the filesystem the session actually reached ------------------------
    # Read from the TRANSCRIPT, not from our own denial log: the question is
    # whether anything outside the workspace was ever SERVED. An attempt that
    # was refused is the hook working, so it is reported and it passes.
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
            for key in isolation.PATH_KEYS:
                raw = ti.get(key)
                if not raw:
                    continue
                t = PurePosixPath(str(raw).replace("\\", "/"))
                t = ws / t if not t.is_absolute() else t
                # Collapse `..` by hand: these are container paths, so they
                # cannot be resolved against this filesystem.
                parts = []
                for seg in t.parts:
                    if seg == "..":
                        if parts[1:]:
                            parts.pop()
                    elif seg != ".":
                        parts.append(seg)
                t = PurePosixPath(*parts)
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
    outside = {k: v for k, v in used.items() if k not in isolation.TOOLS}
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
        # The sweep happened in the container, so the manifest can only say what
        # the host intended. What the session did is in its own summary.
        got = info.get("env_set") or {}
        unset = [k for k in isolation.HERMETIC if got.get(k) != isolation.HERMETIC[k]]
        removed = info.get("env_removed")
        a.check("ENV", not unset and removed is not None,
                f"{len(removed) if isinstance(removed, list) else '?'} variable(s) "
                f"swept in the container, "
                f"{len(isolation.HERMETIC) - len(unset)}/{len(isolation.HERMETIC)} flag(s) set"
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

    cid = args.contract or smallest_outstanding()
    if not cid:
        print("nothing outstanding to preflight on")
        sys.exit(1)

    print(f"preflight on {cid}\n")
    asyncio.run(ag.run(Namespace(limit=0, shuffle=False, seed=0, only=cid,
                                 parallel=1, image=ag.IMAGE)))
    sys.exit(audit(cid))


if __name__ == "__main__":
    main()
