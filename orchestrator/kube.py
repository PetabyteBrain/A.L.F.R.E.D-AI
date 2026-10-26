"""Kubernetes cluster status for the Systems page.

Alfred now runs as a pod in the K3s cluster (not Docker-on-hosts), so the Systems page reflects the
cluster: each node's readiness and each service Deployment's availability, read from the in-cluster
Kubernetes API with Alfred's mounted ServiceAccount token (read-only RBAC: nodes + deployments).

Display only — cluster alerting is handled out-of-band by Prometheus/Alertmanager. This just keeps the
`health` table (the Systems page) current. Standard library only.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

from . import db

log = logging.getLogger(__name__)

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
TOKEN_FILE = SA_DIR / "token"
CA_FILE = SA_DIR / "ca.crt"
POLL_SECONDS = 300
# The namespaces whose Deployments are "services" worth showing on the Systems page.
WATCH_NS = ("apps", "web", "data", "games")


def enabled() -> bool:
    """True when running inside the cluster with a ServiceAccount token mounted."""
    return TOKEN_FILE.is_file() and bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def _api(path: str, timeout: float = 10) -> dict:
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    token = TOKEN_FILE.read_text().strip()
    ctx = ssl.create_default_context(cafile=str(CA_FILE))
    req = urllib.request.Request(f"https://{host}:{port}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode())


def _node_roles(labels: dict) -> str:
    roles = [k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/")]
    return "control-plane" if "control-plane" in roles or "master" in roles else "worker"


def _node_summary(node: dict) -> tuple[bool, str]:
    conds = {c["type"]: c for c in node.get("status", {}).get("conditions", [])}
    ready = conds.get("Ready", {}).get("status") == "True"
    role = _node_roles(node.get("metadata", {}).get("labels", {}))
    arch = node.get("status", {}).get("nodeInfo", {}).get("architecture", "")
    pressures = [t.replace("Pressure", " pressure").lower()
                 for t in ("MemoryPressure", "DiskPressure", "PIDPressure")
                 if conds.get(t, {}).get("status") == "True"]
    state = "Ready" if ready else "NotReady"
    extra = (" · " + ", ".join(pressures)) if pressures else ""
    return ready and not pressures, f"{state} · {role}{(' · ' + arch) if arch else ''}{extra}"


def refresh() -> int:
    """Update the `health` table with one row per node and per watched Deployment. Returns rows written."""
    if not enabled():
        return 0
    written = 0
    # --- nodes ---
    try:
        for n in _api("/api/v1/nodes").get("items", []):
            name = n["metadata"]["name"]
            ok, detail = _node_summary(n)
            db.set_health(f"node {name}", ok, detail)
            written += 1
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        log.warning("kube: node list failed: %s", e)
    # --- service deployments (one row per Deployment in the watched namespaces) ---
    for ns in WATCH_NS:
        try:
            deps = _api(f"/apis/apps/v1/namespaces/{ns}/deployments").get("items", [])
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.warning("kube: deployment list for ns %s failed: %s", ns, e)
            continue
        for d in deps:
            name = d["metadata"]["name"]
            spec = d.get("spec", {}).get("replicas", 1) or 0
            avail = d.get("status", {}).get("availableReplicas", 0) or 0
            ok = avail >= spec and spec > 0
            db.set_health(f"svc {ns}/{name}", ok, f"{ns} · {avail}/{spec} ready")
            written += 1
    return written


async def background_loop() -> None:
    import asyncio
    if not enabled():
        log.info("kube: not running in-cluster (no ServiceAccount token) — Systems cluster view disabled")
        return
    await asyncio.sleep(20)          # let the pod settle
    while True:
        try:
            await asyncio.to_thread(refresh)
        except Exception:            # noqa: BLE001
            log.exception("kube: refresh iteration failed")
        await asyncio.sleep(POLL_SECONDS)
