#!/usr/bin/env python3
"""Deterministic Herdr launcher: native role injection, parallel starts, durable resume."""

import argparse
import concurrent.futures
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

import launch
import records


class FrameworkError(RuntimeError):
    pass


def command(argv, timeout):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise FrameworkError(f"Command failed ({result.returncode}): {argv[:4]}\n{result.stdout}\n{result.stderr}")
    return result.stdout


class Runtime:
    def __init__(self, *, env=None, transport=command, seconds=180):
        self.env = dict(os.environ if env is None else env)
        if self.env.get('HERDR_ENV') != '1':
            raise FrameworkError('Run inside a real Herdr pane (HERDR_ENV=1); no outside-session control')
        self.pane = self.env.get('HERDR_PANE_ID', '')
        self.workspace = self.env.get('HERDR_WORKSPACE_ID', '')
        if not records.PANE_RE.fullmatch(self.pane) or self.pane.split(':')[0] != self.workspace:
            raise FrameworkError('Missing or inconsistent inherited Herdr identity')
        if not 1 <= seconds <= 600:
            raise ValueError('Deadline must be between 1 and 600 seconds')
        self.transport = transport
        self.started = time.monotonic()
        self.deadline = self.started + seconds

    def remaining(self):
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('Framework deadline expired; keep existing panes and resume the same plan')
        return value

    def run(self, argv, limit=15):
        return self.transport(argv, timeout=min(limit, self.remaining()))

    def api(self, *args):
        value = json.loads(self.run(['herdr', *args]))
        if not isinstance(value, dict) or 'error' in value or not isinstance(value.get('result'), dict):
            raise FrameworkError(f'Unexpected Herdr response: {value}')
        return value['result']

    def pane_info(self, pane):
        value = self.api('pane', 'get', pane)['pane']
        if value['pane_id'] != pane or value['workspace_id'] != self.workspace:
            raise FrameworkError('Pane identity/workspace mismatch')
        return value

    def processes(self, pane):
        value = self.api('pane', 'process-info', '--pane', pane)['process_info']
        if value['pane_id'] != pane:
            raise FrameworkError('Process information belongs to a different pane')
        return value

    def agent(self, pane):
        agents = self.api('agent', 'list')['agents']
        matches = [a for a in agents if a['pane_id'] == pane]
        if len(matches) > 1:
            raise FrameworkError('Duplicate live agent pane mapping')
        return matches[0] if matches else None

    def pause(self):
        time.sleep(min(0.25, self.remaining()))


def identity(runtime, agent, expected_kind, project, *, pid=None):
    if not agent or records.agent_kind(agent.get('agent', '')) != expected_kind:
        raise FrameworkError('Expected agent kind not detected')
    if agent['workspace_id'] != runtime.workspace:
        raise FrameworkError('Agent workspace changed')
    cwd = agent.get('foreground_cwd') or agent.get('cwd')
    if not cwd or Path(cwd).resolve() != Path(project):
        raise FrameworkError('Agent cwd does not match this project')
    processes = runtime.processes(agent['pane_id'])
    pids = sorted(p['pid'] for p in processes.get('foreground_processes', [])
                  if p['pid'] != processes.get('shell_pid'))
    if pid is not None and pid not in pids:
        raise FrameworkError('Recorded launcher process is not in the live foreground group')
    if not pids:
        raise FrameworkError('No foreground agent process evidence')
    return {'pane_id': agent['pane_id'], 'terminal_id': agent['terminal_id'],
            'agent': expected_kind, 'pid': pid if pid is not None else min(pids),
            'agent_session': agent.get('agent_session')}


def verify_identity(expected, actual):
    if any(expected[k] != actual[k] for k in ('pane_id', 'terminal_id', 'agent', 'pid')):
        raise FrameworkError('Recorded agent identity changed; refusing to address a replacement')
    if expected.get('agent_session') is not None and expected['agent_session'] != actual.get('agent_session'):
        raise FrameworkError('Agent session changed; explicit rebind is required')


