#!/usr/bin/env python3
"""Single-task runner for Claude Code on CyberGym.

Sets up the workspace, invokes `claude -p` with the prompt, and captures
the stream-json trajectory.

Usage:
    uv run python3 examples/agents/claude_code/run.py \
        --task_id arvo:8933 --model opus
"""

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from simple_parsing import ArgumentParser, flag
from simple_parsing.helpers.serialization.serializable import FrozenSerializable

from cybergym.task.gen_task import generate_task
from cybergym.task.types import TaskConfig, TaskDifficulty

logger = logging.getLogger(__name__)
SCRIPT_DIR = Path(__file__).parent


@dataclass
class ClaudeCodeArgs:
    model: str = "opus"
    """Model alias (opus, sonnet, haiku) or full name."""

    log_dir: Path = Path("logs")
    """Directory to store logs and trajectories."""

    tmp_dir: Path = Path("tmp")
    """Temporary directory for workspace setup."""

    timeout: int = 1800
    """Wall-clock timeout in seconds."""

    max_budget_usd: float = 5.0
    """Per-task API budget cap in USD."""

    effort: str = "high"
    """Effort level: low, medium, high, max."""

    remove_tmp: bool = flag(default=True)
    """Remove temporary workspace after running."""

    prompt_file: Path | None = None
    """Override the default prompt file."""


@dataclass
class TaskArgs:
    task_id: str
    """Task ID (e.g., arvo:8933)."""

    data_dir: Path = Path("/data/cybergym_data/cybergym-benchmark-data/data")
    """Directory containing CyberGym benchmark data."""

    server: str = "http://172.17.0.1:8666"
    """CyberGym evaluation server URL."""

    difficulty: str = "level1"
    """Task difficulty: level0, level1, level2, level3."""


def run_with_configs(claude_args: ClaudeCodeArgs, task_args: TaskArgs) -> str | None:
    """Set up workspace, run Claude Code, capture trajectory. Returns agent_id."""

    agent_id = uuid4().hex
    task_norm = task_args.task_id.replace(":", "_")
    sub_dir = f"{task_norm}-{agent_id}"

    workspace_dir = claude_args.tmp_dir / sub_dir / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    log_dir = claude_args.log_dir / sub_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate task workspace
    difficulty = TaskDifficulty(task_args.difficulty)
    task = generate_task(TaskConfig(
        task_id=task_args.task_id,
        out_dir=workspace_dir,
        data_dir=task_args.data_dir,
        server=task_args.server,
        difficulty=difficulty,
        agent_id=agent_id,
    ))
    logger.info(f"Generated workspace: {workspace_dir}")

    # 2. Extract repo-vul.tar.gz → src-vul/
    tarball = workspace_dir / "repo-vul.tar.gz"
    if tarball.exists():
        src_vul = workspace_dir / "src-vul"
        src_vul.mkdir(exist_ok=True)
        subprocess.run(
            ["tar", "xzf", str(tarball), "-C", str(src_vul)],
            check=True, capture_output=True,
        )
        logger.info(f"Extracted source to {src_vul}")

    # 3. Save args.json
    args_data = {
        "agent": f"claude-code:{claude_args.model}",
        "task": {
            "task_id": task_args.task_id,
            "agent_id": agent_id,
            "checksum": task.checksum,
            "server": task_args.server,
            "difficulty": task_args.difficulty,
        },
        "agent_args": {
            "model": claude_args.model,
            "timeout": claude_args.timeout,
            "max_budget_usd": claude_args.max_budget_usd,
            "effort": claude_args.effort,
        },
    }
    with open(log_dir / "args.json", "w") as f:
        json.dump(args_data, f, indent=2)

    # 4. Read prompt
    prompt_path = claude_args.prompt_file or (SCRIPT_DIR / "prompt.txt")
    prompt_text = prompt_path.read_text()

    # 5. Run claude CLI
    trajectory_path = log_dir / "trajectory.jsonl"
    claude_bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")

    cmd = [
        "timeout", str(claude_args.timeout),
        claude_bin,
        "-p", prompt_text,
        "--output-format", "stream-json",
        "--verbose",
        "--model", claude_args.model,
        "--allowedTools", "Bash,Read,Write,Edit",
        "--permission-mode", "bypassPermissions",
        "--max-budget-usd", str(claude_args.max_budget_usd),
        "--no-session-persistence",
        "--effort", claude_args.effort,
    ]

    logger.info(f"Running Claude Code: model={claude_args.model}, timeout={claude_args.timeout}s")

    with open(trajectory_path, "w") as traj_file:
        result = subprocess.run(
            cmd,
            cwd=str(workspace_dir),
            stdout=traj_file,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=claude_args.timeout + 60,  # extra buffer for timeout command
        )

    logger.info(f"Claude Code exited with code {result.returncode}")

    # 6. Cleanup
    if claude_args.remove_tmp:
        shutil.rmtree(claude_args.tmp_dir / sub_dir, ignore_errors=True)

    return agent_id


def main(raw_args=None):
    parser = ArgumentParser()
    parser.add_arguments(ClaudeCodeArgs, dest="claude_args")
    parser.add_arguments(TaskArgs, dest="task_args")
    args = parser.parse_args(raw_args)
    run_with_configs(args.claude_args, args.task_args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    main()
