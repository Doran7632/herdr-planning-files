# 按原 PLAN_ID 恢复

先从上一条输出找到完整 `plan_dir`。本地记录查看不访问 Herdr：

```bash
python3 "$SKILL_DIR/scripts/records.py" status --plan-dir "$PLAN_DIR"
```

真实恢复必须在原主管 pane 内执行：

```bash
python3 "$SKILL_DIR/scripts/create_framework.py" resume --plan-dir "$PLAN_DIR" --deadline 180
```

| 情况 | 行为 |
|---|---|
| worker 已就绪 | 重新核验身份，复用，不重启 |
| 只建了 pane，未提交启动 | 创建剩余 pane，并启动未提交者 |
| 启动返回超时/进程中断 | 只观察原 pane 与启动 receipt，不再次提交 |
| split 返回丢失 | 保留 pending_operation，禁止再 split |
| 新 worker 的 Claude 工作区信任提示 | 仅在目标目录、原进程与准确选项匹配时按用户授权选 Yes；记录 startup-trust.json，不重复确认 |
| 登录/其他审批/未知 UI | 保留窗口与错误，用户处理真实提示后再 resume；脚本不自动批准 |
| agent 退出、被替换或 session 改变 | 拒绝自动重绑；先核对现场，不向新占用者发旧任务 |
| 原规则或启动文件 hash 改变 | 拒绝静默加载；恢复文件或明确另建新框架，不伪称规则已热更新 |
| 主管换了会话 | 不是普通 resume；本版不支持自动移交主身份 |

## 不确定 split

`framework.json.pending_operation` 保留本次 split 前的 pane 列表、tab、anchor 和角色。
在真实 Herdr 通过 `herdr pane list --workspace "$HERDR_WORKSPACE_ID"` 核对实际新增 pane，
结合先前结果/终端内容确认它就是这次创建的空 shell。不要只凭编号或“恰好多一个”猜测。
核对后使用明确采用参数：

```bash
python3 "$SKILL_DIR/scripts/create_framework.py" resume --plan-dir "$PLAN_DIR" \
  --adopt-pane worker2=w1:p7 --deadline 180
```

脚本再检查该 pane 不在旧列表、同 tab、同 cwd 且没有 agent。示例 ID 不能直接照抄。
如无法确定对应关系，保留资源并报告；不要关闭或复用别人的窗口。

## 不确定启动

保存状态 `submitted_unknown` 后、实际调用前崩溃也会被保守视为“不确定”，可能需要人工核对。
本版不提供强制重发启动按钮。查 `workers/workerN.launch.json`、终端内容和进程证据。
不存在 receipt 不证明启动没有提交；不要靠删除状态字段跳过保护。

Claude 的 `workerN.startup-trust.json` 在发送按键前落盘。`confirmation_submitted` 只表示 Enter 已提交，
仍需后续 idle/done 和身份检查证明就绪。`selection_requested` / `confirmation_requested` 的不确定状态
不会在 resume 时盲目重放；也不要删除该文件来强行重试。若屏幕目录与项目不一致（例如误进入 home），
不会自动选择 Yes。Codex 更新通过每个新进程的配置覆盖跳过，不修改正在运行的旧会话。

## 文件与升级

从独立 peers 版本升级到一体版：本次用户确认旧测试框架无需保留兼容，因此独立 helper 地址停用，
不提供旧路径转发，也不改写旧 PLAN_ID 的记录或规则 hash。新建框架将使用包内 helper；
以下恢复流程适用于安装路径和通信入口仍有效的同一版框架。

role/common/hash 是本次启动的固定快照；修改磁盘文件不等于已有模型已读取新规则。
恢复不会改全局配置、pin 或共享 `.active_plan`，不会自动迁移旧 schema 1/2 框架。
同一框架只能有一个 launcher 写 `framework.json`；并发 resume 会被文件锁拒绝。
关闭/清理本次 pane、停用旧框架或切换版本应作为明确操作，不在恢复里隐式执行。
