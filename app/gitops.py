"""Git operations for the config repository."""

import subprocess
from pathlib import Path


def _run_git(*args: str, cwd: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ['git', *args],
        capture_output=True, text=True, timeout=timeout, cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def pull(repo_path: str) -> str:
    """Pull latest changes."""
    return _run_git('pull', '--ff-only', cwd=repo_path)


def add_and_commit(repo_path: str, files: list[str], message: str) -> str:
    """Stage files and commit."""
    for f in files:
        _run_git('add', f, cwd=repo_path)
    return _run_git('commit', '-m', message, cwd=repo_path)
