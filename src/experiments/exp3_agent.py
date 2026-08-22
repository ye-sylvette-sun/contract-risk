"""Experiment 3, agentic: one Claude Code session per contract, each in a container.

Same task and same prompts as `exp3_llm_api.py`; only the delivery differs. The
one-shot arm answers in one pass; here the model gets a workspace and decides
what to read — the point being that Category 2 ("does this conflict with another
provision?") is a search problem.

Isolation is structural: the container has no `~/.claude`, no settings, no
skills, no managed policy, and only this contract's workspace is mounted, so
`dataset.csv` and the other contracts are not on its filesystem at all.

    host                                container
    build_workspace()  ->  output/exp3_agent_ws/<cid>  ->  /work
    the two prompts    ->  a temp dir                  ->  /opt/task (ro)
    read back: predictions*.json, _session.json, _trajectory.jsonl

Usage:
    python src/experiments/exp3_agent.py --only <cid>
    python src/experiments/exp3_agent.py --shuffle --parallel 4
"""
import argparse
import asyncio
import csv
import hashlib
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import manifest  # noqa: E402
import isolation  # noqa: E402
import predictions  # noqa: E402

# The taxonomy, worked examples and gold mapping come from the one-shot
# experiment by import, so the two arms cannot drift apart.
_spec = importlib.util.spec_from_file_location(
    "exp3_llm_api", Path(__file__).with_name("exp3_llm_api.py"))
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

MODEL = "claude-opus-5"
EFFORT = "high"
MAX_TURNS = 60
IMAGE = "contract-risk-judge:0.2.139"
CONTAINER_TIMEOUT = 3600

PREDS = lib.OUT / "exp3_agent_preds.csv"
RAW = lib.OUT / "exp3_agent"
WS = lib.OUT / "exp3_agent_ws"
LOGS = lib.OUT / "llm_logs" / "exp3_agent"

WARN_AT = 0.90              # rate-limit utilisation at which we stop launching


# -------------------------------------------------------------- workspace ----
def write_examples(root, examples):
    """One directory per worked pair, holding the notes and nothing else.

    The example CONTRACTS are deliberately not here. `exp3_llm_api.py` puts only
    the two provision texts and the court's words in its few-shot block, so
    shipping the full contracts would give this arm more evidence than the one
    it is compared against.
    """
    for e in examples:
        r, foil = e["row"], e["foil"]
        d = root / "examples" / f"{e['code']}_{r['contract_id']}"
        d.mkdir(parents=True, exist_ok=True)

        notes = [
            f"# Worked example — {api.CATEGORY_NAME[e['code']]}",
            "",
            f"Contract `{r['contract_id']}`, filed in {r['citation']}.",
            "",
            f"A court construed **{e['n_pos']}** of this contract's provisions; "
            f"the other **{e['n_neg']}** it did not. Read that as the base rate "
            f"to expect, NOT as a quota to reproduce.",
            "",
            "## HIGH RISK — a federal court construed this provision",
            "",
            f'"{r["clause_name"]}"  (risk type: {e["code"]})',
            "",
            "```", api.flat(r["clause_text"]), "```",
            "",
            f"### What the court said, verbatim from the opinion in {r['citation']}",
            "",
            "```", r["opinion_comment"].strip(), "```",
        ]
        if foil:
            notes += [
                "",
                "## LOWER RISK — from the same contract",
                "",
                f'"{foil["clause_name"]}". No court construed it in this case. '
                f"It is not established to be sound, only never fought over.",
                "",
                "```", api.flat(foil["clause_text"]), "```",
            ]
        (d / "notes.md").write_text("\n".join(notes), encoding="utf-8")


