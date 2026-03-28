"""Image tag resolution - extracted from update-image-tags.py."""

import os
from typing import Optional, Tuple

import requests


def parse_image_ref(image: str) -> Tuple[str, str, Optional[str], Optional[str]]:
    """Parse image reference into (registry, repository, tag, digest)."""
    digest = None
    if '@' in image:
        image, digest = image.split('@', 1)

    tag = None
    if ':' in image and '/' in image:
        last_slash = image.rfind('/')
        if ':' in image[last_slash:]:
            image, tag = image.rsplit(':', 1)
    elif ':' in image:
        image, tag = image.rsplit(':', 1)

    if '/' not in image:
        registry = 'docker.io'
        repository = f'library/{image}'
    elif '.' not in image.split('/')[0] and ':' not in image.split('/')[0]:
        registry = 'docker.io'
        repository = image
    else:
        parts = image.split('/', 1)
        registry = parts[0]
        repository = parts[1]

    if tag is None and digest is None:
        tag = 'latest'

    return registry, repository, tag, digest


def format_image_ref(registry: str, repository: str, digest: str) -> str:
    """Build a full image reference from components."""
    if registry == 'docker.io':
        if repository.startswith('library/'):
            return f"{repository.replace('library/', '')}@{digest}"
        return f"{repository}@{digest}"
    return f"{registry}/{repository}@{digest}"


_session = requests.Session()
_session.headers.update({'User-Agent': 'k8smate/1.0'})


def _get_auth_token(registry: str, repository: str) -> Optional[str]:
    if registry == 'docker.io':
        try:
            resp = _session.get(
                'https://auth.docker.io/token',
                params={'service': 'registry.docker.io', 'scope': f'repository:{repository}:pull'},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get('token')
        except Exception:
            pass
    elif registry == 'ghcr.io':
        github_token = os.environ.get('GHCR_TOKEN') or os.environ.get('GITHUB_TOKEN')
        try:
            kwargs = {}
            if github_token:
                kwargs['auth'] = (github_token, '')
            resp = _session.get(
                'https://ghcr.io/token',
                params={'scope': f'repository:{repository}:pull'},
                timeout=10,
                **kwargs,
            )
            if resp.status_code == 200:
                return resp.json().get('token')
        except Exception:
            pass
    return None


def resolve_tag_to_digest(registry: str, repository: str, tag: str) -> Optional[str]:
    """Resolve an image tag to its digest via registry API."""
    if registry == 'docker.io':
        registry_url = 'https://registry-1.docker.io'
    else:
        registry_url = f'https://{registry}'

    manifest_url = f'{registry_url}/v2/{repository}/manifests/{tag}'
    token = _get_auth_token(registry, repository)

    headers = {
        'Accept': 'application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json'
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'

    try:
        resp = _session.get(manifest_url, headers=headers, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get('Content-Type', '')
        digest = resp.headers.get('Docker-Content-Digest')

        if 'manifest.list' in content_type or 'image.index' in content_type:
            return digest

        if digest:
            return digest

        # Retry with single-arch accept header
        headers['Accept'] = 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'
        resp = _session.get(manifest_url, headers=headers, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            return resp.headers.get('Docker-Content-Digest')

    except Exception:
        pass

    return None
