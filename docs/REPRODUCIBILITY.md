# Reproducibility of the agent experiment

A review found that the agent experiment starts a fresh *conversation* for each
contract but does not establish a clean *environment*. This document lists each
point it raised, what was done about it, and how the claims are checked.

The corrected harness is on `main`; the superseded code and results are on
`legacy_agent_experiment_8.17`.

Only the **agent** arm is affected. `exp3_llm_api.py` is a stateless Messages API
call with no CLI, settings, memory or filesystem, and its predictions were not
rerun.

---

## 1. Defects and fixes

- **`setting_sources=None` loads every settings file.** The SDK treats `None` as
  "load all sources, matching CLI defaults": user `~/.claude/settings.json`,
  project `.claude/settings.json`, local `.claude/settings.local.json`, and every
  `CLAUDE.md` on the ancestor chain above the workspace. The code's comment said
  the opposite.
  **Fixed:** `setting_sources=[]`, the SDK's isolation mode, and the comment
  removed. `ClaudeAgentOptions` is a dataclass, so a non-existent option name
  raises `TypeError` at construction — if a session starts, these options were
  real.

- **`skills=None` is not "skills off".** `None` means the SDK configures nothing,
  leaving the CLI's own defaults in place, including any skills marketplace
  registered in user settings.
  **Fixed:** `skills=[]`.

- **Project, user and plugin MCP servers were still eligible to load.**
  **Fixed:** `strict_mcp_config=True`.

- **Auto memory and `CLAUDE.md` injection are outside `setting_sources`** and
  both default to on.
  **Fixed:** `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and
  `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, set in the parent process before the SDK
  spawns anything.

- **The whole parent environment was inherited; `env()` removed three
  variables.** Any `CLAUDE_*`, `ANTHROPIC_*` or proxy variable on the machine
  entered the run, unrecorded.
  **Fixed:** `env()` sweeps the parent process, removing by name every variable
  matching `ANTHROPIC_*`, `CLAUDE_*`, `CLAUDECODE`, `DISABLE_*` or a proxy name,
  then sets the flags above. The review's proposed `env={...}` does not achieve
  this: the SDK builds `{**inherited_env, ..., **options.env, ...}`, so
  `options.env` merges over the inherited environment and can only add.
  Restricting inheritance must happen in the parent. Variable names are
  recorded; values are not.

- **The CLI could update itself mid-run.**
  **Fixed:** `DISABLE_AUTOUPDATER=1`. Limited effect in practice — see the CLI
  version item below.

- **The agent arm was not running a single model.** All 64 sessions also billed
  `claude-haiku-4-5` for the CLI's internal calls.
  **Fixed:** `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`. Note that
  `DISABLE_NON_ESSENTIAL_MODEL_CALLS` is not a variable the CLI recognises;
  setting it has no effect.

- **The tool restriction was implicit** — `Bash` and `Edit` were absent from the
  allow-list rather than forbidden.
  **Fixed:** an explicit `disallowed_tools` alongside the four-tool allow-list,
  naming `Bash`, `Edit`, `Task`, `Skill`, `WebFetch`, `WebSearch` and the rest.

- **No filesystem boundary.** The session has no `Bash`, but `Read` and `Glob`
  accept absolute paths and could reach this repository's source, the gold labels
  in `dataset.csv`, or another contract's workspace.
  **Fixed:** a `PreToolUse` hook resolves every path argument against the
  session's workspace and refuses anything outside it, recording the refusal. In
  preflight sessions the model requested `/tmp/outputs/...` and
  `/mnt/user-data/outputs/...`; every attempt was refused and the task completed
  normally.

- **`claude-agent-sdk` was absent from `requirements.txt`**, and the two entries
  present used `>=` floors.
  **Fixed:** all dependencies pinned with `==` and committed before the run:
  `claude-agent-sdk==0.2.139`, `anthropic==0.122.0`, `matplotlib==3.8.4`,
  `openpyxl==3.1.2`. The SDK must be newer than 0.1.59, below which
  `setting_sources=[]` was mishandled.

- **Only the CLI version was recorded; nothing else about the machine.**
  **Fixed:** each invocation writes
  `output/llm_logs/exp3_agent/run_manifest_<stamp>.json` before its first session
  and again at the end: SDK and CLI versions, interpreter, platform, git commit
  and dirty flag, the isolation options verbatim, the names of every environment
  variable swept, the flags set, SHA-256 of the prompts and of `dataset.csv` and
  `contracts.json`, the worked examples chosen, and the contract order. Stamped
  rather than overwritten, because the script is resumable.

