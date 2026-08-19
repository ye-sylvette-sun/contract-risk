# Agent 实验的可复现性 · 说明（中文）

配套文档：[数据集说明](DATASET_ZH.md)、[实验说明](EXPERIMENTS_ZH.md)。英文原文见
[REPRODUCIBILITY.md](REPRODUCIBILITY.md)。

外部 review 指出：agent 实验为每份合同开了新的**对话**，但没有建立干净的**运行环境**。
本文逐条列出 review 提出的问题、各自的处理，以及验证方式。

修好的代码在 `main`，旧代码与旧结果在 `legacy_agent_experiment_8.17`。

只有 **agent** 一路受影响。`exp3_llm_api.py` 是无状态 Messages API 调用，不涉及 CLI、
配置文件、memory 或文件系统，其预测结果未重跑。

---

## 一、问题与处理

- **`setting_sources=None` 会加载全部配置文件。** SDK 对 `None` 的定义是"加载全部来源，
  与 CLI 默认一致"：`~/.claude/settings.json`、`.claude/settings.json`、
  `.claude/settings.local.json`，以及工作目录各级祖先目录下的 `CLAUDE.md`。原代码的
  注释写的是相反的意思。
  **修复：**`setting_sources=[]`（SDK 隔离模式），并删除该注释。`ClaudeAgentOptions`
  是 dataclass，不存在的选项名会在构造时抛 `TypeError`，因此 session 能启动即表示这些
  选项均有效。

- **`skills=None` 不等于关闭 skills。** `None` 表示 SDK 不做配置，CLI 自身默认仍生效，
  包括用户配置中注册的 skills marketplace。
  **修复：**`skills=[]`。

- **项目级、用户级、插件级 MCP server 仍可被加载。**
  **修复：**`strict_mcp_config=True`。

- **auto memory 与 `CLAUDE.md` 注入不受 `setting_sources` 控制**，两者默认开启。
  **修复：**在 SDK 启动子进程前，于父进程设置 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
  与 `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`。

- **父进程环境被整体继承，`env()` 仅删除三个变量。** 机器上任何 `CLAUDE_*`、
  `ANTHROPIC_*` 或代理变量都会进入运行，且无记录。
  **修复：**`env()` 清扫父进程，按名删除匹配 `ANTHROPIC_*`、`CLAUDE_*`、`CLAUDECODE`、
  `DISABLE_*` 及代理变量名的条目，再设置上述开关。注意 review 建议的 `env={...}` 无法
  达到此效果：SDK 构造的是 `{**inherited_env, ..., **options.env, ...}`，`options.env`
  覆盖在继承环境之上，只能增不能减，限制继承必须在父进程完成。记录变量名，不记录值。

- **CLI 可能在运行中途自动升级。**
  **修复：**`DISABLE_AUTOUPDATER=1`。实际影响有限，原因见下文 CLI 版本一条。

- **agent 一路并非只运行一个模型。** 64 个 session 均额外为 CLI 内部调用计费
  `claude-haiku-4-5`。
  **修复：**`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`。注意 CLI 中不存在
  `DISABLE_NON_ESSENTIAL_MODEL_CALLS` 这个变量名，设置它无任何效果。

- **工具限制是隐式的**：`Bash`、`Edit` 只是未出现在白名单中。
  **修复：**在四工具白名单外增加显式 `disallowed_tools`，列出 `Bash`、`Edit`、`Task`、
  `Skill`、`WebFetch`、`WebSearch` 等。

- **无文件系统边界。** session 无 `Bash`，但 `Read`、`Glob` 接受绝对路径，可触及本仓库
  源码、`dataset.csv` 中的标准答案或其他合同的工作区。
  **修复：**`PreToolUse` hook 将每个路径参数相对 session 工作区解析，工作区外的一律
  拒绝并记录。preflight 中模型曾请求 `/tmp/outputs/...` 与
  `/mnt/user-data/outputs/...`，均被拒绝，任务照常完成。

- **`claude-agent-sdk` 不在 `requirements.txt` 中**，已有两条依赖使用 `>=` 下界。
  **修复：**全部改为 `==` 锁定并在运行前提交：`claude-agent-sdk==0.2.139`、
  `anthropic==0.122.0`、`matplotlib==3.8.4`、`openpyxl==3.1.2`。SDK 须高于 0.1.59，
  低于该版本 `setting_sources=[]` 行为不正确。

- **仅记录了 CLI 版本，机器环境无记录。**
  **修复：**每次调用在首个 session 前写入
  `output/llm_logs/exp3_agent/run_manifest_<时间戳>.json`，结束时再写一次，内容包括
  SDK 与 CLI 版本、Python 解释器、平台、git commit 与 dirty 标志、隔离选项原文、被清扫
  的环境变量名单、生效的开关、prompt 与 `dataset.csv`、`contracts.json` 的 SHA-256、
  选中的示范例子、合同运行顺序。文件名带时间戳而非覆盖写，因为脚本可续跑。