def build_workspace(cid, clauses, registry, examples):
    """Lay out one contract's workspace. Returns (dir, real_of)."""
    root = WS / cid
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    shutil.copyfile(lib.ROOT / registry[cid]["file"], root / "contract.txt")
    write_examples(root, examples)

    # Opaque ids in document order. The dataset's own pos1/neg1 ids would put
    # the gold label on the door of every provision.
    shown, _opaque_of, real_of = api.anonymise(clauses)
    (root / "provisions.json").write_text(json.dumps(
        [{"id": oid, "name": c["clause_name"], "text": api.flat(c["clause_text"])}
         for oid, c in zip(real_of, shown)],
        ensure_ascii=False, indent=2), encoding="utf-8")
    return root, real_of


def task_prompt(cid, citation, n, examples):
    """What to do with the workspace. The judging criteria are in the SYSTEM
    prompt, taken verbatim from prompts/exp3.md so both arms are asked the same
    question in the same words."""
    dirs = "\n".join(
        f"    examples/{e['code']}_{e['row']['contract_id']}/" for e in examples)
    return f"""You are judging contract `{cid}`, filed in {citation}.

Your working directory holds everything you need.

    contract.txt          the contract to judge, in full
    provisions.json       the {n} provisions to judge, in the order they appear
                          in the contract, each with an id like `c001`
    examples/             one worked pair per risk type — read these FIRST
{dirs}

Work in this order.

1. Read every `examples/*/notes.md`. Each gives a provision a federal court
   actually construed and the court's own words about the dispute, paired with a
   provision from the same contract that no court construed. That is the
   standard to apply — not your own sense of what looks badly drafted.
2. Read `contract.txt`. You need the whole instrument for Category 2: a conflict
   cannot be seen from one provision alone. It is long — read it in pieces, and
   use Grep to chase a defined term or a cross-reference wherever it leads.
3. Read `provisions.json` and judge every provision in it.

For each provision decide two INDEPENDENT probabilities, each to two decimal
places on a 0.01 grid (0.03, 0.17, 0.62 — do not round to the nearest 0.05):

  * `prob_cat1` — that it carries a Category 1 risk (an intrinsic textual
    defect, visible in the provision itself)
  * `prob_cat2` — that it carries a Category 2 risk (a defect in its
    relationship to the rest of the instrument)

Take the second one seriously: you can search this contract, so go and check.
Name the other provision you checked against.

Write your answers to `predictions.json` in the working directory, as an object:

    {{"judgments": [
      {{"clause_id": "c001",
        "reasoning_cat1": "two sentences on the provision's own wording",
        "reasoning_cat2": "two sentences on its fit with the rest of the contract",
        "prob_cat1": 0.07,
        "prob_cat2": 0.03}},
      ...
    ]}}

Rules for that file:

  * one entry for EVERY id in `provisions.json`, all {n} of them, same order
  * `clause_id` copied exactly — `c001`, not the provision's heading
  * keep each reasoning to two sentences; you are producing a judgment, not a
    memorandum
  * write the file even if you are unsure about some provisions; a missing entry
    is scored as "not risky" at probability 0, which is the worst outcome
    available to you — an entry you are unsure of is always better than none

**Work in batches and flush as you go.** Do not hold {n} judgments in your head
and write them all at the end: if the session ends early, everything unwritten
is lost and scored against you. Instead judge fifty or so provisions, write them
out, then continue with the next batch. Write each batch to its OWN numbered
file — `predictions_001.json`, `predictions_002.json`, and so on, each with the
same `judgments` shape. They are read together and concatenated, so never
rewrite an earlier file and never repeat an id.

When every provision in `provisions.json` has been written to one of those
files, stop. Reply with one line: how many judgments you wrote.
"""


def read_predictions(root, real_of, clauses):
    """The agent's files -> {dataset clause_id: judgment}."""
    by_id, notes = predictions.judgments_in(root)
    # An id we never issued is dropped rather than guessed at; the agent
    # sometimes appends a sentinel row of its own invention.
    by_real = {real_of[k]: j for k, j in by_id.items() if k in real_of}
    why = "; ".join(notes) if notes else None
    if len(by_real) < len(clauses) and not why:
        why = f"only {len(by_real)} of {len(clauses)} provisions judged"
    return by_real, why


