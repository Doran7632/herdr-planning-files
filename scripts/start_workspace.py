#!/usr/bin/env python3
"""User-run one-command workspace bootstrap and evidence-based smoke verification.

Run start from a genuine Herdr shell. This entry never fabricates Herdr context.
"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import traceback
from uuid import uuid4

import launch
import records
import create_framework as runner

SCRIPTS = Path(__file__).resolve().parent


def save(request):
    records.atomic_text(Path(request['run_dir']) / 'request.json', json.dumps(request, ensure_ascii=False, indent=2) + '\n')


def read(run_dir):
    directory = Path(run_dir).resolve(strict=True)
    request = json.loads((directory / 'request.json').read_text())
    if request['run_dir'] != str(directory) or directory.parent != Path(request['project']):
        raise ValueError('Bootstrap directory binding changed')
    return request


def supervisor_prompt(run_dir):
    command = shlex.join([sys.executable, str(Path(__file__).resolve())])
    directory = shlex.quote(str(run_dir))
    return f'''用户已授权在此独立测试 workspace 中搭建并验证 Herdr 协作框架。你是新主管 Codex。
只测试当前目录，不改其他 workspace，不升级 agent、不修改全局设置或审批策略。
使用本提示给出的源码入口，不调用已安装的旧 herdr-planning-files，不设置 Session Pin。
保留当前主管，worker 数量和类型已保存在 {run_dir}/request.json。
1. 只运行一次：{command} build --run-dir {directory}
   启动器已获用户授权：Codex 本次跳过更新，Claude 仅自动确认与目标目录匹配的工作区信任提示。
   若失败，保留资源和日志，不重新 create，不自行确认其他权限/登录提示；报告实际错误。
2. 成功后完整读取 build 输出的 main context，采用主管职责；然后运行
   {command} adopt --run-dir {directory}
   adopt 输出 PLAN_DIR、nonce 和每个 worker 的精确测试任务与 shell 命令。
3. 依次执行输出的 notify 命令（任务很短）。worker 从自己的既有角色上下文报告身份，
   写各自 smoke JSON 与日志后独立通知你。不给 worker 补发其角色答案，不把发送成功当接收成功。
4. 收到的完整协议通知保存为 run_dir/received-workerN.txt（包括 PLAN_ID 正文及协议标记）。
   执行 communicate.py --plan-dir <实际PLAN_DIR> --role main check --message <该文件>，
   把真实 JSON verdict 保存为 run_dir/receipt-workerN.json。NOTICE 不回复。
5. 收齐后运行 {command} verify --run-dir {directory}。
   verify 区分建队时间和通信验收时间；只有真实证据齐全才报告通过。没有收到时如实等待，
   不伪造 received/receipt 或 worker 输出。测试不通过也保留完整日志。
所有工具异常完整保留堆栈，禁止为了测试通过关闭 hook 或跳过审批。'''


def start(runtime, project, workers, agent_assignments, label, base_files=None):
    project = Path(project).expanduser().resolve(strict=True)
    kinds = records.resolve_agents(workers, agent_assignments)
    helper = runner.peer_helper()
    runner.check_peers(runtime, helper)
    supervisor = launch.capabilities('codex')['executable']
    for kind in set(kinds):
        launch.capabilities(kind)
    base_files = dict(base_files or {})
    if any(int(role[6:]) > workers for role in base_files):
        raise ValueError('Base instruction role exceeds worker count')
    for index, kind in enumerate(kinds, 1):
        launch.check_append_conflicts(kind, project, base_file=base_files.get(f'worker{index}'))
    # Exclusive creation makes repeating the same command safe: no duplicate workspace.
    run_dir = project / '.herdr-bootstrap'
    if run_dir.exists() or run_dir.is_symlink():
        raise RuntimeError(f'Existing test run at {run_dir}; inspect request.json and resume its plan, do not create again')
    run_dir.mkdir(mode=0o700)
    request = {'run_dir': str(run_dir), 'project': str(project), 'workers': workers,
               'assignments': agent_assignments, 'base_files': base_files, 'nonce': uuid4().hex,
               'supervisor_executable': supervisor, 'peer_helper': helper,
               'started_epoch': time.time(), 'started_at': records.now(),
               'state': 'workspace_create_unknown'}
    launch_command = shlex.join([sys.executable, str(Path(__file__).resolve()), 'entry', '--run-dir', str(run_dir)])
    if len(launch_command.encode()) > 900:
        raise ValueError('Bootstrap path too long for conservative terminal input limit')
    save(request)
    created = runtime.api('workspace', 'create', '--cwd', str(project), '--label', label, '--focus')
    workspace = created['workspace']['workspace_id']
    pane = created['root_pane']
    if pane['workspace_id'] != workspace or not records.PANE_RE.fullmatch(pane['pane_id']):
        raise RuntimeError('Unexpected workspace creation response; do not retry')
    request.update(workspace_id=workspace, main_pane=pane['pane_id'], state='workspace_created')
    save(request)
    while True:
        info = runtime.api('pane', 'get', pane['pane_id'])['pane']
        cwd = info.get('foreground_cwd') or info.get('cwd')
        if info.get('agent') or not cwd or Path(cwd).resolve() != project:
            raise RuntimeError('New workspace pane is not the expected empty project shell')
        process = runtime.api('pane', 'process-info', '--pane', pane['pane_id'])['process_info']
        if process.get('shell_pid') and process['shell_pid'] == process.get('foreground_process_group_id'):
            break
        runtime.pause()
    request['state'] = 'supervisor_submission_unknown'
    save(request)
    runtime.run(['herdr', 'pane', 'run', pane['pane_id'], launch_command])
    # Native entry / supervisor own subsequent state. Parent does not overwrite their progress.
    return {'workspace_id': workspace, 'main_pane': pane['pane_id'], 'run_dir': str(run_dir),
            'state': 'supervisor_command_submitted_not_yet_verified'}


def require_supervisor(request):
    runtime = runner.Runtime(seconds=180)
    if runtime.pane != request['main_pane'] or runtime.workspace != request['workspace_id']:
        raise RuntimeError('Must execute in this run\'s actual supervisor pane')
    if Path.cwd().resolve() != Path(request['project']):
        raise RuntimeError('Supervisor cwd differs from test project')
    return runtime


def entry(request):
    require_supervisor(request)
    request.update(state='supervisor_exec_requested', supervisor_pid=os.getpid())
    save(request)
    prompt = supervisor_prompt(Path(request['run_dir']))
    records.atomic_text(Path(request['run_dir']) / 'supervisor-task.md', prompt)
    env = os.environ.copy()
    for key in ('HERDR_PEERS_DEPTH', 'DWP_PLAN', 'DWP_TASK'):
        env.pop(key, None)
    os.execve(request['supervisor_executable'],
              [request['supervisor_executable'], '--no-daemon', *launch.CODEX_SKIP_UPDATE_ARGS, prompt], env)


def build(request):
    require_supervisor(request)
    if request.get('build_requested_at'):
        raise RuntimeError('Build was already requested; inspect framework-start.log and use original PLAN_ID resume')
    request.update(state='framework_build_requested', build_requested_at=records.now())
    save(request)
    argv = [sys.executable, str(SCRIPTS / 'create_framework.py'), 'create', '--project', request['project'],
            '--main-agent', 'codex', '--workers', str(request['workers'])]
    for assignment in request['assignments']:
        argv += ['--agent', assignment]
    for role, path in request.get('base_files', {}).items():
        argv += ['--base-instructions', role + '=' + path]
    result = subprocess.run(argv, capture_output=True, text=True)
    records.atomic_text(Path(request['run_dir']) / 'framework-start.log', result.stdout + result.stderr)
    print(result.stdout, end='')
    print(result.stderr, end='', file=sys.stderr)
    for line in result.stdout.splitlines():
        if line.startswith('{') and '"plan_dir"' in line:
            value = json.loads(line)
            if 'plan_dir' in value:
                request['plan_dir'] = value['plan_dir']
                break
    request['state'] = 'framework_cli_ready' if result.returncode == 0 else 'framework_failed'
    save(request)
    if result.returncode:
        raise RuntimeError('Framework creation failed; see framework-start.log')
    if not request.get('plan_dir'):
        raise RuntimeError('Build did not return an unambiguous PLAN_ID')


def adopt(request):
    runtime = require_supervisor(request)
    state = records.load(request['plan_dir'])
    runner.validate_main(runtime, state)
    if state['state'] != 'ready':
        raise RuntimeError('Cannot adopt an incomplete framework as ready')
    if request.get('main_adopted_at'):
        raise RuntimeError('Adoption already recorded; do not dispatch the test twice')
    request.update(state='main_adopted_waiting_smoke', main_adopted_at=records.now(),
                   build_end_to_end_seconds=round(time.time() - request['started_epoch'], 3))
    save(request)
    commands = []
    for worker in state['workers']:
        destination = Path(state['plan_dir']) / 'workers' / f"{worker['role']}.smoke.json"
        task = (f"用户授权的只读框架测试，nonce={request['nonce']}。从你启动时已有的规则/上下文读取 ROLE、"
                f"SELF_PANE、MAIN_PANE 和 PLAN_ID；不要从本消息猜身份。将 JSON 写入 {destination}，"
                "字段 nonce, role, self_pane, main_pane, plan_id。此测试授予你该文件和个人日志的写入归属，"
                "不改其他业务文件。先在自己的日志记证据，再用 context 中 COMMUNICATION_SCRIPT 向 main "
                "独立 notify 一条含 nonce 和 JSON 路径的结果。不要使用 reply 回复此通知；不要再创建框架。")
        argv = [sys.executable, str(SCRIPTS / 'communicate.py'), '--plan-dir', state['plan_dir'],
                '--role', 'main', 'notify', '--to', worker['role'], '--text', task]
        commands.append(shlex.join(argv))
    print(json.dumps({'plan_dir': state['plan_dir'], 'nonce': request['nonce'],
                      'build_end_to_end_seconds': request['build_end_to_end_seconds'],
                      'notify_commands_execute_once': commands}, ensure_ascii=False, indent=2))


def verify(request):
    state = records.load(request['plan_dir'])
    checks = []
    for worker in state['workers']:
        path = Path(state['plan_dir']) / 'workers' / f"{worker['role']}.smoke.json"
        receipt = Path(request['run_dir']) / f"receipt-{worker['role']}.json"
        raw = Path(request['run_dir']) / f"received-{worker['role']}.txt"
        value = json.loads(path.read_text()) if path.exists() else {}
        expected = {'nonce': request['nonce'], 'role': worker['role'], 'self_pane': worker['pane_id'],
                    'main_pane': state['main']['pane_id'], 'plan_id': state['plan_id']}
        verdict = json.loads(receipt.read_text()) if receipt.exists() else {}
        received = raw.read_text() if raw.exists() else ''
        outbound = []
        for outbox in (Path(state['plan_dir']) / 'workers').glob(f"{worker['role']}.outgoing-*.json"):
            message = json.loads(outbox.read_text())
            if (message.get('to_role') == 'main' and message.get('verb') == 'notify'
                    and request['nonce'] in message.get('body', '') and message.get('state') == 'submitted'):
                outbound.append(str(outbox))
        checks.append({'role': worker['role'], 'identity_answer_matches': value == expected,
                       'notification_submitted': bool(outbound),
                       'main_recorded_notice': verdict.get('decision') == 'notice'
                           and verdict.get('from') == 'local:' + worker['pane_id']
                           and received.startswith('PLAN_ID=' + state['plan_id'] + '\n')
                           and request['nonce'] in received})
    passed = state['state'] == 'ready' and bool(request.get('main_adopted_at')) and all(
        all(item[k] for k in ('identity_answer_matches', 'notification_submitted', 'main_recorded_notice')) for item in checks)
    report = {'result': 'evidence_complete' if passed else 'incomplete', 'checks': checks,
              'build_end_to_end_seconds': request.get('build_end_to_end_seconds'),
              'framework_timings': state['timings'], 'worker_count': len(state['workers']),
              'verified_at': records.now(), 'assurance': 'local_records_and_agent_reports; no fresh live API probe',
              'ask_reply_test': 'not_run', 'long_run_reliability': 'not_run'}
    records.atomic_text(Path(request['run_dir']) / 'live-test-report.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = records.Parser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    create = sub.add_parser('start')
    create.add_argument('--project', required=True)
    create.add_argument('--workers', type=int, default=3)
    create.add_argument('--agent', action='append', default=[])
    create.add_argument('--base-instructions', action='append', default=[])
    create.add_argument('--label', default='Herdr native-role smoke test')
    for mode in ('entry', 'build', 'adopt', 'verify'):
        command = sub.add_parser(mode)
        command.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    if args.mode == 'start':
        result = start(runner.Runtime(), args.project, args.workers, args.agent, args.label,
                       runner.assignments(args.base_instructions, 'base instruction file'))
    else:
        request = read(args.run_dir)
        result = globals()[args.mode](request)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.mode == 'verify' and result['result'] != 'evidence_complete':
            sys.exit(2)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        print('Workspace 测试入口未完成；保留已有资源和 request.json，不重跑 start，不自动审批。', file=sys.stderr)
        sys.exit(1)