- **记录的 CLI 版本会是错的。** SDK 的 wheel 内置一份 CLI 并优先于 `PATH` 使用。本机
  `claude --version` 为 Homebrew 的 2.1.227，实际运行的是内置的 2.1.233。
  **修复：**manifest 通过 SDK 自身的查找逻辑解析 CLI，同时记录其路径、版本与 `PATH`
  上的版本。因此锁定 `claude-agent-sdk` 即锁定 CLI。

- **`claude-opus-5` 是别名而非带日期的快照**，API 返回的即该别名，无法直接锁定。
  **部分修复：**记录可观测项——API 每次调用返回的 `model` 字段、CLI 每个 session 报告的
  `model_usage` 键、manifest 的起止时间。若日后暴露带日期的 id，在 `lib.MODEL` 中锁定。

## 二、review 中两条不成立的判断

- **"memory 位置按 git 仓库确定，仓库下所有工作区共用一份 memory。"** 实际按工作目录
  的绝对路径确定：64 个工作区在 `~/.claude/projects/` 下生成 64 个独立目录，其中没有
  任何一个含 `memory/`。工作区结构无需改动。

- **"轨迹中出现一次 `Bash` 与一次 `Edit` 调用，说明实际代码与提交代码不一致，或工具
  限制失效。"** 两次调用均返回错误：*"No such tool available: Bash. Bash exists but is
  not enabled in this context."* 64 个 session 全部 755 次工具调用的统计为
  `Read 470 / Write 170 / Glob 92 / Grep 21 / Edit 1 / Bash 1`。两次越界尝试均被拒绝，
  这是限制生效、且提交代码与实际运行代码一致的证据。仍加入 `disallowed_tools`，因为
  "未列出"与"被明确拒绝"是两种不同的说法，只有后者可从外部检查。

## 三、未解决的问题

- **每个条件仅运行一次。** 两路均未设 temperature 与 seed，重跑不会得到相同数字。多次
  配对重复可界定该波动，因成本未做。

- **无操作系统层面的隔离。** 运行在单台机器的普通用户下，非容器、非专用系统账号。企业
  托管的组织级策略是环境清扫无法覆盖的通道。preflight 验证的是实际进入模型上下文的
  内容；manifest 记录机器，而非隔离机器。

## 四、验证方式

`preflight.py` 在剩余合同中选最小的一份，以与正式实验完全相同的选项真实运行一个
session，随后审计其轨迹，任一项不通过即以非零码退出。检查项：

- 轨迹中无 `MEMORY.md`、`CLAUDE.md`、skills 列表、`mcp__*` 工具；
- `<system-reminder>` 仅作为 CLI 包裹工具返回结果出现，无独立指令块；
- 计费模型有且仅有一个，且为指定模型；
- 所有记录的 CLI 版本一致，且等于 SDK 解析出的版本；
- 未使用四个工具以外的工具，此类尝试均被拒绝；
- 无工作区外路径被实际读取（被拒绝的尝试计为通过并记录）；
- 存在覆盖该合同的 manifest，其隔离选项为设定值，且环境开关均实际存在于 `os.environ`。

该检查前两次运行均失败：一次因 manifest 记录的 CLI 未参与运行，一次因
`DISABLE_NON_ESSENTIAL_MODEL_CALLS` 不是有效变量名。两者均属选项看似生效、实际无效，
仅靠阅读代码无法发现。

```sh
python src/experiments/preflight.py                  # 运行一个 session 并审计
python src/experiments/preflight.py --audit <cid>    # 审计已运行的 session
```

## 五、复现步骤

```sh
pip install -r requirements.txt        # 精确锁定；CLI 随 SDK 一并安装
python src/experiments/preflight.py    # 须输出 PREFLIGHT PASSED
python src/experiments/exp3_agent.py --shuffle
python src/experiments/compare_exp3.py
python src/experiments/plot_exp3_thresholds.py --run agent
```

数据集本身不需要 API key：`step0_corpus.py` 与 `build_dataset.py` 不做模型调用，且
`build_dataset.py` 按记录的字符区间从磁盘重新切分每一行，文字不能逐字复现即拒绝写出。
重跑复现的是流程，模型答案会不同，差异幅度即第三节所述的未解决问题。

每个 session 留下 `output/llm_logs/exp3_agent/<cid>.trajectory.jsonl`，含每次工具调用、
思考块、CLI 版本与工作目录，本文各项说法可据此重新审计，无需重跑。
