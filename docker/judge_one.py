"""Judge one contract, inside the container.

    /work        the session's cwd: contract.txt, provisions.json, examples/,
                 and the predictions*.json the agent writes
    /opt/task    the two prompts, read-only
    /opt/harness this code, baked into the image

The prompts sit outside /work so the workspace holds exactly what the model is
meant to see. _session.json and _trajectory.jsonl are written only after the
last session ends. Neither /opt path is under /work, so the hook denies both.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolation  # noqa: E402
import predictions  # noqa: E402

TOP_UP_ROUNDS = 2


def fill_prompt(missing):
    """Tell the session, in its own context, which ids it still owes."""
    return (
        f"You have not written judgments for these {len(missing)} provision "
        f"ids from `provisions.json`:\n\n" + ", ".join(missing) + "\n\n"
        "Judge EXACTLY these and no others. Re-read the parts of "
        "`contract.txt` they sit in if you need to. Write them to "
        "`predictions_fill_001.json` (and `_002`, `_003` … if you work in "
        "batches) in the same shape as your earlier files — do not modify a "
        "file you have already written. Probabilities to two decimal places on "
        "a 0.01 grid. Reply with the count when every id above is written."
    )


async def session(root, prompt_text, system_prompt, model, effort, max_turns,
                  resume=None):
    """One agent session. `resume` continues an earlier one by its id."""
    from claude_agent_sdk import RateLimitEvent, ResultMessage, query

    denials = []
    options = isolation.agent_options(root, system_prompt, resume, denials,
                                      model, effort, max_turns)
    result, limits = {}, {}
    async for message in query(prompt=prompt_text, options=options):
        if isinstance(message, RateLimitEvent):
            info = message.rate_limit_info
            limits[info.rate_limit_type or "unknown"] = {
                "status": info.status,
                "utilization": info.utilization,
                "resets_at": info.resets_at,
            }
        elif isinstance(message, ResultMessage):
            result = {
                "is_error": message.is_error,
                "stop_reason": message.stop_reason,
                "num_turns": message.num_turns,
                "duration_ms": message.duration_ms,
                "total_cost_usd": message.total_cost_usd,
                "usage": message.usage,
                "model_usage": message.model_usage,
                "session_id": message.session_id,
                "errors": message.errors,
                "result": (message.result or "")[:500],
            }
    result["path_denials"] = denials
    result["rate_limits"] = limits
    return result


def save_trajectory(root, session_id):
    """Copy the CLI's transcript into the workspace so the host gets it.

    It is the only record of HOW an answer was reached, and the CLI writes it
    under the container's HOME — which vanishes with the container.
    """
    if not session_id:
        return False
    try:
        from claude_agent_sdk import project_key_for_directory
        src = (Path.home() / ".claude" / "projects"
               / project_key_for_directory(str(Path(root).resolve()))
               / f"{session_id}.jsonl")
        if src.exists():
            shutil.copyfile(src, root / "_trajectory.jsonl")
            return True
        # Fall back to a search: the key scheme is the SDK's, not ours.
        hits = sorted((Path.home() / ".claude" / "projects").rglob(f"{session_id}.jsonl"))
        if hits:
            shutil.copyfile(hits[0], root / "_trajectory.jsonl")
            return True
    except Exception as e:                                       # noqa: BLE001
        print(f"    ! could not save the trajectory: {type(e).__name__}: {e}",
              flush=True)
    return False


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="/work")
    ap.add_argument("--task-dir", default="/opt/task")
    ap.add_argument("--model", required=True)
    ap.add_argument("--effort", required=True)
    ap.add_argument("--max-turns", type=int, required=True)
    args = ap.parse_args()

    root = Path(args.workspace)
    task = Path(args.task_dir)
    removed = isolation.env()

    system_prompt = (task / "system_prompt.txt").read_text(encoding="utf-8")
    prompt_text = (task / "task_prompt.txt").read_text(encoding="utf-8")
    wanted = [p["id"] for p in json.loads(
        (root / "provisions.json").read_text(encoding="utf-8"))]

    result = await session(root, prompt_text, system_prompt, args.model,
                           args.effort, args.max_turns)
    cost = result.get("total_cost_usd") or 0.0
    models = set((result.get("model_usage") or {}).keys())
    denials = list(result.get("path_denials") or [])

    # A short answer is not an answer. Resume and ask for what is missing rather
    # than let unjudged provisions be scored as "not risky" by default.
    session_id = result.get("session_id")
    tops = []
    for r in range(TOP_UP_ROUNDS):
        judged, _ = predictions.judgments_in(root)
        missing = [i for i in wanted if i not in judged]
        if not missing:
            break
        print(f"  ~ {len(missing)} unwritten, resuming (round {r + 1})", flush=True)
        try:
            extra = await session(root, fill_prompt(missing), system_prompt,
                                  args.model, args.effort, args.max_turns,
                                  resume=session_id)
        except Exception as e:                                   # noqa: BLE001
            print(f"  ! top-up raised {type(e).__name__}: {str(e)[:160]}", flush=True)
            break
        session_id = extra.get("session_id") or session_id
        cost += extra.get("total_cost_usd") or 0.0
        models |= set((extra.get("model_usage") or {}).keys())
        denials += list(extra.get("path_denials") or [])
        tops.append(extra)
        after, _ = predictions.judgments_in(root)
        if len(after) <= len(judged):
            break                       # no progress; another round would not help

    judged, notes = predictions.judgments_in(root)
    saved = save_trajectory(root, session_id)

    result.update({
        "session_id": session_id,
        "total_cost_usd": round(cost, 4),
        "models_seen": sorted(models),
        "path_denials": denials,
        "top_ups": tops,
        "n_judged": len(judged),
        "n_wanted": len(wanted),
        "parse_notes": notes,
        "trajectory_saved": saved,
        "env_removed": removed,
        "env_set": isolation.HERMETIC,
    })
    (root / "_session.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")

    print(f"  {len(judged)}/{len(wanted)} judged, "
          f"{result.get('num_turns', '?')} turns, ${cost:.2f}, "
          f"models {sorted(models)}", flush=True)
    # Non-zero only when nothing at all was produced. A partial contract is
    # worth keeping and the host decides what to do with it.
    return 0 if judged else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
