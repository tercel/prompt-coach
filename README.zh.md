# prompt-coach

[English](README.md) | **中文**

一个面向 **Claude Code 和 Codex** 的 `UserPromptSubmit` 插件，从两个维度对每条 prompt 进行辅导：

1. **Prompt 质量** — 将你的 prompt 改写为更清晰的编程指令，并附上一条教学提示。
2. **目标语言** — 纠正你在练习语言中的写作，或将母语 prompt 翻译成目标语言，每条都附有简短说明。

两个平台共用同一个分析核心、Hook 配置和环境变量。每个功能都可以通过 `/prompt-coach:*` 命令实时开关。输入规范或后端失败时静默通过，不影响正常工作流。

## 支持的平台与后端

| Hook 平台 | 首选 CLI | API 备选 |
|---|---|---|
| Claude Code | `claude -p` | Anthropic SDK，需 `ANTHROPIC_API_KEY` |
| Codex | `codex exec` | OpenAI SDK，需 `OPENAI_API_KEY` |

默认值为 `COACH_BACKEND=auto` 和 `COACH_PLATFORM=auto`。在 Hook 运行时，插件通过插件根目录环境变量自动检测平台：

- `CLAUDE_PLUGIN_ROOT` 选择 Claude Code。
- `PLUGIN_ROOT` 选择 Codex。
- 独立运行 `--dry-run` 时默认 Codex；设置 `COACH_PLATFORM=claude` 可测试 Claude 路径。

插件优先使用所选平台的 CLI；如果失败且对应 API Key 可用，则回退到该平台的 API。

## 安装

### Claude Code

Claude 插件入口为 `.claude-plugin/plugin.json`：

```text
/plugin marketplace add /absolute/path/to/prompt-coach
/plugin install prompt-coach
```

安装后重启 Claude Code。

#### Claude 桌面应用（Code 模式）

插件和 Hook 在桌面应用的 Code 模式中也能运行，不限于终端 CLI。需要注意的是 Hook 子进程的环境变量：桌面应用是 GUI 进程，其 `PATH` 通常是精简版本，**不包含** `claude` 的安装路径（如 `~/.local/bin`）。如果找不到 `claude`，CLI 后端将不可用，Hook 会静默跳过（除非设置了 API Key）。

为了在任何界面下都能稳定运行，请在 `~/.claude/settings.json` 中添加 `env` 块 —— Claude Code 会将其注入会话和 Hook 子进程，不受 GUI 的 `PATH` 影响：

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "sk-ant-...",
    "COACH_CLAUDE_BIN": "/Users/you/.local/bin/claude"
  }
}
```

- `COACH_CLAUDE_BIN`（`which claude` 的绝对路径）让零成本 CLI 后端保持可用 —— 复用你的 Claude 认证，不消耗 API 额度。
- `ANTHROPIC_API_KEY` 是备选方案，即便找不到 CLI 二进制文件，Hook 也能继续运行。两者都设置可实现 CLI 优先、API 兜底。

在终端 CLI 中，shell 的 `PATH` 已经暴露了 `claude`，因此两者都不是必需的。

### Codex

Codex 插件入口为 `.codex-plugin/plugin.json`，其中 `"hooks": "./hooks/hooks.json"` 字段指向 Hook。将此仓库添加到已配置的 Codex marketplace，安装 `prompt-coach`，重启 Codex，然后通过 `/hooks` 查看并信任内置 Hook。

两个平台共用同一个 `hooks/hooks.json` 和 `scripts/coach.py`：Claude Code 通过 `.claude-plugin/plugin.json` 发现 Hook，Codex 通过 `.codex-plugin/plugin.json` 中的 `hooks` 字段发现。

### 插件安装 vs 手动配置

**优先使用上述插件安装方式。** 平台自动检测依赖于此：`COACH_PLATFORM=auto` 会读取 `CLAUDE_PLUGIN_ROOT` / `PLUGIN_ROOT`，而这两个变量**只有**插件系统才会设置。通过插件安装后，Claude Code 和 Codex 各自检测自己的平台并自动选择正确的后端，共享同一份仓库，享受 Codex 的 `/hooks` 信任流程，并在终端 CLI 和桌面应用之间保持一致。

仅在主动编辑 `scripts/coach.py` 且希望不重装即可生效时，才在 `~/.claude/settings.json` 中手动配置 Hook。此时需将 `command` 指向工作副本的绝对路径，**并显式设置 `COACH_PLATFORM=claude`**，因为手动 Hook 没有 `CLAUDE_PLUGIN_ROOT`，否则检测会回退到 Codex：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/prompt-coach/scripts/coach.py",
            "timeout": 30
          }
        ]
      }
    ]
  },
  "env": { "COACH_PLATFORM": "claude" }
}
```