# ------------------------------------------------------------------ docker ---
def auth_mount():
    """How the container proves it is you, without importing your config.

    CLAUDE_CODE_OAUTH_TOKEN is preferred, and `.env` counts — `lib` loads it
    into os.environ on import. The fallback mounts a single FILE, never
    `~/.claude`: the directory would bring settings.json, CLAUDE.md and the
    memory store with it.
    """
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "token", []
    cred = Path.home() / ".claude" / ".credentials.json"
    if cred.exists():
        return "credentials-file", [
            "-v", f"{cred.resolve().as_posix()}:/home/judge/.claude/.credentials.json:ro"]
    sys.exit("No subscription credential. Run `claude setup-token` and export "
             "CLAUDE_CODE_OAUTH_TOKEN, or log in with `claude`. See README.")


def image_id(image):
    out = subprocess.run(["docker", "image", "inspect", image, "--format",
                          "{{.Id}}"], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"image {image} not found. Build it:\n"
                 f"  docker build -f docker/Dockerfile -t {image} .")
    return out.stdout.strip()


async def run_container(cid, root, task_dir, image, auth_extra, log_to):
    """`docker run` one contract to completion. Returns (rc, output)."""
    cmd = ["docker", "run", "--rm",
           "--name", f"judge-{cid[:40]}-{int(time.time())}",
           "-v", f"{root.resolve().as_posix()}:/work",
           "-v", f"{task_dir.resolve().as_posix()}:/opt/task:ro",
           *auth_extra,
           "--memory", "4g", "--cpus", "2"]
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        cmd += ["-e", "CLAUDE_CODE_OAUTH_TOKEN"]
    cmd += [image, "--workspace", "/work", "--task-dir", "/opt/task",
            "--model", MODEL, "--effort", EFFORT, "--max-turns", str(MAX_TURNS)]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), CONTAINER_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"timed out after {CONTAINER_TIMEOUT}s"
    text = (out or b"").decode("utf-8", "replace")
    log_to.write_text(text, encoding="utf-8")
    return proc.returncode, text


class Gate:
    """Stops launching new containers while the rate-limit window is gone."""

    def __init__(self):
        self.until, self.why = 0.0, None

    def note(self, rate_limits):
        for kind, info in (rate_limits or {}).items():
            resets = info.get("resets_at") or 0
            if info.get("status") == "rejected":
                self.until, self.why = max(self.until, resets + 60), f"{kind} limit reached"
            elif (info.get("utilization") or 0) >= WARN_AT:
                self.until, self.why = max(self.until, resets + 60), f"{kind} nearly used up"

    async def wait(self):
        while self.until - time.time() > 0:
            left = self.until - time.time()
            print(f"  ~ {self.why}, sleeping {left / 3600:.1f}h", flush=True)
            await asyncio.sleep(min(left, 300))


