"""Native launch adapters. Long prompt arguments never travel through terminal input."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from records import agent_kind

BINS = {"codex": "codex", "claude code": "claude", "omp": "omp"}
CODEX_SKIP_UPDATE_ARGS = ('-c', 'check_for_update_on_startup=false')


def native_arguments(kind, prompt_file):
    kind = agent_kind(kind)
    text = Path(prompt_file).read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("Empty native role prompt")
    if kind == "codex":
        return ["--no-daemon", "-c", "developer_instructions=" + json.dumps(text, ensure_ascii=False),
                *CODEX_SKIP_UPDATE_ARGS]
    return ["--append-system-prompt", text]


def claude_trust_selection(screen, project):
    """Recognize the exact workspace-trust dialog, never a generic Yes prompt."""
    flat = ' '.join(screen.split())
    required = ('Quick safety check: Is this a project you created or one you trust?',
                "Claude Code'll be able to read, edit, and execute files here.",
                'Enter to confirm', 'Esc to cancel')
    if screen.count('Accessing workspace:') != 1 or any(text not in flat for text in required):
        return None
    displayed = screen.split('Accessing workspace:', 1)[1].split('Quick safety check:', 1)[0]
    displayed = ''.join(line.strip() for line in displayed.splitlines())
    if not displayed.startswith(('/', '~/')) or Path(displayed).expanduser().resolve() != Path(project).resolve():
        return None
    options = []
    for line in screen.splitlines():
        match = re.fullmatch(r'\s*([❯›>])?\s*(No, exit|Yes, I trust this folder)\s*', line)
        if match:
            options.append((bool(match[1]), match[2]))
    if [label for _, label in options] != ['No, exit', 'Yes, I trust this folder']:
        return None
    if sum(selected for selected, _ in options) != 1:
        return None
    return 'yes' if options[1][0] else 'no'


def entry_command(plan_dir, role):
    entry = Path(__file__).resolve().with_name("agent_entry.py")
    command = shlex.join([sys.executable, str(entry), "--plan-dir", str(plan_dir), "--role", role])
    # macOS canonical input limits concern this short command, NOT execve's argument vector.
    if len(command.encode("utf-8")) > 900:
        raise ValueError("Launcher command exceeds conservative terminal input budget; use a shorter skill/project path")
    return command


def _contains_key(value, key):
    if isinstance(value, dict):
        return any((k == key and bool(v)) or _contains_key(v, key) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_key(v, key) for v in value)
    return False


def check_append_conflicts(kind, project, *, home=None, env=None, base_file=None):
    """Fail closed when an explicit native override could hide an existing append layer.

    A supplied base is an explicit, reviewed complete append layer, not an automatic merge
    of guessed config precedence. AGENTS.md and other native project rules stay native.
    """
    kind = agent_kind(kind)
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    project = Path(project).resolve()
    if base_file:
        path = Path(base_file).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("Base instructions must be a regular UTF-8 file")
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("Reviewed base instructions cannot be empty")
        return text
    conflicts = []
    if kind == "codex":
        codex_home = Path(env.get("CODEX_HOME", home / ".codex")).expanduser()
        paths = [codex_home / "config.toml", codex_home / "managed_config.toml",
                 Path("/etc/codex/config.toml"), Path("/etc/codex/managed_config.toml")]
        paths += [folder / ".codex/config.toml" for folder in [project, *project.parents]]
        # Profiles are overlays in the installed CLI. Conservatively check all available
        # user profiles rather than silently selecting an incomplete base instruction set.
        paths += list(codex_home.glob("*.config.toml"))
        for path in dict.fromkeys(paths):
            if path.is_file():
                config = tomllib.loads(path.read_text(encoding="utf-8"))
                if _contains_key(config, "developer_instructions"):
                    conflicts.append(str(path))
        if env.get("CODEX_PROFILE"):
            conflicts.append("CODEX_PROFILE is set")
    elif kind == "omp":
        bases = [home / ".omp/agent", home / ".agents", home / ".agent",
                 home / ".claude", home / ".codex", home / ".gemini"]
        if env.get("PI_CODING_AGENT_DIR"):
            bases.append(Path(env["PI_CODING_AGENT_DIR"]).expanduser())
        if env.get("OMP_PROFILE"):
            bases.append(home / ".omp/profiles" / env["OMP_PROFILE"] / "agent")
        if env.get("CLAUDE_CONFIG_DIR"):
            bases.append(Path(env["CLAUDE_CONFIG_DIR"]).expanduser())
        for folder in [project, *project.parents]:
            bases += [folder / name for name in (".omp", ".agents", ".agent", ".claude", ".codex", ".gemini")]
        for base in dict.fromkeys(bases):
            path = base / "APPEND_SYSTEM.md"
            if path.is_file() and path.read_text(encoding="utf-8").strip():
                conflicts.append(str(path))
        if env.get("PI_CONFIG_DIR"):
            conflicts.append("custom PI_CONFIG_DIR requires reviewed append instructions")
    if conflicts:
        raise ValueError("Existing append instructions may be overridden; supply a reviewed complete "
                         "--base-instructions workerN=/absolute/file before creating panes: " + "; ".join(conflicts))
    return ""


def capabilities(kind, run=subprocess.run):
    kind = agent_kind(kind)
    executable = shutil.which(BINS[kind])
    if not executable:
        raise ValueError(f"Required CLI not installed: {BINS[kind]}")
    result = run([executable, "--help"], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError(f"CLI help failed: {BINS[kind]}\n{result.stderr}")
    flag = "--no-daemon" if kind == "codex" else "--append-system-prompt"
    if flag not in result.stdout:
        raise ValueError(f"Installed {BINS[kind]} lacks required {flag}")
    return {"executable": executable, "native_flag": flag}