## 配置

| 变量 | 默认值 | 含义 |
|---|---|---|
| `COACH_PLATFORM` | `auto` | `auto`、`claude` 或 `codex`。 |
| `COACH_BACKEND` | `auto` | `auto`、`cli`、`api`、`ollama`、`claude`、`anthropic`、`codex` 或 `openai`。可通过 `/prompt-coach:backend` 持久设置。 |
| `COACH_CLAUDE_BIN` | PATH 查找 | Claude CLI 的显式路径。 |
| `COACH_CODEX_BIN` | PATH 查找 | Codex CLI 的显式路径。 |
| `ANTHROPIC_API_KEY` | 未设置 | 启用 Anthropic API 备选。 |
| `OPENAI_API_KEY` | 未设置 | 启用 OpenAI API 备选。 |
| `COACH_MODEL` | 未设置 | 覆盖所有后端的模型。 |
| `COACH_ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Claude CLI 和 Anthropic API 使用的模型。 |
| `COACH_CLI_MODEL` | agent 默认值 | Codex CLI 模型。 |
| `COACH_API_MODEL` | `gpt-4o-mini` | OpenAI API 模型。 |
| `COACH_OLLAMA_HOST` | `http://localhost:11434` | Ollama 服务器基础 URL（`ollama` 后端）。 |
| `COACH_OLLAMA_MODEL` | `llama3.1` | Ollama 模型（`ollama` 后端）。推荐高质量 instruct 模型，如 `qwen2.5-coder:32b-instruct-q4_K_M`。 |
| `COACH_OLLAMA_KEEP_ALIVE` | `30m` | Ollama 在两次调用之间保持模型驻留的时长（避免断续使用时的冷启动延迟）。 |
| `COACH_TARGET_LANG` | `English` | 正在练习的语言。 |
| `COACH_NATIVE_LANG` | locale 检测 | 用于说明的语言。 |
| `COACH_LEVEL` | `Advanced` | 反馈深度。 |
| `COACH_EVALUATE` | `off` | Prompt 质量辅导开关。可通过 `/prompt-coach:enable\|disable evaluate` 实时覆盖。 |
| `COACH_CORRECT` | `off` | 目标语言纠错开关。可通过 `/prompt-coach:enable\|disable correct` 实时覆盖。 |
| `COACH_TRANSLATE` | `off` | 母语→目标语言翻译开关。可通过 `/prompt-coach:enable\|disable translate` 实时覆盖。 |
| `COACH_STATE_SCOPE` | `project` | `project` 或 `global` — `/prompt-coach:*` 切换的生效范围。 |
| `COACH_STATE_DIR` | `~/.config/prompt-coach` | 运行时状态文件所在目录。 |
| `COACH_CLI_FLAGS` | 未设置 | 传给 `codex exec` 的额外标志（Codex CLI 后端）。 |
| `COACH_MODE` | `annotate` | `annotate` 或 `block`。 |
| `COACH_MIN_PROMPT_CHARS` | `6` | 超短多词 prompt 的字符下限（见下方过滤说明）。 |
| `COACH_CONTEXT_MESSAGES` | `6` | 用作上下文的近期对话轮次数。 |
| `COACH_CONTEXT_CHARS` | `2000` | 最大渲染上下文字符数。 |
| `COACH_TIMEOUT` | `60` | 后端超时秒数。嵌套 CLI 后端可能耗时 15–25s+；设置过低会导致辅导请求超时被静默丢弃。 |
| `COACH_DISABLE` | 未设置 | 设置为真值可禁用。 |
| `COACH_DEBUG` | 未设置 | 设置为真值可打印错误。 |

