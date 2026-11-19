#!/usr/bin/env python3
"""Exec one native CLI with role instructions; never send a startup model prompt."""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import launch
import records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", required=True)
    parser.add_argument("--role", required=True)
    args = parser.parse_args()
    state = records.load(args.plan_dir)
    worker = next((w for w in state["workers"] if w["role"] == args.role), None)
    if worker is None:
        raise ValueError("Entry only starts a recorded worker, never the existing main")
    if os.environ.get("HERDR_ENV") != "1" or os.environ.get("HERDR_PANE_ID") != worker["pane_id"]:
        raise ValueError("Worker launch does not match the actual Herdr pane environment")
    if os.environ.get("HERDR_WORKSPACE_ID") != state["workspace_id"]:
        raise ValueError("Worker workspace mismatch")
    if Path.cwd().resolve() != Path(state["project_root"]):
        raise ValueError("Worker cwd mismatch")
    prompt = Path(state["plan_dir"]) / "rules" / f"{args.role}.launch.txt"
    if prompt.is_symlink() or records.digest(prompt.read_text(encoding="utf-8")) != worker["launch_hash"]:
        raise ValueError("Native launch instructions changed after preparation")
    executable = worker["executable"]
    if not Path(executable).is_file():
        raise ValueError("Recorded CLI executable is missing")
    env = os.environ.copy()
    env.update(PLAN_ID=state["plan_id"], PWF_PLAN_ROOT=state["project_root"],
               HERDR_FRAMEWORK_ROLE=args.role, HERDR_FRAMEWORK_DIR=state["plan_dir"],
               HERDR_PEERS_SCOPE="local:" + state["workspace_id"])
    # These are long-lived lanes, not depth-limited one-shot delegates.
    env.pop("HERDR_PEERS_DEPTH", None)
    # This is a PWF framework. Do not append peer records to an unrelated inherited DWP plan.
    env.pop("DWP_PLAN", None)
    env.pop("DWP_TASK", None)
    argv = [executable, *launch.native_arguments(worker["agent"], prompt)]
    receipt_path = Path(state["plan_dir"]) / "workers" / f"{args.role}.launch.json"
    receipt = {"role": args.role, "pane_id": worker["pane_id"], "pid": os.getpid(),
               "instructions_sha256": worker["launch_hash"], "at": records.now(),
               "stage": "exec_requested", "assurance": "native_arguments_prepared_not_model_observed"}
    records.atomic_text(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    try:
        os.execve(executable, argv, env)
    except Exception:
        receipt["stage"] = "exec_failed"
        records.atomic_text(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("Worker 启动未完成；保留当前 pane，按原 PLAN_ID 检查后恢复。", file=sys.stderr)
        sys.exit(1)
