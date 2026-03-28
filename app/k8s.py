"""Kubernetes operations via kubectl and the Python client."""

import json
import subprocess
from typing import Any


def _run_kubectl(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ['kubectl', *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def list_pods() -> list[dict[str, Any]]:
    """List all pods across all namespaces."""
    out = _run_kubectl('get', 'pods', '--all-namespaces', '-o', 'json')
    data = json.loads(out)
    return data.get('items', [])


def describe_pod(namespace: str, name: str) -> str:
    """Get pod description."""
    return _run_kubectl('describe', 'pod', name, '-n', namespace, timeout=15)


def get_pod_logs(namespace: str, name: str, tail_lines: int = 200, container: str | None = None) -> str:
    """Get pod logs."""
    args = ['logs', name, '-n', namespace, f'--tail={tail_lines}']
    if container:
        args.extend(['-c', container])
    try:
        return _run_kubectl(*args, timeout=15)
    except RuntimeError:
        # Pod might have multiple containers - try --all-containers
        if not container:
            args_all = ['logs', name, '-n', namespace, f'--tail={tail_lines}', '--all-containers=true']
            return _run_kubectl(*args_all, timeout=15)
        raise


def apply_file(path: str) -> str:
    """kubectl apply a file."""
    return _run_kubectl('apply', '-f', path, timeout=30)
