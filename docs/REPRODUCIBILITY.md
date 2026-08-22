# Reproducibility of the agent experiment

A review found that the agent experiment starts a fresh *conversation* for each
contract but does not establish a clean *environment*. This document lists each
point it raised, what was done about it, and how the claims are checked.

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
  recorded; values are not. Since the run moved into a container the sweep has
  nothing left to remove: the container starts with no `CLAUDE_*` or
  `ANTHROPIC_*` variable, and all 64 sessions recorded **0 variables swept**.
  The code is kept because it is what makes that number checkable.

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
  session's workspace and refuses anything outside it, recording the refusal.
  The argument names it checks include `pattern` and `glob`, not only
  `file_path` — a `Glob` call takes its path through `pattern`, and a hook that
  inspects only `file_path` lets absolute glob patterns through untouched.
  `test_isolation.py` pins this in 22 cases and runs both on the host and inside
  the image. Across the 64 sessions the model made 32 attempts outside the
  workspace, all `Read`, all refused, all at invented locations
  (`/tmp/outputs/...`, `/mnt/user-data/outputs/...`, `/Users/you/work/...`);
  every session then found the real path and finished normally.

- **`claude-agent-sdk` was absent from `requirements.txt`**, and the two entries
  present used `>=` floors.
  **Fixed:** all dependencies pinned with `==` and committed before the run:
  `claude-agent-sdk==0.2.139`, `anthropic==0.122.0`, `matplotlib==3.8.4`,
  `openpyxl==3.1.2`, installed into the image at build time. Read the manifests
  with one caveat: they record the **host** environment, which drives the run
  but does not perform it, and in every manifest written so far `matplotlib` is
  3.11.1 — that host was CPython 3.13 on Windows, where 3.8.4 has no wheel. The
  host has since been rebuilt on CPython 3.12, where the pinned wheel installs,
  so a manifest written now would record 3.8.4. The two versions that decide
  what a session does,
  `claude-agent-sdk` and `anthropic`, match everywhere; `matplotlib` and
  `openpyxl` draw figures and read spreadsheets and are imported by nothing a
  session runs. The SDK must be newer than 0.1.59, below which
  `setting_sources=[]` was mishandled.

- **Only the CLI version was recorded; nothing else about the machine.**
  **Fixed:** each invocation writes
  `output/llm_logs/exp3_agent/run_manifest_<stamp>.json` before its first session
  and again at the end: SDK and CLI versions, interpreter, platform, git commit
  and dirty flag, the isolation options verbatim, the names of every environment
  variable swept, the flags set, SHA-256 of the prompts and of `dataset.csv` and
  `contracts.json`, the model, the effort, the worked examples chosen, the
  contract order, and the container's image id and content hashes. §4 maps each
  field to the requirement it answers. Stamped
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

- **The session ran as an ordinary user on a developer machine.** The
  environment sweep cannot reach managed organisational policy
  (`/etc/claude-code/managed-settings.json`), and a machine that has ever run
  Claude Code interactively carries a `~/.claude` with settings, a memory store
  and skills in it.
  **Fixed:** every session runs in its own container, from an image pinned by
  digest (`ubuntu@sha256:d78ab764...`), as a non-root user whose home was
  created by the build and holds nothing. There is no managed policy file, no
  `~/.claude`, no skills marketplace and no `CLAUDE.md` anywhere on the
  filesystem — not because they were disabled but because they were never
  installed. The container mounts exactly one contract's workspace at `/work`
  and the two prompts read-only at `/opt/task`; `dataset.csv`, this repository
  and the other 63 contracts are not on its filesystem at all. The build asserts
  that the SDK's bundled CLI is 2.1.233 before the image is usable, so an image
  that builds cannot quietly carry a different CLI.

- **Authentication had to enter the container without bringing configuration
  with it.** Bind-mounting `~/.claude` would import settings, memory and skills
  along with the credential.
  **Fixed:** `CLAUDE_CODE_OAUTH_TOKEN` is passed as an environment variable, or
  failing that the single file `~/.claude/.credentials.json` is mounted
  read-only. The directory is never mounted. Manifests record which route was
  used, never the value.

- **Not raised in the review, found in our own audit: the two arms were not
  shown the same worked examples.** The agent's workspace carried each example's
  full contract; `exp3_llm_api.py` puts only the two provision texts and the
  court's verbatim words in its few-shot block. The agent therefore had evidence
  available that the arm it is compared against did not.
  **Fixed:** the example contracts are gone from the workspace, along with the
  prompt line offering them, and everything was rerun. A census of the
  trajectories shows the affordance was never used in either run — all 64
  sessions read all three `notes.md`, none ever opened an example contract — so
  no result depended on it, but the comparison should not have rested on the
  model declining an advantage it was offered.

## 2. Two findings in the review that do not hold

- **"Memory is keyed by the git repository, so all workspaces under it share one
  store."** It is keyed by the absolute working directory: the 64 per-contract
  workspaces produced 64 separate directories under `~/.claude/projects/`, none
  containing a `memory/` folder. The workspace layout needed no change.

