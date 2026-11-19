#!/usr/bin/env python3
"""Address peers by recorded role and verify the live pane before sending."""

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from uuid import uuid4

import records
from create_framework import FrameworkError, Runtime, identity, verify_identity


def peer_run(argv, timeout):
    env = os.environ.copy()
    env.pop('DWP_PLAN', None)
    env.pop('DWP_TASK', None)
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)


def member(state, role):
    result = next((m for m in [state['main'], *state['workers']] if m['role'] == role), None)
    if not result or not result['identity']:
        raise FrameworkError('Unknown or unverified role: ' + role)
    return result


def check_live(runtime, state, target):
    actual = identity(runtime, runtime.agent(target['pane_id']), target['agent'], state['project_root'],
                      pid=target['identity']['pid'])
    verify_identity(target['identity'], actual)


def sender_flags(runtime, state, role):
    sender = member(state, role)
    if runtime.pane != sender['pane_id'] or runtime.workspace != state['workspace_id']:
        raise FrameworkError('Sender role differs from actual calling pane')
    check_live(runtime, state, sender)
    return ['--pane', sender['pane_id'], '--scope', 'local:' + state['workspace_id']]


def classify_result(result):
    # Upstream uses nonzero exits for valid NOTICE/NEVER/NONE verdicts. Read decision, not exit alone.
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get('decision') not in ('answer', 'notice', 'never', 'none'):
        raise FrameworkError(f'Missing/unknown peer decision: {result.stdout}\n{result.stderr}')
    return value


def check(runtime, state, role, message, run=peer_run):
    message = Path(message).resolve(strict=True)
    incoming = message.read_text(encoding='utf-8')
    # Our outgoing bodies always begin with this exact binding. Validate before check writes protocol state.
    if not incoming.startswith(f"PLAN_ID={state['plan_id']}\n"):
        raise FrameworkError('Incoming PLAN_ID differs from this framework or is missing')
    flags = sender_flags(runtime, state, role)
    result = run([state['peers']['helper'], 'check', '--json', *flags, str(message)],
                 timeout=min(15, runtime.remaining()))
    verdict = classify_result(result)
    if verdict['decision'] in ('answer', 'notice'):
        # A workspace scope can contain other frameworks. Only this plan's recorded members are trusted senders.
        source = next((m for m in [state['main'], *state['workers']]
                       if 'local:' + str(m['pane_id']) == verdict.get('from')), None)
        if source is None:
            raise FrameworkError('Peer message sender is outside this framework')
        check_live(runtime, state, source)
    return verdict


def send(runtime, state, role, to_role, verb, text, run=peer_run, *, grant=None, message=None):
    if verb not in ('ask', 'notify', 'reply') or not text.strip():
        raise ValueError('Expected a nonempty ask, notify or reply')
    flags = sender_flags(runtime, state, role)
    target = member(state, to_role)
    if role == to_role:
        raise FrameworkError('Cannot message the same role')
    check_live(runtime, state, target)
    address = 'local:' + target['pane_id']
    body = f"PLAN_ID={state['plan_id']}\n{text}"
    argv = [state['peers']['helper'], verb, *flags]
    if verb in ('notify', 'reply'):
        argv += ['--wait-blocked', '0']
    if verb == 'reply':
        if not grant or grant.get('decision') != 'answer' or grant.get('from') != address or not message:
            raise FrameworkError('Reply requires the original received ask and an answer grant')
        argv += ['--message', str(message), address, grant['id'], body]
    else:
        argv += [address, body]
    outbox = Path(state['plan_dir']) / 'workers' / f'{role}.outgoing-{uuid4().hex}.json'
    delivery = {'at': records.now(), 'from_role': role, 'to_role': to_role, 'verb': verb,
                'body': body, 'state': 'delivery_unknown', 'automatic_replay': False}
    records.atomic_text(outbox, json.dumps(delivery, ensure_ascii=False, indent=2) + '\n')
    try:
        result = run(argv, timeout=min(30, runtime.remaining()))
        if result.returncode:
            raise FrameworkError(f'Peer delivery not confirmed ({result.returncode}):\n{result.stdout}\n{result.stderr}')
        delivery.update(state='submitted', helper_output=result.stdout.strip(), processed_by_receiver='unverified')
    except Exception:
        delivery['error'] = traceback.format_exc()
        raise
    finally:
        records.atomic_text(outbox, json.dumps(delivery, ensure_ascii=False, indent=2) + '\n')
    return {**delivery, 'record': str(outbox)}


def main():
    parser = records.Parser(description=__doc__)
    parser.add_argument('--plan-dir', required=True)
    parser.add_argument('--role', required=True)
    sub = parser.add_subparsers(dest='verb', required=True)
    for verb in ('ask', 'notify', 'reply', 'check'):
        cmd = sub.add_parser(verb)
        if verb in ('ask', 'notify'):
            cmd.add_argument('--to', required=True)
        if verb != 'check':
            text_args = cmd.add_mutually_exclusive_group(required=True)
            text_args.add_argument('--text')
            text_args.add_argument('--text-file')
        if verb in ('reply', 'check'):
            cmd.add_argument('--message', required=True, help='File containing the complete received protocol message')
    args = parser.parse_args()
    runtime = Runtime(seconds=60)
    state = records.load(args.plan_dir)
    grant = None
    if args.verb in ('reply', 'check'):
        args.message = str(Path(args.message).resolve(strict=True))
        grant = check(runtime, state, args.role, args.message)
    if args.verb == 'check':
        print(json.dumps(grant, ensure_ascii=False, indent=2))
        return
    text = Path(args.text_file).read_text(encoding='utf-8') if args.text_file else args.text
    to_role = args.to if args.verb != 'reply' else next((m['role'] for m in [state['main'], *state['workers']]
        if 'local:' + str(m['pane_id']) == grant.get('from')), None)
    if args.verb == 'reply' and grant.get('decision') != 'answer':
        raise FrameworkError('This message does not authorize a reply')
    print(json.dumps(send(runtime, state, args.role, to_role, args.verb, text,
                          grant=grant, message=getattr(args, 'message', None)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        print('通信未完成；保留发送记录，先核验对端与投递状态，不盲目重发。', file=sys.stderr)
        sys.exit(1)