async def judge_contract(cid, clauses, registry, examples, system_prompt, image,
                         auth_extra, gate, sem, counter, total):
    async with sem:
        await gate.wait()
        root, real_of = build_workspace(cid, clauses, registry, examples)

        # Prompts live outside /work so the workspace holds exactly what the
        # model is meant to see: contract.txt, provisions.json, examples/.
        task_dir = Path(tempfile.mkdtemp(prefix=f"task_{cid[:24]}_"))
        (task_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")
        (task_dir / "task_prompt.txt").write_text(
            task_prompt(cid, clauses[0]["citation"], len(clauses), examples),
            encoding="utf-8")

        n = next(counter)
        print(f"[{n}/{total}] {cid}: {len(clauses)} provisions, "
              f"{(root / 'contract.txt').stat().st_size:,}-char contract", flush=True)

        started = time.time()
        try:
            rc, out = await run_container(cid, root, task_dir, image, auth_extra,
                                          LOGS / f"{cid}.container.log")
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        sf = root / "_session.json"
        sess = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}
        gate.note(sess.get("rate_limits"))

        traj = root / "_trajectory.jsonl"
        if traj.exists():
            shutil.copyfile(traj, LOGS / f"{cid}.trajectory.jsonl")

        judged, why_not = read_predictions(root, real_of, clauses)
        cost = sess.get("total_cost_usd") or 0.0
        models = sess.get("models_seen") or []

        print(f"    {cid}: {len(judged)}/{len(clauses)} judged"
              + (f"  ({len(clauses) - len(judged)} MISSING)"
                 if len(judged) < len(clauses) else "")
              + f"  | {sess.get('num_turns', '?')} turns, "
                f"{time.time() - started:.0f}s, ${cost:.2f}, rc={rc}", flush=True)
        if why_not:
            print(f"    ! {cid}: {why_not}", flush=True)
        if sess.get("path_denials"):
            print(f"    ! {cid}: {len(sess['path_denials'])} path(s) denied outside "
                  f"the workspace", flush=True)
        if set(models) - {MODEL}:
            print(f"    ! {cid}: models billed {sorted(models)}", flush=True)
        if rc not in (0, 1):
            print(f"    ! {cid}: container exited {rc}: {out[-300:]}", flush=True)

        (LOGS / f"{cid}.json").write_text(json.dumps(
            {"contract_id": cid, "model": MODEL, "effort": EFFORT,
             "n_provisions": len(clauses), "container_rc": rc, "image": image,
             **sess}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (RAW / f"{cid}.json").write_text(json.dumps(
            {"contract_id": cid, "_id_map": real_of,
             "judgments": [dict(j, dataset_clause_id=k) for k, j in judged.items()]},
            ensure_ascii=False, indent=2), encoding="utf-8")

        return cid, clauses, judged, cost, models


# ------------------------------------------------------------------- run -----
def counter_from(start=1):
    n = start
    while True:
        yield n
        n += 1


async def run(args):
    image = args.image
    img_id = image_id(image)
    how, auth_extra = auth_mount()

    rows = api.load_rows(lib.OUT / "dataset.csv")
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    examples = api.pick_examples(rows)
    taught = {e["row"]["contract_id"] for e in examples}
    print("examples (%d): " % len(examples) + ", ".join(
        f"{e['code']}={e['row']['contract_id']}({e['n_pos']}p/{e['n_neg']}n)"
        for e in examples))

    eval_rows = [r for r in rows if r["contract_id"] not in taught]
    groups = api.by_contract(eval_rows)
    # `groups` makes "done" mean COMPLETE — a contract with unjudged provisions
    # comes back rather than being counted as finished.
    done = api.done_contracts(PREDS, groups)
    todo = [(cid, cl) for cid, cl in groups.items() if cid not in done]
    if args.only:
        todo = [(cid, cl) for cid, cl in todo if cid == args.only]
        if not todo:
            sys.exit(f"{args.only}: not outstanding (done, an example, or no such id)")
    if args.shuffle:
        random.Random(args.seed).shuffle(todo)
        print(f"order shuffled, seed {args.seed}")
    if args.limit:
        todo = todo[:args.limit]

    n_pos = sum(1 for r in eval_rows if r["label"] == "POSITIVE")
    print(f"{len(eval_rows)} clauses ({n_pos} risky) in {len(groups)} contracts "
          f"after excluding {len(taught)} example contract(s)")
    print(f"{len(done)} contract(s) done, {len(todo)} to run ({MODEL}, effort "
          f"{EFFORT}, max {MAX_TURNS} turns, {args.parallel} at a time, auth via {how})")
    print(f"image {image}\n      {img_id}")
    if not todo:
        return

    system_prompt = lib.prompt("exp3", examples="", contract_id="", citation="",
                               document="", clauses="")["SYSTEM"]
    for d in (RAW, LOGS, WS):
        d.mkdir(parents=True, exist_ok=True)

    # Provenance, written before the first session. The options come from the
    # same function the container calls, so the manifest cannot describe
    # options the sessions did not use.
    opts = manifest.options_digest(isolation.agent_options(
        Path("/work"), system_prompt, None, [], MODEL, EFFORT, MAX_TURNS))
    opts["system_prompt"] = "sha256:" + hashlib.sha256(
        system_prompt.encode("utf-8")).hexdigest()
    opts["cwd"] = "/work"
    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    man = manifest.Manifest(
        LOGS / f"run_manifest_{started}.json", run="agent", repo_root=lib.ROOT,
        model=MODEL, effort=EFFORT, max_turns=MAX_TURNS, billing="subscription",
        options=opts, seed=args.seed if args.shuffle else None,
        container={"image": image, "image_id": img_id, "auth": how,
                   "parallel": args.parallel,
                   "dockerfile_sha256": manifest.sha256(lib.ROOT / "docker" / "Dockerfile"),
                   "entrypoint_sha256": manifest.sha256(lib.ROOT / "docker" / "judge_one.py"),
                   "isolation_sha256": manifest.sha256(
                       lib.ROOT / "src" / "experiments" / "isolation.py")},
        contract_order=[cid for cid, _ in todo],
        examples=[{"code": e["code"], "contract_id": e["row"]["contract_id"],
                   "clause_id": e["row"]["clause_id"]} for e in examples],
    )
    # The sweep happens inside each container; per-contract _session.json
    # records what was actually removed and set there.
    man.data["env_removed"] = "swept in container; see per-contract _session.json"
    man.data["env_set"] = isolation.HERMETIC
    man.hash_prompts(lib.PROMPTS / "exp3.md", lib.PROMPTS / "exp3.schema.json")
    man.hash_inputs(dataset=lib.OUT / "dataset.csv",
                    contracts=lib.OUT / "contracts.json")
    man.write()

    new = not PREDS.exists()
    fout = open(PREDS, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=api.FIELDS)
    if new:
        writer.writeheader()

    gate, sem, lock = Gate(), asyncio.Semaphore(args.parallel), asyncio.Lock()
    counter = counter_from()
    spend, seen_models = 0.0, set()

    tasks = [judge_contract(cid, cl, registry, examples, system_prompt, image,
                            auth_extra, gate, sem, counter, len(todo))
             for cid, cl in todo]

    for coro in asyncio.as_completed(tasks):
        try:
            cid, clauses, judged, cost, models = await coro
        except Exception as e:                                   # noqa: BLE001
            # One contract must never end the run.
            print(f"    ! contract raised {type(e).__name__}: {str(e)[:200]}",
                  flush=True)
            continue
        spend += cost
        seen_models.update(models)
        async with lock:
            for c in clauses:
                j = judged.get(c["clause_id"])
                ok = j is not None
                writer.writerow({
                    "contract_id": cid, "citation": c["citation"],
                    "clause_id": c["clause_id"], "clause_name": c["clause_name"],
                    "label": c["label"], "taxonomy": c["taxonomy"],
                    "gold": api.gold_of(c), "gold_subtype": api.gold_fine(c),
                    "pred": api.pred_from_probs(api._f(j.get("prob_cat1")),
                                                api._f(j.get("prob_cat2"))) if ok else "",
                    "prob_cat1": api._f(j.get("prob_cat1")) if ok else "",
                    "prob_cat2": api._f(j.get("prob_cat2")) if ok else "",
                    "reasoning_cat1": j.get("reasoning_cat1", "") if ok else "",
                    "reasoning_cat2": j.get("reasoning_cat2", "") if ok else "",
                    "ok": "1" if ok else "0"})
            fout.flush()

    fout.close()
    man.finish(contracts_run=len(todo), cost_usd_api_equivalent=round(spend, 2),
               models_seen=sorted(seen_models))
    print(f"\n{len(todo)} session(s), ${spend:.2f} at API-equivalent rates")
    if seen_models and set(seen_models) != {MODEL}:
        print(f"  ! models billed: {sorted(seen_models)} — expected only {MODEL}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="run only N contracts")
    ap.add_argument("--shuffle", action="store_true", help="seeded random order")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only", metavar="CONTRACT_ID", help="one contract by id")
    ap.add_argument("--parallel", type=int, default=4, help="containers at once")
    ap.add_argument("--image", default=IMAGE)
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
