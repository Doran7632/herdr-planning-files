# 原生注入与兼容边界

## 已依据的接口

| 宿主 | 注入 | 保留措施 |
|---|---|---|
| Codex | `--no-daemon -c developer_instructions=<TOML string>` | 不改 model/system 基础指令；项目 AGENTS 仍由原生加载；已有 developer layer 先核对合并 |
| Claude Code | `--append-system-prompt <text>` | 追加到默认提示；不使用替换 system prompt 参数 |
| OMP | `--append-system-prompt <text>` | 显式 append 会覆盖自动发现的 APPEND_SYSTEM；存在时先核对合并 |

这里的长 text 由 Python `os.execve` 作为参数传入，不经过终端规范输入长度限制。
终端仅接收不足 900 字节的 wrapper 命令。路径过长会停止，不能截断路径或规则。
不会新增危险审批跳过参数，也不会改变原权限模式。未登录或出现未知审批时如实报告。
用户已授权的启动例外：Codex worker 和新主管追加 `-c check_for_update_on_startup=false`，仅本进程跳过更新检查。
Claude 对新 worker 的目标目录信任提示自动选 Yes；只识别截图所示英文提示、准确目录及当前选择，
确认前重新核验界面和进程。不同版本/语言的未知界面不会猜测按键。

本机只读能力核对版本：Codex 0.169.0、Claude Code 2.1.327、OMP 18.20.3。
这些不是所有版本兼容性承诺。实际创建还会检查所需参数是否存在。

## 已有追加规则

若发现已有 developer_instructions 或 APPEND_SYSTEM，预检停止在创建 pane 之前。
先读取有效配置层，整理一份包含**全部原有效追加规则**的 UTF-8 文件，再针对对应 worker 传：

```bash
... create --workers 3 --main-agent codex \
  --base-instructions worker2=/absolute/path/reviewed-codex-base.txt \
  --base-instructions worker3=/absolute/path/reviewed-codex-base.txt
```

这份文件是已核对的原追加层；脚本再接上框架角色规则。不要复制 API key、完整 config 或其他秘密。
不要为了通过检查传空白文件。其他 agent 类型的 base 文件同样可显式提供。
自动配置检查是保守的，可能因未使用的 profile 而暂停；并不假称能解析全部托管/企业配置。
自定义包装器、远端配置或企业策略必须先确认有效指令来源，不能猜测优先级。

AGENTS.md、CLAUDE.md 等原生项目规则仍交给各 CLI；本框架不覆盖它们，不创建每角色假的共享 AGENTS.md。

## 已有主管与会话恢复

运行中的 main 无法靠新启动参数热注入。main 在当前轮读 context，任务切换/压缩后重读。
worker 在首次新进程启动时注入；独立角色文件用于恢复，不是每轮钩子。
Claude 等宿主可能把系统提示快照保存在会话里，恢复旧会话时改参数不一定生效。
因此本版只为新 worker 启动注入，不自动 resume 原生 CLI 历史会话，不宣称每轮强制约束。

## 本地版本与证据

- Herdr JSON schema：herdrdev/herdr v0.9.3 commit `7b116c05bfda646af39d2524c54e70c751f57ee8`。
- peers：DailybotHQ/herdr-peers v2.2.1 commit `6b9f17d8ebffe54ecde68c3bc7c768032be60e92`。
- 该固定版本已内置在 `vendor/herdr-peers/`，保留 MIT 许可证和来源 hash；仅上游 `--skill` 帮助文件路径作打包适配，不再需要独立 skill。
- 本版 Python 需要 3.11+ 和 Unix flock，不支持 Windows 原生终端。
- 真实 Herdr 环境不存在于本次开发会话；运行时 schema/PID 检测仍需验收。响应字段缺失会停止，不递归猜测 ID。

参考上游官方说明：OpenAI Codex config-reference / agents-md；Claude Code cli-reference；
can1357/oh-my-pi docs/system-prompt-customization.md；Herdr 及 peers 的以上固定版本源码。