- **"A `Bash` or `Edit` call in the trajectories means the executed harness
  differed from the committed one, or the tool restriction did not hold."** Such
  a call returns *"No such tool available: Bash. Bash exists but is not enabled
  in this context."* — a refusal is evidence the restriction held, not that it
  failed. `disallowed_tools` was added regardless, because "never listed" and
  "explicitly refused" are different claims and only the second is checkable
  from outside. A census of all 727 tool calls across the 64 sessions gives
  `Read 445 / Write 165 / Glob 92 / Grep 25` and nothing else.

## 3. Not fixed

- **One run per condition.** Neither arm sets a temperature or a seed, and the
  API exposes no way to make sampling deterministic, so a rerun will not
  reproduce the same numbers. No repeat study was done, on cost, so the
  run-to-run variance of each metric is **unquantified**.

  One incidental measurement sets the scale. The agent arm was executed twice
  under conditions verified identical by matching `prompt_sha256`,
  `input_sha256`, model, effort, turn ceiling and image; between those two
  executions ROC-AUC moved by about 0.02 and recall@0.5 by about 0.10 across the
  full corpus. That is one observation, not an estimate, but it means
  differences of a few hundredths in ROC-AUC are not interpretable on their own.
  Which findings survive that caution, and which do not, is set out in §6 of
  [REPORT.md](REPORT.md). This remains the one open item that changes how the
  results may be read.

- **`claude-opus-5` is an alias.** Covered in §1: what is observable is
  recorded, but there is no dated snapshot to pin.

## 4. What is logged, and where

Two files per run, both committed.

**`run_manifest_<stamp>.json`** — one per launch, written before the first
session and again at the end:

| Required | Field |
|---|---|
| SDK / CLI version | `packages`; `cli_version`, `cli_path` (the bundled binary that ran), `cli_on_path` (what `claude --version` would report) |
| Environment allowlist | `env_set`, `env_removed` |
| Prompt / input hashes | `prompt_sha256`, `input_sha256`; the system prompt is stored as a `sha256:` digest, not as text |
| Model, effort | `model` as asked for, `models_seen` as billed, `effort`, `max_turns` |
| Run order | `contract_order`, `seed`, `examples` |
| Machine | `python`, `platform`, `git_commit`, `git_dirty`, `billing` |
| Isolation | `options` verbatim — `setting_sources`, `skills`, `strict_mcp_config`, `disallowed_tools`, `hooks`, `cwd` |
| Container | image tag, image id, SHA-256 of the Dockerfile, the entrypoint and `isolation.py` |

**`<cid>.json`** — one per contract: model, effort, `models_seen`, session id,
turns, usage, `container_rc`, image, `path_denials`, and `env_removed` — the
sweep's own report that there was nothing left to remove. Beside it,
`<cid>.trajectory.jsonl` and `<cid>.container.log`.

**The instruction-loading manifest** is the one requirement met differently, and
the difference should be stated rather than glossed: the CLI emits no such
record, so there is no positive list of what was loaded. In its place, nothing
is installed to load — no `~/.claude`, no `CLAUDE.md`, no skills, no
managed-settings file anywhere in the image — and `preflight.py` audits the
transcript for their absence (§5). Absence by construction plus a negative audit
is the strongest evidence available without CLI support, and it is weaker than
what was asked for.

## 5. How the claims are checked

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
  values and every environment flag present in the session's own environment;
- the session ran in the expected image with `/work` as its only working
  directory, and the container had 0 environment variables to sweep — the one
  number a run on a developer machine could not produce.

Two early runs failed: once because the manifest named a CLI that never ran,
once because `DISABLE_NON_ESSENTIAL_MODEL_CALLS` is not a valid variable name.
Both are options that appear to take effect while doing nothing, and neither is
visible by reading the code. The current harness passes all 14 checks.

```sh
python src/experiments/preflight.py                  # run one session, audit it
python src/experiments/preflight.py --audit <cid>    # audit one already run
```

## 6. Reproducing the run

```sh
pip install -r requirements.txt        # host side: enough to build and drive
docker build -f docker/Dockerfile -t contract-risk-judge:0.2.139 .
claude setup-token                     # by hand, once; the token goes in .env
python src/experiments/preflight.py    # must print PREFLIGHT PASSED
python src/experiments/exp3_agent.py --shuffle --parallel 6
python src/experiments/compare_exp3.py
python src/experiments/plot_exp3_thresholds.py --run agent
```

`--parallel` only sets how many containers run at once. Each contract is a
separate session in a separate container, so the number does not affect results.

The dataset needs no API key: `step0_corpus.py` and `build_dataset.py` make no
model calls, and `build_dataset.py` re-cuts every row from disk and refuses to
write unless the text reproduces exactly. A rerun reproduces the procedure; the
model's answers will differ, and by how much is the open question in §3.

Every session leaves `output/llm_logs/exp3_agent/<cid>.trajectory.jsonl` — every
tool call, thinking block, the CLI version and the working directory — plus a
`<cid>.json` recording turns, usage, models billed, refused paths and the image
it ran in. The claims here can be re-audited from those artefacts without
rerunning anything.
