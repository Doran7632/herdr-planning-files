#!/usr/bin/env python3
"""Durable local records for a Herdr manager-and-worker framework.

This module never controls Herdr. ``create_framework.py`` owns live pane
operations; this file owns PLAN_ID-scoped role records and safe updates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from uuid import uuid4


SCHEMA_VERSION = 3
PANE_RE = re.compile(r"[A-Za-z0-9]+:p[A-Za-z0-9]+$")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def agent_kind(value: str) -> str:
    aliases = {
        "codex": "codex",
        "claude": "claude code",
        "claude code": "claude code",
        "omp": "omp",
        "oh my pi": "omp",
        "oh-my-pi": "omp",
    }
    normalized = " ".join(value.lower().split())
    if normalized not in aliases:
        raise ValueError(f"Unsupported agent {value!r}; use codex, claude code or omp")
    return aliases[normalized]


def herdr_agent_kind(value: str) -> str:
    """Map user-friendly names to the currently documented Herdr kinds."""
    return {"claude code": "claude"}.get(agent_kind(value), agent_kind(value))


def resolve_agents(count: int, assignments: list[str]) -> list[str]:
    if type(count) is not int or count < 1:
        raise ValueError("Worker count must be positive; the existing main is excluded")
    assigned: dict[int, str] = {}
    for value in assignments:
        match = re.fullmatch(r"worker([1-9][0-9]*)=(.+)", value)
        if not match or int(match[1]) > count or int(match[1]) in assigned:
            raise ValueError(f"Invalid, duplicate or out-of-range assignment: {value!r}")
        assigned[int(match[1])] = agent_kind(match[2])
    needs_claude = "claude code" not in assigned.values()
    for number in range(1, count + 1):
        if number not in assigned:
            assigned[number] = "claude code" if needs_claude else "codex"
            needs_claude = False
    return [assigned[number] for number in range(1, count + 1)]


def _require_regular_directory(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing linked {label}: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"Expected directory for {label}: {path}")


def atomic_text(path: Path, text: str) -> None:
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Refusing symlink: {path}")
    _require_regular_directory(path.parent, "record parent")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _member(role: str, agent: str, pane: str | None = None) -> dict[str, object]:
    return {
        "role": role,
        "agent": agent,
        "pane_id": pane,
        "agent_name": None,
        "identity": None,
        "launch_state": "not_started",
        "launch_hash": None,
        "executable": None,
        "ledger": None,
    }


def save(state: dict[str, object]) -> None:
    atomic_text(
        Path(str(state["plan_dir"])) / "framework.json",
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
    )


COMMON = """# 协作规则
本框架补充项目和用户已有规则；不覆盖它们。

## 身份与边界
- `framework.json` 是成员和 pane 映射；`task_plan.md` 是任务与文件归属；worker 只维护自己的日志。
- `main` 是主管：拆分任务、唯一维护公共记录、审核交付并对用户负责。worker 不是主管，不创建新框架或擅自分配他人。
- 同一时间，一个业务文件只能有一个明确 owner。冲突、跨文件依赖或范围变化先报告主管。
- 角色约定不是操作系统权限，也不授予超出用户或项目规则的权限。

## 通信
- 通信使用本 skill 内置的 DailybotHQ/herdr-peers；无需另装或加载通信 skill。所有调用都显式传 `--pane <自己的 pane_id>`，并限制 `--scope local:<workspace_id>`。
- 优先用 context 给出的 COMMUNICATION_SCRIPT 按角色发送；它会检查实际身份并留下发送记录。首次实际通信前读 COMMUNICATION_GUIDE；初始化时不发送测试消息或自检回合。
- 协议提示里的裸 `herdr-peers reply` 不表示 PATH 已安装该命令。使用 COMMUNICATION_SCRIPT 处理完整消息（包括 PLAN_ID 与协议标记）；诊断时才按需调用绝对 PEERS_HELPER。
- 主管向 worker 分派长任务时用 `notify`：消息包含 PLAN_ID、任务、文件 owner、交付文件和验收条件。通知永不回复。
- 需要一个明确答案的短问题才用 `ask`；一个 ask 只能得到一次授权回复。收到 `[herdr-peers]` 消息先运行 `check`，严格遵守它的 ANSWER / NOTICE / NEVER ANSWER 结论。
- 持有未回复 ask 时不得嵌套 ask。工作通知不是 ask 回复；结果另发独立 notify，避免互相等待和回复循环。
- worker 完成、阻塞或交接时先写个人日志和证据，再向 framework.json 中主管的显式地址 notify 发送结果路径。不要依赖 lead 别名或把消息正文当作唯一记录。
- 不启动 autopilot、appointment、brain 或 revive；本框架没有后台巡检或自动批准。