`cli` 和 `api` 是平台感知的别名。显式后端名称可绕过平台检测：

```bash
COACH_BACKEND=claude    # 强制使用 Claude CLI
COACH_BACKEND=anthropic # 强制使用 Anthropic API
COACH_BACKEND=codex     # 强制使用 Codex CLI
COACH_BACKEND=openai    # 强制使用 OpenAI API
COACH_BACKEND=ollama    # 强制使用本地 Ollama 服务器（COACH_OLLAMA_HOST / COACH_OLLAMA_MODEL）
```

若 `COACH_NATIVE_LANG` 与 `COACH_TARGET_LANG` 相同，则只运行 prompt 质量辅导。

### 依赖说明（仅 API 后端需要额外安装）

默认路径**仅依赖标准库** —— CLI 后端通过 `subprocess` 调用外部命令，`ollama` 后端使用 `urllib`，无需 `pip install`。

直接 HTTP **API** 后端（`COACH_BACKEND=api | openai | anthropic`）需要可选 SDK，延迟导入，列于 [`requirements.txt`](requirements.txt)：

```bash
pip install anthropic   # Claude / Anthropic API 后端
pip install openai      # Codex / OpenAI API 后端
```

若选择了 API 后端但未安装对应 SDK，`/prompt-coach:status` 会标记缺失的包，而非静默失败。

## 哪些 prompt 会被辅导

在任何模型调用之前，会先运行一个廉价的确定性预过滤器。它只跳过明确不值得辅导的输入，因此短但模糊的 prompt 仍会被捕获：

| 跳过（无模型调用） | 辅导 |
|---|---|
| 斜杠命令、`!shell` | `fix bug`、`review code`、`add tests` |
| 裸答案 / 流程控制：`yes`、`ok`、`1`、`continue` | `make it better`、`optimize it` |
| 开发命令行：`git push`、`npm install`、`cargo test` | `go implement the login feature` |
| 上下文丰富的短语：`build it`、`run tests`、`do it` | 任何 ≥2 词的自然语言请求 |
| 单 token：`refactor`、`optimize` | |

通过过滤器的内容会发送给模型，模型会读取近期对话记录，对上下文已明确的追问保持静默。`make`/`go` 被视为英语单词而非 CLI 命令，因此 `make it better` 会被辅导。

CJK（中文/日文/韩文）输入按字符数而非词数判断 —— 这些文字没有空格，因此整句话如 `优化这段代码的性能` 会被辅导，而不会被误认为单 token（只有 `好` 这样的单字符回复才会被跳过）。

## 命令

Claude Code 将插件命令命名为 `/<plugin>:<command>`，每个操作都是独立的命令（在 `/` 菜单中输入动词可模糊匹配）。它们在运行时生效，无需重启 —— 下一条 prompt 即可生效：

| 命令 | 效果 |
|---|---|
| `/prompt-coach:power on` · `… off` | 整个 Hook 开关（功能状态保留） |
| `/prompt-coach:enable <功能…>` | 开启一个或多个功能 |
| `/prompt-coach:disable <功能…>` | 关闭一个或多个功能 |
| `/prompt-coach:lang native <X> target <Y>` | 设置母语 / 练习语言（名称或代码，如 `native zh target en`） |
| `/prompt-coach:backend <auto\|cli\|api\|ollama> [model]` | 选择分析引擎（auto=CLI 默认；api/ollama 更快更可靠）。使用 `ollama` 时，传入已拉取的模型，如 `backend ollama qwen2.5-coder:32b-instruct-q4_K_M` —— 会持久保存，无需手动修改全局配置 |
| `/prompt-coach:status` | 显示当前状态（每个功能、范围、状态文件路径） |
| `/prompt-coach:help [en\|zh]` | 显示命令用法（英文或中文） |