- **The recorded CLI version would have been the wrong one.** The SDK ships a CLI
  in its own wheel and prefers it to anything on `PATH`. On this machine
  `claude --version` reports 2.1.227 from Homebrew while every session ran on the
  bundled 2.1.233.
  **Fixed:** the manifest resolves the CLI through the SDK's own lookup and
  records its path, its version, and what is on `PATH`. Pinning
  `claude-agent-sdk` therefore pins the CLI.

- **`claude-opus-5` is an alias, not a dated snapshot**, and the API returns the
  alias, so no snapshot can be pinned.
  **Partly fixed:** what is observable is recorded — the `model` field the API
  returns per call, the `model_usage` keys the CLI reports per session, and the
  manifest's start and finish times. If a dated id is exposed for opus-5, pin it
  in `lib.MODEL`.

## 2. Two findings in the review that do not hold

- **"Memory is keyed by the git repository, so all workspaces under it share one
  store."** It is keyed by the absolute working directory: the 64 per-contract
  workspaces produced 64 separate directories under `~/.claude/projects/`, none
  containing a `memory/` folder. The workspace layout needed no change.

- **"One `Bash` call and one `Edit` call in the trajectories mean the executed
  harness differed from the committed one, or the tool restriction did not
  hold."** Both calls returned an error: *"No such tool available: Bash. Bash
  exists but is not enabled in this context."* A census of all 755 tool calls
  across the 64 sessions gives `Read 470 / Write 170 / Glob 92 / Grep 21 /
  Edit 1 / Bash 1`. Two out-of-scope attempts were refused, which is evidence the
  restriction held and that the committed code matches the executed code.
  `disallowed_tools` was added regardless, because "never listed" and "explicitly
  refused" are different claims and only the second is checkable from outside.

## 3. Not fixed

- **One run per condition.** Neither arm sets a temperature or a seed, so a rerun
  will not reproduce the same numbers. Repeated paired runs would bound that
  variance; they are not being done, on cost.

- **No OS-level isolation.** The run happens as an ordinary user on one machine,
  not in a container or under a dedicated account. Managed organisational policy
  is the one channel the environment sweep cannot reach. The preflight verifies
  what actually reached the model's context; the manifest records the machine
  rather than isolating it.

## 4. How the claims are checked

`preflight.py` runs one real session on the smallest outstanding contract, under
the options the full run uses, then audits that session's transcript. It exits
non-zero on any failure. Checks:

- no `MEMORY.md`, `CLAUDE.md`, skills listing or `mcp__*` tool in the transcript;
- `<system-reminder>` only as the CLI's wrapper around a tool result, never
  free-standing;
- exactly one model billed, and it is the one requested;
- one CLI version across every record, equal to the one the SDK resolves;
- no tool used outside the four, and any such attempt refused;
- no path outside the workspace served — refused attempts are recorded and pass;
- a manifest covering this contract, with the isolation options at their set
  values and every environment flag present in `os.environ`.

Its first two runs failed: once because the manifest named a CLI that never ran,
once because `DISABLE_NON_ESSENTIAL_MODEL_CALLS` is not a valid variable name.
Both are options that appear to take effect while doing nothing, and neither is
visible by reading the code.

```sh
python src/experiments/preflight.py                  # run one session, audit it
python src/experiments/preflight.py --audit <cid>    # audit one already run
```

## 5. Reproducing the run

```sh
pip install -r requirements.txt        # exact pins; the CLI comes with the SDK
python src/experiments/preflight.py    # must print PREFLIGHT PASSED
python src/experiments/exp3_agent.py --shuffle
python src/experiments/compare_exp3.py
python src/experiments/plot_exp3_thresholds.py --run agent
```

The dataset needs no API key: `step0_corpus.py` and `build_dataset.py` make no
model calls, and `build_dataset.py` re-cuts every row from disk and refuses to
write unless the text reproduces exactly. A rerun reproduces the procedure; the
model's answers will differ, and by how much is the open question in §3.

Every session leaves `output/llm_logs/exp3_agent/<cid>.trajectory.jsonl` — every
tool call, thinking block, the CLI version and the working directory — so the
claims here can be re-audited from the artefacts without rerunning anything.
