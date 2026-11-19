---
name: herdr-planning-files
description: >-
  在 Herdr 当前 workspace 一句话保留主管并新增 N 个 worker，建立独立 Planning with Files
  记录、角色与文件归属，内置 DailybotHQ/herdr-peers 通信，无需另装通信 skill。
  支持 Codex、Claude Code、OMP；用原生启动规则代替 Session Pin。用于快速建队、恢复，
  或已有框架内分派任务、询问进度与 pane 间通信。只维护源码时不启动框架。需要真实 HERDR_ENV=1。
---

# Herdr Planning Files — native roles + bundled communication

新建时，当前接收建队请求的会话就是 main（主管）。保留它，新建恰好 N 个 worker，N 不含 main。
用户要求建队已经授权本地记录、分 pane 和启动所选 agent；参数清楚时直接执行。
初始化后等待业务目标，不自行开发业务、不启动自动审批或巡检循环。

已有框架的通信、进度询问与任务调整：沿用当前上下文的 `PLAN_DIR`、`ROLE` 和成员映射，
直接读 [通信约定](references/communication.md)，使用本包 `communicate.py`，不要再次建队。
worker 加载本 skill 不会变成主管。缺失上下文时先核对本 pane 对应的框架，不能猜“最新计划”。

## 参数

- 未指定 worker 类型：worker1 用 Claude Code，其余 Codex。
- 部分指定：保留显式选择；尚无 Claude Code 时给最小空位，其余空位 Codex。
- 全部指定时不强行加入 Claude。`oh my pi` / `oh-my-pi` 归一为 `omp`；`pi` 不等于 `omp`。
- 数量、编号冲突或对应关系不明时才澄清。main 类型使用当前实际宿主，不能猜成 worker 默认值。
- 保持当前项目 cwd、tab、workspace 和用户焦点。不要为建队另建 workspace/worktree。

## 执行

先检查 `HERDR_ENV=1`。不成立时不调用 Herdr 控制接口，不伪造环境；说明需要在真实 Herdr pane 运行。
维护源码与离线测试不需要进入 Herdr。

`SKILL_DIR` 为本 SKILL.md 所在的实际目录。外部运行环境需要 Python 3.11+、Herdr 和所选原生 agent CLI。
通信运行代码已固定内置在 `vendor/herdr-peers/`（2.2.1），不再发现或加载独立 herdr-peers skill。
创建热路径不下载、不安装、不改全局配置。

例如当前主管为 Codex，新建 3 个 worker：

```bash
python3 "$SKILL_DIR/scripts/create_framework.py" create \
  --workers 3 --main-agent codex --project "$PWD" --deadline 180
```

指定 worker 时追加 `--agent worker1=omp --agent worker2=codex` 等。
通信入口由本包定位，不需要 `--peer-bin` 或额外 PATH 设置。
检测到已有 Codex developer instructions / OMP APPEND_SYSTEM 时会在分 pane 前停止；
按 [兼容性](references/compatibility.md) 合并经核对的原规则，不可用空文件绕过检查。

脚本一次完成预检、独立记录、顺序分 pane、并行原生启动与就绪验证。让脚本执行；
不要再逐个 prompt worker、设置 pin、广播 lead claim、手动重复处理弹窗或运行全套启动自查。

用户已指定默认启动选择：Codex 在本次进程用 `-c check_for_update_on_startup=false` 跳过启动更新检查，
不升级、不写全局配置；新 workspace 的 Codex 主管同样适用。Claude 仅对新启动 worker 的
“Accessing workspace / Yes, I trust this folder”提示自动选择 Yes，且屏幕目录必须等于本次项目目录。
启动器核验原进程、重新读取选项后才发送 Enter，并保存 `workers/workerN.startup-trust.json`。
其他权限/登录/未知提示保持人工处理；不新增跳过权限的参数，不对任意 Yes 提示自动确认。

**脚本输出成功后，main 必须在当前轮读取输出的完整 main context 并采用它。**
这不是修改已有会话的系统提示，也不保证每轮自动重注入。压缩/恢复或更换任务时重读：

```bash
python3 "$SKILL_DIR/scripts/records.py" context --plan-dir "$PLAN_DIR" --role main
```

然后向用户报告 PLAN_ID、各 worker 的类型/pane、实际耗时和未验证项。无业务目标则等待。

## 通信与分工

实际分派前读 [通信约定](references/communication.md)。优先使用 `communicate.py`：
按角色选对端，核验当前会话身份，显式指定 pane/scope，保存发送记录，再调用内置通信实现。
协议消息中即使显示裸命令 `herdr-peers reply`，仍按本次 context 的 `COMMUNICATION_SCRIPT`
处理完整消息；不要另找 skill、安装命令或丢弃 PLAN_ID。`PEERS_HELPER` 是包内入口的绝对路径。

主管唯一维护公共计划与文件 owner；worker 只写分配文件和个人日志。长任务用 notify，
短问题用 ask/reply。收到协议消息先 check，只有 `answer` 能回复；notice 不回复。
持有未答 ask 时不再 ask。结果和阻塞先落盘，再独立 notify 主管。

本框架直接寻址，不创建 peers lead record，不用 `lead` 别名，也不在初始化时发送通信演练。
“已提交”不等于“已处理”；blocked、超时和断连按发送记录核验，不能盲目重复投递。

## 恢复

使用原 plan 目录，不再 create：

```bash
python3 "$SKILL_DIR/scripts/create_framework.py" resume --plan-dir "$PLAN_DIR" --deadline 180
```

它核验主身份和规则 hash，复用原 pane；已提交的启动只观察，不再次输入命令。
不确定的 split、登录/审批、规则改变、会话被替换按 [恢复说明](references/recovery.md) 处理。
除上述已授权且目录匹配的启动信任提示外，不得接受其他审批、重启已有 main、删除未知 pane
或自动迁移旧 pin 方案来让检查通过。启动信任按键响应不明时不重复发送，先核对现场。

## 完成定义

`ready` 仅表示：角色文件就位、N 个原生 CLI 的类型/工作目录/进程证据与记录一致且可交互。
它不证明模型已遵守全部规则，也不证明真实问答已跑通。
1+3 在 180 秒内是性能目标；通过真实 [验收步骤](references/acceptance.md) 前，不得声称达标。

## 用户明确要求创建新 workspace

使用 [一键 workspace 测试](references/workspace-test.md) 的 `start_workspace.py start`。此入口在新 workspace 启动 Codex 主管，再调用上面的 create 流程。不要在仅要求保留当前主管时擅自使用它。

## 内置组件

上游完整运行代码和参考资料保存在 `vendor/herdr-peers/`，MIT 许可证与固定版本记录见
[第三方说明](THIRD_PARTY_NOTICES.md)。日常使用只需本入口和通信约定。
原始 [上游指南](vendor/herdr-peers/UPSTREAM_GUIDE.md) 按需查阅；其中独立安装说明不适用于本包。
内置代码不代表自动启用上游 autopilot、自动审批、后台巡检或主管继任。
