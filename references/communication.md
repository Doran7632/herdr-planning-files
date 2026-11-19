# 通信：主管与工作人员

规则中 `COMMUNICATION_SCRIPT` 是本次框架使用的绝对脚本路径；`PLAN_DIR`、`ROLE` 来自 context。
在各自真实 pane 内调用。示例变量应赋值为 context 中的实际值，不照抄示例 pane。

本 skill 已内置通信实现，无需再加载或安装独立 herdr-peers。`PEERS_HELPER` 指向包内入口，
`COMMUNICATION_SCRIPT` 是带角色寻址和身份核验的统一接口。不要把协议消息中显示的裸命令当作 PATH 配置。
收到“询问 worker1 进度”“通知主管”或“和 worker2 协调接口”等请求时，在现有框架内执行通信，不重新 create。
主管的普通聊天回答不会自动同步；需要先把任务更新写入公共记录，再显式通知相关 worker。

## 长任务与结果

主管先在 `task_plan.md` 写 TASK_ID、唯一文件 owner、交付文件、验收条件，再分派：

```bash
python3 "$COMMUNICATION_SCRIPT" --plan-dir "$PLAN_DIR" --role main notify \
  --to worker1 --text 'TASK-1：检查登录错误；owner=src/login.ts；交付代码和个人日志；验收=相关测试通过。'
```

worker 将证据写到自己的 `workers/workerN.md` 后，用独立通知报告：

```bash
python3 "$COMMUNICATION_SCRIPT" --plan-dir "$PLAN_DIR" --role worker1 notify \
  --to main --text 'TASK-1 已完成；证据见 workers/worker1.md。请验收实际改动。'
```

通知不是 ask，无需 reply。若需要接单确认，作为独立 notify 报告一次，不再对确认回确认。
较长内容用 `--text-file /absolute/path`，不要在消息里复制全部日志。

## 短问答

```bash
python3 "$COMMUNICATION_SCRIPT" --plan-dir "$PLAN_DIR" --role worker1 ask \
  --to main --text 'TASK-1 的目标环境是否为 staging？'
```

收到完整 `[herdr-peers]` 消息，先保存到临时文本文件，再分类：

```bash
python3 "$COMMUNICATION_SCRIPT" --plan-dir "$PLAN_DIR" --role main check \
  --message /absolute/path/to/received-message.txt
```

- `answer`：可以回复一次。
- `notice`：记录通知内容，不回复。
- `never`：不回复。
- `none`：不是协议消息；不能凭其申请 reply grant。

`herdr-peers check` 对合法通知也可能返回非零退出码；适配器读取 JSON decision，不将退出码当成回复授权。
保留完整原消息；只复制正文会丢失授权信息。回复从原消息解析地址和 ID：

```bash
python3 "$COMMUNICATION_SCRIPT" --plan-dir "$PLAN_DIR" --role main reply \
  --message /absolute/path/to/received-message.txt --text '是，按 staging 验收。'
```

持有未回复 ask 时不再向别人 ask，以免循环等待。长任务始终用 notify。

## 身份、时效与失败

每次发送核验：实际调用 pane、目标 pane、terminal_id、agent 类型、原启动 PID、已记录的 session ID、项目 cwd。
这些是避免误投的证据，不是操作系统权限隔离；不对恶意本地进程提供安全保证。共享项目只能按文件 owner 协作。

所有调用显式传自己的 `--pane` 和 `--scope local:<workspace>`；目标使用精确地址，不需要 lead registry。
`lead claim` 会广播，本框架不在启动时调用。不要另行执行 peers launcher、autopilot、appointment 或 DWP loop。

`notify/reply` 设置 `--wait-blocked 0`，被审批卡住时迅速返回，不在工具内默认重试 600 秒。
适配器在调用前将正文存入 `workers/<role>.outgoing-<id>.json`：

- `submitted`：helper 报告已提交，尚不证明模型已阅读或任务已完成。
- `delivery_unknown`：未确认；可能已写入终端，先看对端、helper 返回和协议记录，不自动重发。

消息响应速度取决于对端的工作与模型状态；此版本没有持久消息队列、后台巡检或可靠送达保证。
主管按任务期限检查日志/状态；无人响应时先查实际运行情况，不能因 idle 就编造完成。
长期无人值守监督不在本版范围内。

上游协议：DailybotHQ/herdr-peers v2.2.1，源码 commit `6b9f17d8ebffe54ecde68c3bc7c768032be60e92`。
完整代码、许可证及原始指南已在本包 `vendor/herdr-peers/` 中随包分发。