def peer_helper():
    candidate = Path(__file__).resolve().parents[1] / 'vendor/herdr-peers/scripts/herdr-peers'
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    raise FrameworkError(f'Peer helper is missing or not executable: {candidate}; '
                         'restore the complete herdr-planning-files package before creating panes')


def check_peers(runtime, helper):
    output = runtime.run([helper, '--version'], limit=5)
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', output)
    if not match or tuple(map(int, match.groups())) != (2, 2, 1):
        raise FrameworkError('This adapter is verified against herdr-peers 2.2.1; review other versions before use')
    return output.strip()


def preflight(runtime, project, kinds, main_kind, bases=None):
    project = Path(project).expanduser().resolve(strict=True)
    if Path.cwd().resolve() != project:
        raise FrameworkError('Project must be the calling pane cwd; do not silently change topology or cwd')
    helper = peer_helper()
    peer_version = check_peers(runtime, helper)
    current = runtime.api('pane', 'current', '--current')['pane']
    if current['pane_id'] != runtime.pane or current['workspace_id'] != runtime.workspace:
        raise FrameworkError('Inherited caller context resolves to another pane; stop before mutation')
    main = runtime.agent(runtime.pane)
    main_identity = identity(runtime, main, records.agent_kind(main_kind), project)
    caps = {}
    # Cached once per kind; independent help calls run concurrently, with the same deadline.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(set(kinds))) as pool:
        jobs = {pool.submit(launch.capabilities, kind): kind for kind in set(kinds)}
        for future in concurrent.futures.as_completed(jobs):
            caps[jobs[future]] = future.result()
    append = {}
    for index, kind in enumerate(kinds, 1):
        role = f'worker{index}'
        append[role] = launch.check_append_conflicts(kind, project, base_file=(bases or {}).get(role))
    runtime.remaining()
    return project, main_identity, helper, peer_version, caps, append


@contextmanager
def locked(plan_dir):
    path = Path(plan_dir) / '.runner.lock'
    if path.is_symlink():
        raise FrameworkError('Refusing linked lock file')
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def timed(state, stage, start, outcome='ok'):
    state['timings'].append({'at': records.now(), 'stage': stage,
                             'seconds': round(time.monotonic() - start, 4), 'outcome': outcome})
    records.save(state)


def validate_main(runtime, state):
    if runtime.pane != state['main']['pane_id'] or runtime.workspace != state['workspace_id']:
        raise FrameworkError('Resume must run in the recorded main pane')
    actual = identity(runtime, runtime.agent(runtime.pane), state['main']['agent'], state['project_root'],
                      pid=state['main']['identity']['pid'])
    verify_identity(state['main']['identity'], actual)


def split_workers(runtime, state, adoptions=None):
    adoptions = dict(adoptions or {})
    pending = state['pending_operation']
    if pending:
        role = pending['role']
        pane = adoptions.pop(role, None)
        if pending['kind'] != 'split' or not pane:
            raise FrameworkError('Uncertain split: inspect live panes and use --adopt-pane workerN=<observed-id>; do not split again')
        info = runtime.pane_info(pane)
        if pane in pending['before'] or info['tab_id'] != pending['tab_id'] or info.get('agent'):
            raise FrameworkError('Adoption must be an unoccupied new pane from the interrupted split')
        cwd = info.get('foreground_cwd') or info.get('cwd')
        if not cwd or Path(cwd).resolve() != Path(state['project_root']):
            raise FrameworkError('Adoption cwd differs')
        worker = next(w for w in state['workers'] if w['role'] == role)
        worker.update(pane_id=pane, launch_state='pane_created')
        state['pending_operation'] = None
        records.save(state)
    if adoptions:
        raise FrameworkError('Adoption is only allowed for the recorded uncertain split')
    for worker in state['workers']:
        if worker['pane_id']:
            continue
        own = {state['main']['pane_id'], *[w['pane_id'] for w in state['workers'] if w['pane_id']]}
        layout = runtime.api('pane', 'layout', '--pane', runtime.pane)['layout']
        choices = [p for p in layout['panes'] if p['pane_id'] in own]
        anchor = max(choices, key=lambda p: p['rect']['width'] * p['rect']['height'])
        rect = anchor['rect']
        direction = 'right' if rect['width'] >= 2 * rect['height'] else 'down'
        state['pending_operation'] = {'kind': 'split', 'role': worker['role'],
                                      'before': [p['pane_id'] for p in layout['panes']],
                                      'tab_id': layout['tab_id'], 'anchor': anchor['pane_id']}
        records.save(state)
        pane = runtime.api('pane', 'split', anchor['pane_id'], '--direction', direction,
                           '--cwd', state['project_root'], '--no-focus')['pane']
        if (not records.PANE_RE.fullmatch(pane['pane_id']) or pane['workspace_id'] != runtime.workspace
                or pane['tab_id'] != layout['tab_id'] or pane['pane_id'] in own):
            raise FrameworkError('Split returned an unexpected pane identity')
        worker.update(pane_id=pane['pane_id'], launch_state='pane_created')
        state['pending_operation'] = None
        records.save(state)