**功能**（可使用全名或单字母 `e` · `c` · `t`）：
`evaluate`（prompt 质量辅导）· `correct`（纠正*目标语言*写作）· `translate`（将*母语*输入渲染为目标语言）。
因此 `/prompt-coach:enable c t` 等同于 `… enable correct translate`。`/prompt-coach:help zh` 以中文显示用法（默认 `en`）。通过 `/prompt-coach:lang native zh target en` 设置语言（全名或代码；持久保存，覆盖 `COACH_NATIVE_LANG` / `COACH_TARGET_LANG`）。

可以一次传入多个：`/prompt-coach:enable correct translate`（= auto：纠正目标语言写作，翻译母语输入）；`/prompt-coach:disable correct translate` 关闭所有语言辅导。分隔符灵活 —— 空格、逗号或连字符均可（`disable correct,translate`）。

**默认全部关闭，需手动开启：** 全新安装不执行任何操作（当所有功能关闭时，Hook 在任何模型调用之前退出）。按需开启：中文母语者练习英语可运行 `/prompt-coach:enable correct translate`（纠正英语写作，翻译中文输入），或仅 `/prompt-coach:enable evaluate` 获取 prompt 质量提示。通过 `.claude/settings.local.json`（Claude）或 `.codex/config.toml`（Codex）按项目设置，让每个项目独立选择加入。

### Codex

Codex 从 `$CODEX_HOME/prompts/` 读取自定义 prompt（平坦命名空间），且 prompt 运行时没有 `PLUGIN_ROOT`，因此这些命令不能像 Claude Hook 那样通过软链接使用。一次性生成即可（会将 coach.py 的绝对路径写入）：

```bash
bash scripts/install-codex-prompts.sh   # 写入 ~/.codex/prompts/prompt-coach-*.md
```

然后在 Codex 中使用相同动词，加 `$` 前缀并以连字符命名空间：

| Claude | Codex |
|---|---|
| `/prompt-coach:power on` | `$prompt-coach-power on` |
| `/prompt-coach:enable correct translate` | `$prompt-coach-enable correct translate` |
| `/prompt-coach:disable evaluate` | `$prompt-coach-disable evaluate` |
| `/prompt-coach:status` | `$prompt-coach-status` |
| `/prompt-coach:help` | `$prompt-coach-help` |

移动仓库后需重新运行脚本。（相同的 Codex 格式 —— YAML 前置元数据 + `$ARGUMENTS` —— 是这些命令能工作的原因；`agent-skill-bundler` 只转换*技能*，不转换 Hook 命令，因此与此无关。）

每次切换都会写入 `~/.config/prompt-coach/` 下的小型状态文件（`state.json`，或在项目范围下的 `state.<projecthash>.json`；可通过 `COACH_STATE_DIR` 覆盖目录；不在项目内部），Hook 在每条 prompt 时读取此文件；它覆盖 `COACH_EVALUATE` / `COACH_CORRECT` / `COACH_TRANSLATE` / `COACH_DISABLE` 的环境变量默认值。路径被有意固定在 home 位置，**而非** `CLAUDE_PLUGIN_DATA` / `PLUGIN_DATA` —— 那些变量在 Hook 运行时设置，但控制命令子进程中没有，因此以它们为键会导致命令和 Hook 读取不同的文件（切换会静默无效）。

### 配置的存储位置：全局配置 vs 按项目状态

prompt-coach 在 `~/.config/prompt-coach/` 下保存两类文件（可通过 `COACH_STATE_DIR` 覆盖目录）：

