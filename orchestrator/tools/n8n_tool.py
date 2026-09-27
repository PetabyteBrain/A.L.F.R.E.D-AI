"""n8n client: full public REST API (/api/v1, X-N8N-API-KEY) + webhook triggers."""
from __future__ import annotations

import logging
import secrets
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger(__name__)

# Fields n8n accepts on create/update; anything else (id, active, tags, ...) is rejected.
_WRITABLE = ("name", "nodes", "connections", "settings", "staticData")


class N8nError(RuntimeError):
    pass


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    if not settings.n8n_enabled:
        raise N8nError("n8n is not configured (N8N_BASE_URL / N8N_API_KEY missing).")
    return httpx.AsyncClient(
        base_url=settings.n8n_base_url,
        headers={"X-N8N-API-KEY": settings.n8n_api_key, "Accept": "application/json"},
        timeout=timeout,
    )


async def _api(method: str, path: str, **kw) -> Any:
    async with _client() as c:
        r = await c.request(method, f"/api/v1{path}", **kw)
    if r.status_code >= 400:
        raise N8nError(f"n8n {method} {path} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {"ok": True}


def _writable(workflow: dict) -> dict:
    body = {k: workflow[k] for k in _WRITABLE if k in workflow}
    body.setdefault("settings", {})
    body.setdefault("connections", {})
    return body


async def health() -> tuple[bool, str]:
    try:
        data = await _api("GET", "/workflows", params={"limit": 1})
        return True, f"reachable ({len(data.get('data', []))}+ workflows)"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


async def list_active_workflows() -> list[dict]:
    data = await _api("GET", "/workflows", params={"active": "true", "limit": 100})
    return data.get("data", [])


async def run_action(action: str, args: dict) -> Any:
    if action == "list_workflows":
        params: dict[str, Any] = {"limit": 100}
        if "active" in args:
            params["active"] = str(bool(args["active"])).lower()
        data = await _api("GET", "/workflows", params=params)
        return [
            {
                "id": w.get("id"),
                "name": w.get("name"),
                "active": w.get("active"),
                "tags": [t.get("name") for t in w.get("tags", [])],
                "updatedAt": w.get("updatedAt"),
            }
            for w in data.get("data", [])
        ]
    if action == "get_workflow":
        return await _api("GET", f"/workflows/{args['workflow_id']}")
    if action == "create_workflow":
        created = await _api("POST", "/workflows", json=_writable(args["workflow"]))
        return {"id": created.get("id"), "name": created.get("name"), "active": created.get("active")}
    if action == "update_workflow":
        updated = await _api("PUT", f"/workflows/{args['workflow_id']}", json=_writable(args["workflow"]))
        return {"id": updated.get("id"), "name": updated.get("name"), "active": updated.get("active")}
    if action == "set_active":
        verb = "activate" if args["active"] else "deactivate"
        res = await _api("POST", f"/workflows/{args['workflow_id']}/{verb}")
        return {"id": res.get("id"), "active": res.get("active")}
    if action == "delete_workflow":
        if not args.get("confirmed_by_user"):
            raise N8nError("Refused: ask the user to explicitly confirm deleting this workflow first.")
        res = await _api("DELETE", f"/workflows/{args['workflow_id']}")
        return {"deleted": res.get("id", args["workflow_id"])}
    if action == "list_executions":
        params = {"limit": min(int(args.get("limit") or 10), 50)}
        if args.get("workflow_id"):
            params["workflowId"] = args["workflow_id"]
        if args.get("status"):
            params["status"] = args["status"]
        data = await _api("GET", "/executions", params=params)
        return [
            {k: e.get(k) for k in ("id", "workflowId", "status", "mode", "startedAt", "stoppedAt")}
            for e in data.get("data", [])
        ]
    if action == "get_execution":
        return await _api("GET", f"/executions/{args['execution_id']}", params={"includeData": "true"})
    if action == "trigger_webhook":
        prefix = "webhook-test" if args.get("test") else "webhook"
        return await post_webhook(args["path"], args.get("payload") or {}, prefix=prefix)
    raise N8nError(f"Unknown n8n action '{action}'")


async def post_webhook(path: str, payload: dict, *, prefix: str = "webhook", timeout: float = 60.0) -> Any:
    if not settings.n8n_base_url:
        raise N8nError("N8N_BASE_URL is not set.")
    url = f"{settings.n8n_base_url}/{prefix}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=payload)
    if r.status_code >= 400:
        raise N8nError(f"webhook {path} -> {r.status_code}: {r.text[:500]}")
    try:
        return r.json()
    except ValueError:
        return {"response": r.text[:4000]}


def new_correlation_id() -> str:
    return secrets.token_urlsafe(12)