def prepare_launch_files(state, append):
    for worker in state['workers']:
        path = Path(state['plan_dir']) / 'rules' / f"{worker['role']}.launch.txt"
        if worker['launch_hash']:
            if path.is_symlink() or records.digest(path.read_text(encoding='utf-8')) != worker['launch_hash']:
                raise FrameworkError('Prepared native instruction file changed')
            continue
        text = append.get(worker['role'], '') + '\n\n' + records.context(state, worker['role'])
        records.atomic_text(path, text)
        worker['launch_hash'] = records.digest(text)
        launch.entry_command(state['plan_dir'], worker['role'])
        records.save(state)


def accept_startup_trust(runtime, state, worker, actual):
    # User-authorized exception: only this newly launched Claude's exact project dialog.
    if worker['agent'] != 'claude code' or worker['launch_state'] == 'ready':
        return False
    pane = worker['pane_id']
    read = ['herdr', 'pane', 'read', pane, '--source', 'visible', '--lines', '100']
    screen = runtime.run(read)
    selected = launch.claude_trust_selection(screen, state['project_root'])
    if selected is None:
        return False
    audit_path = Path(state['plan_dir']) / 'workers' / f"{worker['role']}.startup-trust.json"
    if audit_path.is_symlink():
        raise FrameworkError('Linked startup trust record')
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding='utf-8'))
        verify_identity(audit['identity'], actual)
        if audit['state'] == 'confirmation_submitted':
            return True  # Wait for the already-submitted confirmation; never press twice.
        raise FrameworkError('Startup trust input has an uncertain outcome; inspect before further input')
    audit = {'at': records.now(), 'role': worker['role'], 'project_root': state['project_root'],
             'identity': actual, 'screen_sha256': records.digest(screen),
             'authorization': 'user_requested_matching_workspace_trust_yes', 'state': 'selection_requested'}
    records.atomic_text(audit_path, json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    if selected == 'no':
        runtime.run(['herdr', 'agent', 'send-keys', pane, 'down'])
        runtime.pause()
    # Re-read before Enter: a changed dialog or a replacement process must never get Yes.
    current = runtime.agent(pane)
    fresh = identity(runtime, current, worker['agent'], state['project_root'], pid=actual['pid'])
    verify_identity(actual, fresh)
    if (current.get('agent_status') == 'working'
            or launch.claude_trust_selection(runtime.run(read), state['project_root']) != 'yes'):
        raise FrameworkError('Startup trust dialog changed before confirmation; no Enter sent')
    audit.update(state='confirmation_requested', confirmation_requested_at=records.now())
    records.atomic_text(audit_path, json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    runtime.run(['herdr', 'agent', 'send-keys', pane, 'enter'])
    audit.update(state='confirmation_submitted', confirmation_submitted_at=records.now())
    records.atomic_text(audit_path, json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    return True


def start_one(runtime, state, worker, submit):
    start = time.monotonic()
    pane = worker['pane_id']
    if submit:
        # Newly created shells need time to reach their prompt. No input until foreground shell evidence.
        while True:
            info = runtime.pane_info(pane)
            if info.get('agent'):
                raise FrameworkError('New worker pane already contains an agent; refusing input')
            cwd = info.get('foreground_cwd') or info.get('cwd')
            if not cwd or Path(cwd).resolve() != Path(state['project_root']):
                raise FrameworkError('New worker cwd changed')
            proc = runtime.processes(pane)
            if proc.get('shell_pid') and proc['shell_pid'] == proc.get('foreground_process_group_id'):
                break
            runtime.pause()
        # Unlike inspection/split, pane run may succeed with empty stdout (CLI admission only).
        # Process exit status acknowledges submission; receipt + live agent evidence below prove readiness.
        runtime.run(['herdr', 'pane', 'run', pane, launch.entry_command(state['plan_dir'], worker['role'])])
    receipt_path = Path(state['plan_dir']) / 'workers' / f"{worker['role']}.launch.json"
    while True:
        runtime.remaining()
        if receipt_path.exists():
            if receipt_path.is_symlink():
                raise FrameworkError('Linked launch receipt')
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            if (receipt['role'] != worker['role'] or receipt['pane_id'] != pane
                    or receipt['instructions_sha256'] != worker['launch_hash']):
                raise FrameworkError('Launch receipt does not match the prepared worker')
            if receipt['stage'] == 'exec_failed':
                raise FrameworkError('Native exec failed; inspect worker output, do not submit twice')
            agent = runtime.agent(pane)
            if agent:
                actual = identity(runtime, agent, worker['agent'], state['project_root'], pid=receipt['pid'])
                if worker['identity']:
                    verify_identity(worker['identity'], actual)
                if agent.get('agent_status') in ('blocked', 'unknown', 'idle', 'done'):
                    if accept_startup_trust(runtime, state, worker, actual):
                        runtime.pause()
                        continue
                if agent.get('agent_status') == 'blocked':
                    raise FrameworkError('Agent startup is blocked by an approval or question; user action required')
                # interactive_ready belongs to Herdr's managed `agent start` lifecycle.
                # Native `pane run` agents omit it even when idle. Their receipt, live
                # foreground identity and settled status are the readiness evidence.
                if agent.get('agent_status') in ('idle', 'done') and not agent.get('launch_pending'):
                    return actual, round(time.monotonic() - start, 4)
        runtime.pause()


def start_workers(runtime, state):
    jobs = []
    for worker in state['workers']:
        submit = worker['launch_state'] == 'pane_created'
        if submit:
            worker['launch_state'] = 'submitted_unknown'
        jobs.append((worker, submit))
    # Checkpoint BEFORE any terminal input. On interruption, resume only observes, never repeats it.
    records.save(state)
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {pool.submit(start_one, runtime, state, worker, submit): worker for worker, submit in jobs}
        for future in concurrent.futures.as_completed(futures):
            worker = futures[future]
            try:
                actual, seconds = future.result()
                worker.update(identity=actual, launch_state='ready', startup_seconds=seconds)
            except Exception:
                traceback.print_exc()
                print(f"{worker['role']} 尚未确认就绪；不重发启动命令。", file=sys.stderr)
                worker['last_error'] = traceback.format_exc()
                errors.append(worker['role'])
            records.save(state)
    if errors:
        raise FrameworkError('Incomplete workers: ' + ', '.join(errors))


def execute(runtime, state, append=None, adoptions=None):
    with locked(state['plan_dir']):
        state = records.load(state['plan_dir'])
        validate_main(runtime, state)
        for worker in state['workers']:
            launch.entry_command(state['plan_dir'], worker['role'])
        state['attempts'].append({'at': records.now(), 'deadline_seconds': round(runtime.deadline - runtime.started)})
        state['state'] = 'initializing'
        records.save(state)
        try:
            started = time.monotonic()
            split_workers(runtime, state, adoptions)
            timed(state, 'pane_split', started)
            prepare_launch_files(state, append or {})
            started = time.monotonic()
            start_workers(runtime, state)
            timed(state, 'parallel_native_start', started)
            validate_main(runtime, state)
            state['state'] = 'ready'
            state['peers']['configured'] = True
            state['live_validation'] = 'native_cli_ready; model_rule_observation_and_message_roundtrip_not_run'
            plan = Path(state['plan_dir']) / 'task_plan.md'
            text = plan.read_text(encoding='utf-8')
            old = '### Phase 1: 框架初始化\n- **Status:** in_progress'
            records.atomic_text(plan, text.replace(old, '### Phase 1: 框架初始化\n- **Status:** complete', 1))
            records.append_progress(state, '全部 worker 原生 CLI 就绪；通信未做模型回合演练。')
        except Exception:
            state['state'] = 'incomplete'
            state['last_error'] = traceback.format_exc()
            raise
        finally:
            timed(state, 'attempt_total', runtime.started, state['state'])
        return state


def assignments(values, label):
    result = {}
    for value in values:
        key, sep, item = value.partition('=')
        if not sep or not re.fullmatch(r'worker[1-9][0-9]*', key) or key in result or not item:
            raise ValueError(f'Invalid {label}: {value}')
        result[key] = item
    return result


def main():
    parser = records.Parser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('--workers', required=True, type=int)
    create.add_argument('--agent', action='append', default=[])
    create.add_argument('--main-agent', required=True)
    create.add_argument('--project', default=os.getcwd())
    create.add_argument('--base-instructions', action='append', default=[])
    create.add_argument('--deadline', type=float, default=180)
    resume = sub.add_parser('resume')
    resume.add_argument('--plan-dir', required=True)
    resume.add_argument('--deadline', type=float, default=180)
    resume.add_argument('--adopt-pane', action='append', default=[])
    args = parser.parse_args()
    runtime = Runtime(seconds=args.deadline)
    if args.command == 'create':
        kinds = records.resolve_agents(args.workers, args.agent)
        bases = assignments(args.base_instructions, 'base instruction file')
        if any(int(k[6:]) > args.workers for k in bases):
            raise ValueError('Base instruction role exceeds worker count')
        root, main_id, helper, version, caps, append = preflight(
            runtime, args.project, kinds, args.main_agent, bases)
        state = records.prepare(root, args.workers, args.agent, args.main_agent, runtime.pane, runtime.workspace)
        state['main'].update(identity=main_id, launch_state='existing')
        state['peers'].update(helper=helper, version=version, routing='explicit_addresses_no_lead_broadcast')
        for worker in state['workers']:
            worker['executable'] = caps[worker['agent']]['executable']
        # Persist reviewed append material BEFORE splitting so an interrupted create can resume exactly.
        for role, text in append.items():
            records.atomic_text(Path(state['plan_dir']) / 'rules' / f'{role}.base.txt', text)
        state['base_sha256'] = {role: records.digest(text) for role, text in append.items()}
        records.save(state)
        timed(state, 'preflight', runtime.started)
    else:
        state = records.load(args.plan_dir)
        check_peers(runtime, state['peers']['helper'])
        append = {}
        for role, expected in state['base_sha256'].items():
            path = Path(state['plan_dir']) / 'rules' / f'{role}.base.txt'
            text = path.read_text(encoding='utf-8')
            if path.is_symlink() or records.digest(text) != expected:
                raise FrameworkError('Reviewed base instructions changed')
            append[role] = text
    print(json.dumps({'plan_dir': state['plan_dir'], 'state': state['state']}, ensure_ascii=False), flush=True)
    state = execute(runtime, state, append, assignments(getattr(args, 'adopt_pane', []), 'pane adoption'))
    print(json.dumps({'plan_dir': state['plan_dir'], 'state': state['state'], 'workers': state['workers'],
                      'timings': state['timings'], 'main_action': 'Read the following main context in this turn'}, ensure_ascii=False, indent=2))
    print('\n' + records.context(state, 'main'))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        print('框架操作未完成；保留已有资源，按输出的原 PLAN_ID 恢复，不重新 create。', file=sys.stderr)
        sys.exit(1)