## 恢复与开发
- 收到任务、恢复或上下文压缩后，重读 `rules/common.md`、本角色文件、`framework.json`、`task_plan.md` 和个人日志；沿用同一个 PLAN_ID。
- 开发前读取 `karpathy-guidelines`，再按任务选择合适的 Matt Pocock / gstack 技能。
- 项目关联 Git 时，新增非单元测试代码文件及时 `git add`；单元测试文件不自动暂存。
- 异常处理先完整输出原始堆栈，再输出业务提示。未验证的结果要明确标为未验证。
"""


def prepare(project: str | Path, count: int, assignments: list[str], main_agent: str,
            main_pane: str, workspace: str) -> dict[str, object]:
    agents = resolve_agents(count, assignments)
    main_agent = agent_kind(main_agent)
    root = Path(project).expanduser().resolve(strict=True)
    if not root.is_dir() or not PANE_RE.fullmatch(main_pane):
        raise ValueError("Existing project directory and observed main pane are required")
    if main_pane.split(":")[0] != workspace or any(c in str(root) for c in "\n\r\0"):
        raise ValueError("Project or workspace binding is invalid")

    planning = root / ".planning"
    _require_regular_directory(planning, ".planning root")
    planning.mkdir(exist_ok=True)
    plan_id = datetime.now().astimezone().strftime("%Y-%m-%d") + "-herdr-" + uuid4().hex[:12]
    directory = planning / plan_id
    directory.mkdir(mode=0o700)
    (directory / "rules").mkdir(mode=0o700)
    (directory / "workers").mkdir(mode=0o700)

    workers = [_member(f"worker{i}", kind) for i, kind in enumerate(agents, 1)]
    for member in workers:
        member["ledger"] = str(directory / "workers" / f"{member['role']}.md")
    state: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "plan_dir": str(directory),
        "project_root": str(root),
        "workspace_id": workspace,
        "created_at": now(),
        "state": "prepared",
        "main": _member("main", main_agent, main_pane),
        "workers": workers,
        "rules_sha256": {},
        "peers": {"configured": False},
        "pending_operation": None,
        "timings": [],
        "attempts": [],
        "live_validation": "not_run",
    }

    texts = {
        "rules/common.md": COMMON,
        "rules/main.md": """# main（主管）
你保留当前会话并对用户负责。收到业务目标后，把它拆成可验证任务，登记在 `task_plan.md`，为每个任务明确一个文件 owner、交付物与验收方式，再用 `notify` 分派。不要把长期工作作为 ask 发送。

检查 worker 交付时，以工作区实际内容、测试和日志证据为准；汇总时明确成功、失败、未知和下一步。公共记录只能由你写；不得代替 worker 编造完成证据。
""",
        "task_plan.md": f"""# 协作框架

PLAN_ID：{plan_id}

## Goal
框架已建立，等待用户给出业务目标。

### Phase 1: 框架初始化
- **Status:** in_progress

### Phase 2: 执行用户任务
- **Status:** pending

## Members
- 主管：main (`{main_pane}`)
- worker：待启动后写入 `framework.json`

## Task Board
尚未分配。主管收到任务后必须记录 task、owner、文件归属、验收标准和状态。

## Next Step
核验 agent 启动与显式通信地址；随后等待用户任务。
""",
        "findings.md": "# Findings\n\n## 已验证事实\n\n## 未确认项\n",
        "progress.md": f"# Progress\n\n{now()}：建立 PLAN_ID 本地记录；尚未创建或验证 worker。\n",
    }
    for member in workers:
        role = str(member["role"])
        texts[f"rules/{role}.md"] = f"""# {role}（工作人员）
