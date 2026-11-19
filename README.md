# herdr-planning-files · 建队与通信一体版

在 Herdr 中保留一个主管，创建指定数量的 Claude Code、Codex 或 OMP worker，并在同一个 skill 内完成任务分派、进度回报和 pane 通信。角色通过原生启动规则注入，无需 Session Pin，也无需另装 herdr-peers。

只需对主管说：

> 使用 herdr-planning-files，保留当前主管，新建 Claude Code、OMP 各一个 worker。

建队后可以继续说“问问两个 worker 做到哪了”“先处理支付链路，通知他们调整方向”。主管需实际调用通信工具；普通聊天内容不会自动广播。

[完整使用剧本：迁移到达梦的协作演练](docs/dameng-simulation-guide.md) · [通信约定](references/communication.md) · [验证范围](VALIDATION.md)

## 实现的改动

- 保留现有主管；顺序创建恰好 N 个新 pane，按布局尺寸选方向，使用 `--no-focus`。
- worker 并行启动。Codex 使用 `--no-daemon` 与 developer instructions；Claude/OMP 使用追加系统规则参数。
- 启动选择按用户要求固化：Codex 本进程跳过更新检查；Claude 仅自动确认与目标目录匹配的工作区信任提示，按键前后核验并留审计记录。其他审批不自动接受，全局配置和权限模式不变。
- 短终端命令调用 Python wrapper，由 `execve` 传入完整规则，避免把多 KB 文本塞进终端输入。
- 规则、任务、个人日志与身份映射隔离在项目 `.planning/<PLAN_ID>/`，不改 `.active_plan`。
- 内置 DailybotHQ/herdr-peers 2.2.1 完整运行代码，使用显式地址通信；省去 lead 广播和启动自检模型回合。
- 保存不确定操作、规则 hash、进程/会话证据与每次真实阶段耗时；恢复时不盲目重建或重发。
- 不更改全局 agent 配置、权限设置或旧 pin；角色约定是协作约束，不是安全隔离。

## 目录

```text
SKILL.md                     agent 的短入口
scripts/create_framework.py  一次建队与原 PLAN_ID 恢复
scripts/agent_entry.py       验证 pane 后 exec 原生 CLI
scripts/launch.py            启动参数与已有规则冲突检测
scripts/records.py           PWF 记录、角色上下文与本地检查
scripts/communicate.py       角色寻址、身份核验、发送记录、ask/reply/notify/check
vendor/herdr-peers/         内置通信运行代码、MIT 许可证、原始指南和来源 hash
THIRD_PARTY_NOTICES.md      固定上游版本与本地打包变更说明
references/                 使用边界、恢复、通信与真实验收
tests/                     Python 标准库离线测试
evals/evals.json            尚待真实 Herdr 执行的技能验收用例
```

## 直接试用源码

在**真实 Herdr 中的主管会话、当前业务项目目录**加载此目录的 `SKILL.md`，随后按入口执行。
也可以由该会话运行：

```bash
SKILL_DIR="$HOME/.agents/skills/herdr-planning-files"
python3 "$SKILL_DIR/scripts/create_framework.py" create \
  --workers 3 --main-agent codex --project "$PWD" --deadline 180
```

`--main-agent` 必须匹配实际主管宿主。脚本输出后，主管需在当前轮读取 main context。
无需安装另一个 herdr-peers skill。包内通信代码缺失或不可执行时，在分 pane 前停止。

## 安装

首次安装，先把完整仓库克隆到共享池，再建立 Codex 和 Claude Code 的发现链接：

```bash
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/Doran7632/herdr-planning-files.git \
  "$HOME/.agents/skills/herdr-planning-files"
mkdir -p "$HOME/.codex/skills" "$HOME/.claude/skills"
ln -s "$HOME/.agents/skills/herdr-planning-files" "$HOME/.codex/skills/herdr-planning-files"
ln -s "$HOME/.agents/skills/herdr-planning-files" "$HOME/.claude/skills/herdr-planning-files"
```

以上针对尚无同名目录或链接的首次安装。已有安装时先检查位置和内容，不覆盖已有目录，不使用强制链接。禁用旧版时移入 `~/.agents/disabled-skills/`；保留整个 `vendor/` 目录及可执行权限。

重新打开 agent 会话以刷新技能发现。OMP 的技能发现方式以其当前版本配置为准；本包能启动 OMP worker，不代表 OMP 必然自动发现 Codex/Claude 的技能目录。

## 依赖与安装位置

Python 3.11+（使用 tomllib）、Unix/macOS（使用 flock）、Herdr、所选 agent CLI。
herdr-peers 已内置；不从共享池、宿主技能目录或 PATH 搜索外部通信组件，也不再接受 `--peer-bin`。
适配器按 Herdr 0.9.3 源码和当前本机 CLI 能力开发；其他版本在真实验收前不宣称兼容。

日后安装应先放入 `~/.agents/skills/` 共享池：

- 此源码的内容对应 `herdr-planning-files/`。
- 保留本包的完整 `vendor/herdr-peers/`；仅根目录有可发现的 `SKILL.md`。
- Codex 与 Claude 发现入口应分别在 `~/.codex/skills/`、`~/.claude/skills/` 指向共享副本。
- 现有条目先检查，不用 `ln -sf` 或无备份覆盖。禁用旧 skill 时移入 `~/.agents/disabled-skills/`。
- 原独立 herdr-peers 移入共享停用池，并移除 Codex/Claude 的独立发现链接。
- 此版本不保留旧 helper 路径或迁移旧记录；从旧版升级后新建框架。现有 pane 不自动关闭。

## 一句话使用

- 建队：“使用 herdr-planning-files，保留当前主管，新建 Claude、OMP 各一个 worker。”
- 查询进度：“通过框架通信询问 worker1 当前进度与阻塞点，收到答复后汇总。”
- 调整任务：“更新任务计划，并通知相关 worker 读取最新方向。”
- worker 汇报：“先写个人进度日志，再通知主管当前结果。”

由当前 pane 的 agent 调用 `scripts/communicate.py`。已有框架按其 `PLAN_DIR`、`ROLE` 寻址，
不重新建队；普通聊天输出不会自动广播。命令用法见 [通信约定](references/communication.md)。

## 测试

从此目录运行：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
```

离线测试包含真实进程 `execve` 参数传递和模拟 Herdr CLI 边界；不会启动计费模型会话。
它们不等同于真实 Herdr 的兼容性、通信及时性或性能验收。

## 速度预期

旧流程的逐个 pin 管理、主管交接及额外模型回合从热路径移除。理论关键路径变为：
预检 + 分 pane 的总耗时 + 最慢 worker 的启动时间 + 核验，而不是所有 worker 启动耗时之和。

默认 180 秒为启动脚本的截止预算；登录、审批或检测迟滞可能导致未完成。
完整“一句话到可工作”耗时还需包含主管读取 skill/context 的时间，不能仅用脚本时长宣称 2–3 分钟达标。
详见 [真实验收](references/acceptance.md)。

## 新建独立测试 workspace

见 [一键 workspace 测试](references/workspace-test.md)。入口为 `scripts/start_workspace.py start`；从真实 Herdr shell 运行即可启动新主管、建队和通知验收。

## License

本项目使用 [MIT License](LICENSE)。内置的 DailybotHQ/herdr-peers 保留其原始 MIT 许可证和版权声明，来源与打包变更见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