| 文件 | 内容 | 写入方 | 范围 |
|---|---|---|---|
| `config.json` | 后端、Ollama 模型/host/keep-alive、母语/目标语言 —— **在 `global` scope 下还包含功能开关** | `/prompt-coach:backend`、`/prompt-coach:lang`；`/prompt-coach:enable`/`disable`/`power`（global scope） | **全局，跨平台** —— Claude 和 Codex 共同读取的唯一文件 |
| `state.<project>.<hash>.json` | `evaluate` / `correct` / `translate` 开关，power | `/prompt-coach:enable` / `disable` / `power`（project scope） | **按项目**（默认 scope） |

只有**一个全局文件**（`config.json`），没有两个：在 `global` scope 下，功能开关与后端/语言配置共同存储在 `config.json` 中，不存在独立的 `state.json`。只有 **project** scope 才会创建按项目的 `state.<project>.<hash>.json` —— 因为单个全局文件无法存储各项目的独立开关。

为什么用独立的 `config.json` 而非宿主设置：`~/.claude/settings.json` 只有 Claude 能读，`~/.codex/config.toml` 只有 Codex 能读 —— 两者都不是共享的。prompt-coach home 目录下的文件是两个 Hook 都能读取的唯一位置，因此你的后端和语言设置只需配置一次即可在任何地方生效。

解析优先级：按项目 state `>` `config.json` `>` 环境变量（`COACH_*`）`>` 内置默认值。功能开关存储在按项目 state 文件中（project scope）**或 `config.json` 中（global scope）**—— 不跨 scope 继承。

#### 文件模板与 schema

两类文件都由 `/prompt-coach:*` 命令按需创建 —— **无需手动创建**，全新安装没有任何文件时使用内置默认值（全部关闭）。仓库附带 [`config.example.json`](config.example.json)（全局 `config.json` 的模板）和 [`state.project.example.json`](state.project.example.json)（按项目 `state.<project>.<hash>.json` 的模板）供参考；如需手动编辑，复制模板（JSON 没有注释，键名文档见下表）：

```bash
mkdir -p ~/.config/prompt-coach
cp config.example.json ~/.config/prompt-coach/config.json   # 然后编辑
```

`config.json` —— 唯一的全局跨平台文件（所有键均可选；省略则使用默认值）：

| 键 | 值 | 默认值 |
|---|---|---|
| `backend` | `auto` · `cli` · `api` · `ollama` · `codex` · `openai` · `claude` · `anthropic` | `auto` |
| `ollama_model` | 任何已拉取的 Ollama 模型标签 | `llama3.1` |
| `ollama_host` | Ollama 基础 URL | `http://localhost:11434` |
| `ollama_keep_alive` | Ollama keep-alive 时长（如 `30m`、`2h`、`-1` 表示永久） | `30m` |
| `native` | 你的母语（名称或代码） | locale 检测 |
| `target` | 你正在练习的语言 | `English` |
| `enabled` / `evaluate` / `correct` / `translate` | **仅限 global scope** —— 当 `COACH_STATE_SCOPE=global` 时，以下功能开关 | 见 state 表 |

`state.<project>.<hash>.json` —— 按项目功能开关（文件名自动生成；`project` 自动写入，方便 `cat` 时知道属于哪个路径）。仅在默认 **project** scope 下创建：

| 键 | 值 | 默认值 |
|---|---|---|
| `enabled` | `true` / `false` —— 主电源开关 | `true`（缺失 ⇒ 开启） |
| `evaluate` | `true` / `false` —— prompt 质量辅导 | `false` |
| `correct` | `true` / `false` —— 纠正目标语言写作 | `false` |
| `translate` | `true` / `false` —— 将母语输入渲染为目标语言 | `false` |
| `project` | 项目绝对路径（自动写入） | — |

### 切换范围（`COACH_STATE_SCOPE`）

