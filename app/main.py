"""K8smate - Kubernetes cluster management dashboard."""

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import images, k8s, gitops

app = FastAPI(title="k8smate")

CONFIG_REPO = os.environ.get("K8SMATE_CONFIG_REPO", "/config")
YAML_DIRS = ["applications", "monitoring"]

# --- helpers ---


def _scan_yaml_files() -> list[Path]:
    """Find all YAML files in the config repo."""
    repo = Path(CONFIG_REPO)
    files = []
    for d in YAML_DIRS:
        dirpath = repo / d
        if dirpath.is_dir():
            files.extend(sorted(dirpath.glob("*.yaml")))
            files.extend(sorted(dirpath.glob("*.yml")))
    return files


def _parse_deployments() -> list[dict[str, Any]]:
    """Parse all deployable resources from YAML files."""
    workloads = []
    for filepath in _scan_yaml_files():
        with open(filepath) as f:
            docs = list(yaml.safe_load_all(f.read()))
        for doc in docs:
            if not doc or not isinstance(doc, dict):
                continue
            kind = doc.get("kind")
            if kind not in ("Deployment", "StatefulSet", "DaemonSet"):
                continue

            metadata = doc.get("metadata", {})
            annotations = metadata.get("annotations", {})
            spec = doc.get("spec", {}).get("template", {}).get("spec", {})

            containers = []
            for c in spec.get("containers", []) + spec.get("initContainers", []):
                img = c.get("image", "")
                name = c.get("name", "")
                registry, repository, tag, digest = images.parse_image_ref(img)
                original_tag = annotations.get(f"k8s-image-updater/{name}.original-tag")
                containers.append({
                    "name": name,
                    "image": img,
                    "registry": registry,
                    "repository": repository,
                    "currentDigest": digest,
                    "originalTag": original_tag or tag,
                })

            workloads.append({
                "kind": kind,
                "name": metadata.get("name"),
                "namespace": metadata.get("namespace"),
                "file": str(filepath.relative_to(CONFIG_REPO)),
                "containers": containers,
            })
    return workloads


# --- API ---


@app.get("/api/workloads")
def get_workloads():
    """List all workloads from the config repo YAML files."""
    return _parse_deployments()


@app.get("/api/pods")
def get_pods():
    """List running pods grouped by namespace."""
    try:
        pods = k8s.list_pods()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    grouped: dict[str, list] = {}
    for pod in pods:
        meta = pod.get("metadata", {})
        ns = meta.get("namespace", "default")
        status = pod.get("status", {})
        container_statuses = status.get("containerStatuses", [])
        containers = []
        for cs in container_statuses:
            containers.append({
                "name": cs.get("name"),
                "image": cs.get("image"),
                "imageID": cs.get("imageID", ""),
                "ready": cs.get("ready", False),
                "restartCount": cs.get("restartCount", 0),
            })
        grouped.setdefault(ns, []).append({
            "name": meta.get("name"),
            "namespace": ns,
            "phase": status.get("phase"),
            "containers": containers,
            "nodeName": pod.get("spec", {}).get("nodeName"),
        })
    return grouped


@app.get("/api/pods/{namespace}/{name}/describe")
def pod_describe(namespace: str, name: str):
    try:
        return {"output": k8s.describe_pod(namespace, name)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pods/{namespace}/{name}/logs")
def pod_logs(namespace: str, name: str, tail: int = 200, container: str | None = None):
    try:
        return {"output": k8s.get_pod_logs(namespace, name, tail_lines=tail, container=container)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CheckTagRequest(BaseModel):
    registry: str
    repository: str
    tag: str
    currentDigest: str | None = None


@app.post("/api/check-tag")
def check_tag(req: CheckTagRequest):
    """Resolve a tag to its latest digest and compare with current."""
    latest_digest = images.resolve_tag_to_digest(req.registry, req.repository, req.tag)
    if not latest_digest:
        raise HTTPException(status_code=502, detail=f"Could not resolve {req.registry}/{req.repository}:{req.tag}")
    up_to_date = req.currentDigest == latest_digest if req.currentDigest else False
    return {
        "latestDigest": latest_digest,
        "upToDate": up_to_date,
    }


class UpgradeRequest(BaseModel):
    file: str
    containerName: str
    registry: str
    repository: str
    tag: str


@app.post("/api/upgrade")
def upgrade_image(req: UpgradeRequest):
    """Upgrade a container image: update YAML, commit, push, apply."""
    repo = Path(CONFIG_REPO)
    filepath = repo / req.file

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file}")

    # Pull latest git changes first
    try:
        gitops.pull(str(repo))
    except Exception:
        pass  # may fail if no remote or already up to date

    # Resolve tag to latest digest
    new_digest = images.resolve_tag_to_digest(req.registry, req.repository, req.tag)
    if not new_digest:
        raise HTTPException(status_code=502, detail=f"Could not resolve {req.registry}/{req.repository}:{req.tag}")

    new_image = images.format_image_ref(req.registry, req.repository, new_digest)

    # Update the YAML file
    with open(filepath) as f:
        docs = list(yaml.safe_load_all(f.read()))

    modified = False
    for doc in docs:
        if not doc or not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        if kind not in ("Deployment", "StatefulSet", "DaemonSet"):
            continue
        spec = doc.get("spec", {}).get("template", {}).get("spec", {})
        for c in spec.get("containers", []) + spec.get("initContainers", []):
            if c.get("name") == req.containerName:
                old_image = c.get("image", "")
                if old_image != new_image:
                    c["image"] = new_image
                    modified = True

    if not modified:
        return {"status": "already_up_to_date", "message": "Image is already at the latest digest"}

    with open(filepath, 'w') as f:
        yaml.dump_all(docs, f, default_flow_style=False, sort_keys=False)

    # Git add and commit
    try:
        gitops.add_and_commit(
            str(repo),
            [req.file],
            f"k8smate: upgrade {req.containerName} to {new_digest[:19]}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Git commit failed: {e}")

    # Apply to cluster
    try:
        apply_output = k8s.apply_file(str(filepath))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"kubectl apply failed: {e}")

    return {"status": "upgraded", "newImage": new_image, "applyOutput": apply_output}


# --- static files ---

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def index():
    return FileResponse(str(static_dir / "index.html"))