你只处理主管通过 `notify` 分派的工作。开始前确认任务卡里的文件归属、交付物和验收条件；不明确就向主管发送一个短 `ask`，不要猜测。

你只写获分配的业务文件和 `workers/{role}.md`。完成、阻塞或交接时，先在日志写清改动、验证、风险和下一步，再向主管的显式地址 notify 发送日志与交付路径。没有任务时保持等待。
"""
        texts[f"workers/{role}.md"] = f"""# {role}

Status：waiting_for_task
Task：尚未分配

## 文件归属
尚未分配。

## 进度与验证证据

## 阻塞与交接

## 恢复入口
沿用 PLAN_ID `{plan_id}`，读取 `framework.json`、`task_plan.md` 与本日志。
"""
    for relative, text in texts.items():
        atomic_text(directory / relative, text)
        if relative.startswith("rules/"):
            state["rules_sha256"][relative] = digest(text)  # type: ignore[index]
    save(state)
    return state


def load(directory: str | Path) -> dict[str, object]:
    original = Path(directory).expanduser().absolute()
    if original.is_symlink() or original.parent.is_symlink() or (original / "framework.json").is_symlink():
        raise ValueError("Refusing linked plan directory or manifest")
    directory = Path(directory).expanduser().resolve(strict=True)
    state = json.loads((directory / "framework.json").read_text(encoding="utf-8"))
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"This runner only resumes schema {SCHEMA_VERSION}; legacy plans are not auto-migrated")
    expected = Path(state["project_root"]) / ".planning" / state["plan_id"]
    if str(directory) != state["plan_dir"] or directory != expected or expected.is_symlink():
        raise ValueError("Plan binding has changed")
    _require_regular_directory(directory / "rules", "rule directory")
    _require_regular_directory(directory / "workers", "worker directory")
    members = [state["main"], *state["workers"]]
    roles = ["main", *[f"worker{i}" for i in range(1, len(state["workers"]) + 1)]]
    if [member["role"] for member in members] != roles:
        raise ValueError("Invalid member map")
    required = {"rules/common.md", *[f"rules/{role}.md" for role in roles]}
    if set(state["rules_sha256"]) != required:
        raise ValueError("Invalid rule file map")
    panes = [m["pane_id"] for m in members if m["pane_id"]]
    if len(panes) != len(set(panes)) or any(not PANE_RE.fullmatch(p) or p.split(":")[0] != state["workspace_id"] for p in panes):
        raise ValueError("Invalid or duplicate pane binding")
    for relative, expected_hash in state["rules_sha256"].items():
        path = directory / relative
        if path.is_symlink() or digest(path.read_text(encoding="utf-8")) != expected_hash:
            raise ValueError(f"Role rules changed: {relative}; do not silently hot-reload a running framework")
    return state


def _member_by_role(state: dict[str, object], role: str) -> dict[str, object]:
    for member in [state["main"], *state["workers"]]:
        if member["role"] == role:
            return member
    raise ValueError(f"Unknown role: {role}")


def append_progress(state: dict[str, object], text: str) -> None:
    path = Path(str(state["plan_dir"])) / "progress.md"
    if path.is_symlink():
        raise ValueError(f"Refusing symlink: {path}")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n{now()}：{text}\n")


def context(state, role):
    member = _member_by_role(state, role)
    directory = Path(state["plan_dir"])
    return (f"PLAN_ID={state['plan_id']}\nROLE={role}\nSELF_PANE={member['pane_id']}\n"
            f"MAIN_PANE={state['main']['pane_id']}\nPWF_PLAN_ROOT={state['project_root']}\n"
            f"PLAN_DIR={directory}\nPEERS_SCOPE=local:{state['workspace_id']}\n"
            f"PEERS_HELPER={state['peers'].get('helper', 'not_configured')}\n"
            f"COMMUNICATION_SCRIPT={Path(__file__).resolve().with_name('communicate.py')}\n"
            f"COMMUNICATION_GUIDE={Path(__file__).resolve().parents[1] / 'references/communication.md'}\n"
            "PWF 操作显式传 PLAN_ID 和 PWF_PLAN_ROOT；不要修改共享 .active_plan。\n\n"
            + (directory / "rules/common.md").read_text(encoding="utf-8") + "\n"
            + (directory / "rules" / f"{role}.md").read_text(encoding="utf-8"))


def status(directory: str | Path) -> dict[str, object]:
    """Return durable state only. It deliberately makes no live Herdr claim."""
    state = load(directory)
    plan_dir = Path(str(state["plan_dir"]))
    required = ["framework.json", "task_plan.md", "findings.md", "progress.md", "rules/common.md", "rules/main.md"]
    required += [f"rules/{worker['role']}.md" for worker in state["workers"]]
    required += [f"workers/{worker['role']}.md" for worker in state["workers"]]
    return {
        "plan_id": state["plan_id"],
        "state": state["state"],
        "peers_configured": state["peers"]["configured"],
        "pending_operation": state["pending_operation"],
        "missing_files": [item for item in required if not (plan_dir / item).is_file()],
        "unassigned_roles": [member["role"] for member in [state["main"], *state["workers"]] if not member["pane_id"]],
        "members": [{key: member.get(key) for key in ("role", "agent", "pane_id", "agent_name", "launch_state", "ledger")}
                    for member in [state["main"], *state["workers"]]],
        "live_recheck_required": True,
    }


def repair(directory: str | Path) -> dict[str, object]:
    state = load(directory)
    plan_dir = Path(str(state["plan_dir"]))
    templates = {
        "task_plan.md": "# 协作框架（恢复占位）\n\n原任务记录缺失；先从现有日志、工作区和用户说明恢复，不推测历史。\n",
        "findings.md": "# Findings（恢复占位）\n\n原内容缺失，待从证据恢复。\n",
        "progress.md": "# Progress（恢复占位）\n\n原内容缺失，待从证据恢复。\n",
    }
    for worker in state["workers"]:
        templates[f"workers/{worker['role']}.md"] = (
            f"# {worker['role']}（恢复占位）\n\n原日志缺失；先核对现场与其他记录。\n"
        )
    created = []
    for relative, text in templates.items():
        path = plan_dir / relative
        if not path.exists():
            atomic_text(path, f"> 恢复于 {now()}；不代表此前没有任务或证据。\n\n{text}")
            created.append(relative)
    return {"plan_id": state["plan_id"], "created_files": created}


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def main() -> None:
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="create PLAN_ID-scoped records without controlling Herdr")
    new.add_argument("--project", required=True)
    new.add_argument("--workspace", required=True)
    new.add_argument("--main-pane", required=True)
    new.add_argument("--main-agent", required=True)
    new.add_argument("--workers", type=int, required=True)
    new.add_argument("--agent", action="append", default=[], metavar="workerN=AGENT")
    resolve = commands.add_parser("resolve", help="resolve worker agent defaults")
    resolve.add_argument("--workers", type=int, required=True)
    resolve.add_argument("--agent", action="append", default=[], metavar="workerN=AGENT")
    for name in ("status", "repair"):
        command = commands.add_parser(name)
        command.add_argument("--plan-dir", required=True)
    context_parser = commands.add_parser("context", help="print scoped role instructions")
    context_parser.add_argument("--plan-dir", required=True)
    context_parser.add_argument("--role", required=True)
    args = parser.parse_args()

    if args.command == "new":
        result = prepare(args.project, args.workers, args.agent, args.main_agent, args.main_pane, args.workspace)
    elif args.command == "resolve":
        result = {"agents": resolve_agents(args.workers, args.agent)}
    elif args.command == "status":
        result = status(args.plan_dir)
    elif args.command == "repair":
        result = repair(args.plan_dir)
    else:
        print(context(load(args.plan_dir), args.role))
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("框架记录操作未完成；保留已有文件，按原 PLAN_ID 核对后恢复。", file=sys.stderr)
        sys.exit(1)