`/prompt-coach:*` **功能**切换的生效范围（后端/语言始终是全局的，见上文）：

| Scope | 行为 |
|---|---|
| `project` *(默认)* | 按 `CLAUDE_PROJECT_DIR` 隔离 —— "在项目 A 中开启翻译"不影响项目 B。当无法解析项目目录时，回退到共享文件。 |
| `global` | 一个共享开关 —— 切换影响所有会话和项目。 |

**不提供按会话的 scope。** 平台仅在 Hook 的 stdin payload 中暴露 `session_id`，而非作为环境变量，因此 `/prompt-coach:*` 命令（普通子进程）无法知道当前处于哪个会话。`project` 是最细粒度的可靠选项；`/prompt-coach:status` 会打印当前 scope 和精确的 state 文件路径。

### 按项目选择加入（推荐）

由于**默认全部关闭**，最简洁的设置是只在需要的项目中开启辅导。

对于 Claude Code，使用该项目的 `.claude/settings.local.json`（个人文件，已 gitignore）：

```json
{
  "env": {
    "COACH_NATIVE_LANG": "Chinese",
    "COACH_TARGET_LANG": "English",
    "COACH_CORRECT": "on",
    "COACH_TRANSLATE": "on"
  }
}
```

对于 Codex，在受信任的项目中使用 `.codex/config.toml`：

```toml
[shell_environment_policy]
set = {
  COACH_NATIVE_LANG = "Chinese",
  COACH_TARGET_LANG = "English",
  COACH_CORRECT = "on",
  COACH_TRANSLATE = "on",
}
```

- 项目设置**覆盖全局默认值**，因此即使没有全局配置也有效 —— 项目设置所需内容，内置默认值保持关闭。
- Codex 只在项目受信任后才加载本地 `.codex/config.toml`。本地 Codex 配置也可以定义 Hook，但如果插件 Hook 已安装，不要在那里再添加第二个 prompt-coach Hook。
- 没有这些文件的项目不会得到任何辅导（默认关闭），Hook 在任何模型调用之前退出，零成本。
- 这种 env/config 方式本身就是按项目的，因此这里不需要 `COACH_STATE_SCOPE` 或 `/prompt-coach:*` 命令 —— 当你想在会话中**实时**切换功能时才使用命令。

可选的全局 `~/.claude/settings.json` `env` —— 在其中放置机器级默认值（如 `COACH_NATIVE_LANG`），任何项目仍可覆盖。Codex 的机器级默认值可以放在 `~/.codex/config.toml` 的 `[shell_environment_policy].set` 下。

## 输出模式

- **`annotate`**：将辅导内容作为额外的开发者上下文注入，然后回答改进后的请求。
- **`block`**：以退出码 2 拒绝 prompt，要求重新提交。

## 本地试用

```bash
# 在 Hook 外部，auto 默认使用 Codex。
python3 scripts/coach.py --dry-run "i want fix login bug when token expire"

# 测试 Claude 自动选择。
COACH_PLATFORM=claude python3 scripts/coach.py --dry-run "review this prompt"

# 强制指定后端。
COACH_BACKEND=openai python3 scripts/coach.py --dry-run "review this prompt"
COACH_BACKEND=anthropic python3 scripts/coach.py --dry-run "review this prompt"

# 翻译模式：用母语写作，获取目标语言版本。
COACH_CORRECT=off COACH_TRANSLATE=on COACH_NATIVE_LANG=Chinese \
  python3 scripts/coach.py --dry-run "帮我修复登录时 token 过期的 bug"
```

## 开发与测试

```bash
python3 tests/test_coach.py -v
```

单元测试不调用外部模型。

## 已知限制

- 辅导在 prompt 提交后运行，而非输入时。
- Annotate 模式依赖当前 agent 遵循注入的显示指令。
- 每条非平凡 prompt 都会产生一次额外的模型调用。
- 对话记录解析是尽力而为，因为 agent 对话记录格式不是稳定的公开 API。
