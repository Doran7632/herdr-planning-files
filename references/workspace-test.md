# 一键创建 workspace 并验证

本入口由用户在真实 Herdr pane 内执行，不从外部伪造 Herdr 上下文。
它创建独立 workspace，启动一个 Codex 主管，然后由主管运行已有的建队入口。
现有 workspace、旧版安装和其他 agent 不做修改。

## 命令

```bash
python3 "$SKILL_DIR/scripts/start_workspace.py" start \
  --project /absolute/existing/test/project --workers 3 \
  --agent worker3=omp --label 'Herdr 无 Pin 实测'
```

默认 worker1=Claude Code、worker2=Codex；上例把 worker3 指定为 OMP。
如果 Codex 有已有 developer instructions，给对应 worker 添加经核对的
`--base-instructions worker2=/absolute/path/to/reviewed-codex-base.txt`。

如果已有主管追加规则，先按兼容性说明核对并准备规则文件，再运行入口。
Codex 启动更新检查默认仅在本进程跳过。用户已授权 Claude 对本次目标目录的准确工作区信任提示选择 Yes；
目录或界面不匹配、登录及其他审批仍交用户处理。入口不会关闭 hook 或改变权限模式。

## 执行阶段

1. 真实上下文、CLI 能力与追加规则预检；有冲突时不创建 workspace。
2. `workspace create` 后立即保存返回的 workspace/pane ID；短 wrapper 启动 Codex 主管。
3. 主管执行 build，保留完整 `framework-start.log`；输出 ready 后读取 main context，再记录 adopt。
4. 分派三个只读测试：worker 报告既有角色、pane、主管和 PLAN_ID，写自己的 smoke JSON/日志，独立 notify 主管。
5. 主管保存收到的完整消息并运行通信 check；只有通知提交记录、身份回答和主管收到的 notice 证据齐备，verify 才输出 `evidence_complete`。

## 记录和重复执行

记录位于 `<project>/.herdr-bootstrap/`：

- `request.json`：workspace ID、主管 pane、PLAN_ID、阶段和总计时。
- `framework-start.log`：建队 stdout/stderr。
- `supervisor-task.md`：实际传给主管的测试任务。
- `received-workerN.txt` / `receipt-workerN.json`：收到的协议消息和检查结果。
- `live-test-report.json`：证据是否齐全；不具备证据时为 incomplete。

重复 start 会拒绝创建第二套 workspace。响应丢失时也保留 unknown 状态，不自动重试。
build 已调用过时不再次 create，应检查日志并使用原 PLAN_ID 的 resume。
此入口不自动关闭测试 workspace，也不把信任提示、未读消息或超时记为成功。

## 性能与验证边界

建队端到端时间从 workspace 创建前的 checkpoint 到主管明确 adopt；包含主管启动和模型调度。
它与 `framework.json` 中 worker 启动阶段耗时分开记录。
通信演练发生在建队之后，不能混入启动时间或反过来隐去必要的启动开销。

verify 对本地记录与 agent 回报做核验，不会在外部会话调用实时 Herdr API。
短问答 ask/reply、长期忙碌队列与压缩后的稳定性不是这次通知演练的通过条件，报告会明确标为未运行。
