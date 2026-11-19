# 验证范围

本仓库发布前运行 Python 标准库测试和静态编译检查。当前框架测试共 40 项，覆盖规则注入、启动提示、身份核验、通信、恢复和独立目录部署。2026-11-19 的发布副本测试：40 项通过，耗时 6.967 秒；Python AST 检查通过。

测试通过不代表真实 Herdr 的模型响应、通信及时性或启动性能已经达标。测试使用模拟 Herdr 边界，不启动计费模型会话。

内置通信组件固定为 herdr-peers 2.2.1，完整来源和文件 SHA-256 见 vendor/herdr-peers/UPSTREAM.json；许可证保持原样。

此前开发阶段曾运行上游 protocol/helper/security 隔离测试 353 项通过；上游测试套件未包含在本仓库中，这属于历史验证，不冒充每次发布都已复跑。

## 本地验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
```

## 未验证与边界

- 合体版尚未重新完成真实 Herdr 的建队与消息往返验收。
- 180 秒是启动预算和性能目标，不能作为 2–3 分钟达标的证明。
- 未宣称通过 ruff、pyright 或 shellcheck。
- 没有后台巡检、持久消息队列、可靠送达保证或自动审批。
- 支持边界以 references/compatibility.md 为准，真实验收步骤见 references/acceptance.md。
