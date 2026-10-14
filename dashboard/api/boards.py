"""Boards tab (roadmap #59): boards, items, views, comments, files, task requests and teams.
Every rule lives in orchestrator/boards.py (levels per board) and orchestrator/permissions.py (teams)."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import boards, db, permissions
from orchestrator.identity.registry import Person

from .routes import current_person, requires
from .security import revoke_user_sessions

router = APIRouter(prefix="/api/boards", dependencies=[Depends(requires("boards.use"))])
teams_router = APIRouter(prefix="/api/teams")
BoardId = str


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BoardBody(Strict):
    name: str | None = Field(default=None, max_length=60)
    key: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=300)
    template: Literal["agile", "simple", "projects"] = "agile"
    sharing: list[dict] | None = None
    archived: bool | None = None


class AccessBody(Strict):
    rows: list[dict] = Field(max_length=60)


class TypeBody(Strict):
    name: str | None = Field(default=None, max_length=30)
    color: str | None = Field(default=None, max_length=7)
    archived: bool | None = None


class StateBody(Strict):
    name: str | None = Field(default=None, max_length=30)
    category: Literal["todo", "doing", "done"] | None = None
    wip_limit: int | None = Field(default=None, ge=0, le=999)
    archived: bool | None = None


class OrderBody(Strict):
    ids: list[int] = Field(max_length=100)


class ItemBody(Strict):
    fields: dict = Field(default_factory=dict)


class MoveBody(Strict):
    state_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=-1)          # -1 = keep, 0 = no team
    assignee_id: int | None = Field(default=-1)      # -1 = keep, 0 = nobody
    index: int | None = Field(default=None, ge=0)


class Flag(Strict):
    archived: bool


class CommentBody(Strict):
    body: str = Field(min_length=1, max_length=5000)


class FileBody(Strict):
    path: str = Field(min_length=1, max_length=400)


class RequestBody(Strict):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    team_id: int | None = Field(default=None, ge=1)
    type_id: int | None = Field(default=None, ge=1)
    priority: int = Field(default=2, ge=1, le=3)
    due_date: str | None = Field(default=None, max_length=10)


class Decision(Strict):
    accept: bool
    note: str = Field(default="", max_length=300)
    assignee_id: int | None = Field(default=None, ge=1)


class TeamBody(Strict):
    name: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=200)
    manager_id: int | None = Field(default=None, ge=0)
    color: str | None = Field(default=None, max_length=7)


class Members(Strict):
    user_ids: list[int] = Field(max_length=500)


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (boards.BoardError, permissions.PermissionError_) as e:
        raise HTTPException(status_code=400, detail=str(e))


def _int(board_id: str) -> int:
    if not board_id.isdigit():
        raise HTTPException(status_code=400, detail="That can't be changed here.")
    return int(board_id)


# --- boards --------------------------------------------------------------------------------------------

@router.get("")
async def list_boards(person: Person = Depends(current_person), archived: bool = False) -> dict:
    return {"boards": boards.boards_for(person, include_archived=archived),
            "can": {"create": person.can("boards.create"), "admin": person.can("boards.admin"),
                    "teams": person.can("teams.manage") or person.can("roles.manage")},
            "templates": {k: v["label"] for k, v in boards.TEMPLATES.items()},
            "requests_to_decide": len(boards.requests_for(person, to_decide=True)),
            "mine": len(boards.mine(person, include_deals=False))}


@router.post("")
async def create_board(body: BoardBody, person: Person = Depends(current_person)) -> dict:
    return _run(boards.create_board, person, name=body.name or "", key=body.key or "", description=body.description or "",
                template=body.template, sharing=body.sharing)


@router.get("/mine")
async def my_items(person: Person = Depends(current_person), teams: bool = False) -> list[dict]:
    return boards.mine(person, teams=teams)


@router.get("/requests")
async def requests(person: Person = Depends(current_person),
                   status: Literal["pending", "accepted", "declined", "withdrawn", "all"] = "pending") -> list[dict]:
    return boards.requests_for(person, status=status)


@router.post("/requests/{request_id}")
async def decide(body: Decision, request_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return await boards.decide_request(person, request_id, body.accept, body.note,
                                           **({"assignee_id": body.assignee_id} if body.assignee_id else {}))
    except boards.BoardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/requests/{request_id}")
async def withdraw(request_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(boards.withdraw_request, person, request_id)


@router.get("/linked")
async def linked(person: Person = Depends(current_person), company_id: int | None = Query(default=None, ge=1),
                 deal_id: int | None = Query(default=None, ge=1), user_id: int | None = Query(default=None, ge=1)) -> list[dict]:
    return boards.linked_items(person, company_id=company_id, deal_id=deal_id, user_id=user_id)


@router.get("/items/{item_id}")
async def get_item(item_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(boards.get_item, person, item_id)


@router.patch("/items/{item_id}")
async def update_item(body: ItemBody, item_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(boards.save_item, person, body.fields, item_id)


@router.post("/items/{item_id}/archive")
async def archive_item(body: Flag, item_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(boards.set_archived, person, item_id, body.archived)


@router.post("/items/{item_id}/comments")
async def add_comment(body: CommentBody, item_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(boards.comment, person, item_id, body.body)


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(boards.delete_comment, person, comment_id)


@router.post("/items/{item_id}/files")
async def attach(body: FileBody, item_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return await asyncio.to_thread(boards.attach_file, person, item_id, body.path)
    except boards.BoardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/files/{file_id}")
async def detach(file_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    return _run(boards.detach_file, person, file_id)


@router.get("/{board_id}")
async def get_board(board_id: BoardId = Path(max_length=10), person: Person = Depends(current_person)) -> dict:
    return _run(boards.get_board, person, board_id)


@router.patch("/{board_id}")
async def update_board(body: BoardBody, board_id: BoardId = Path(max_length=10),
                       person: Person = Depends(current_person)) -> dict:
    return _run(boards.update_board, person, _int(board_id), name=body.name, key=body.key,
                description=body.description, archived=body.archived)


@router.put("/{board_id}/access")
async def set_access(body: AccessBody, board_id: BoardId = Path(max_length=10),
                     person: Person = Depends(current_person)) -> dict:
    return _run(boards.set_access, person, _int(board_id), body.rows)


@router.post("/{board_id}/types")
async def add_type(body: TypeBody, board_id: BoardId = Path(max_length=10), person: Person = Depends(current_person)) -> list:
    return _run(boards.save_type, person, _int(board_id), name=body.name, color=body.color)


@router.patch("/{board_id}/types/{type_id}")
async def change_type(body: TypeBody, board_id: BoardId = Path(max_length=10), type_id: int = Path(ge=1),
                      person: Person = Depends(current_person)) -> list:
    return _run(boards.save_type, person, _int(board_id), type_id=type_id, name=body.name, color=body.color,
                archived=body.archived)


@router.post("/{board_id}/states")
async def add_state(body: StateBody, board_id: BoardId = Path(max_length=10), person: Person = Depends(current_person)) -> list:
    return _run(boards.save_state, person, _int(board_id), name=body.name, category=body.category,
                wip_limit=body.wip_limit)


@router.patch("/{board_id}/states/{state_id}")
async def change_state(body: StateBody, board_id: BoardId = Path(max_length=10), state_id: int = Path(ge=1),
                       person: Person = Depends(current_person)) -> list:
    return _run(boards.save_state, person, _int(board_id), state_id=state_id, name=body.name, category=body.category,
                wip_limit=body.wip_limit, archived=body.archived)


@router.put("/{board_id}/order/{what}")
async def reorder(body: OrderBody, board_id: BoardId = Path(max_length=10), what: Literal["types", "states"] = Path(...),
                  person: Person = Depends(current_person)) -> dict:
    _run(boards.reorder, person, _int(board_id), what, body.ids)
    return _run(boards.get_board, person, board_id)


@router.get("/{board_id}/items")
async def list_items(board_id: BoardId = Path(max_length=10), person: Person = Depends(current_person),
                     q: str = Query(default="", max_length=80), type_id: int | None = Query(default=None, ge=1),
                     assignee_id: int | None = Query(default=None, ge=-1), team_id: int | None = Query(default=None, ge=1),
                     tag: str = Query(default="", max_length=30), done: bool = True, archived: bool = False) -> list[dict]:
    return _run(boards.items, person, board_id, q=q, type_id=type_id, assignee_id=assignee_id, team_id=team_id, tag=tag,
                include_done=done, archived=archived)


@router.post("/{board_id}/items")
async def create_item(body: ItemBody, board_id: BoardId = Path(max_length=10),
                      person: Person = Depends(current_person)) -> dict:
    return _run(boards.save_item, person, body.fields, None, board_id)


@router.post("/{board_id}/items/{item_id}/move")
async def move_item(body: MoveBody, board_id: BoardId = Path(max_length=10), item_id: int = Path(ge=1),
                    person: Person = Depends(current_person)) -> dict:
    team = "keep" if body.team_id == -1 else body.team_id
    who = "keep" if body.assignee_id == -1 else body.assignee_id
    return _run(boards.move, person, board_id, item_id, state_id=body.state_id, team_id=team, index=body.index,
                assignee_id=who)


@router.post("/{board_id}/requests")
async def make_request(body: RequestBody, board_id: BoardId = Path(max_length=10),
                       person: Person = Depends(current_person)) -> dict:
    return _run(boards.request_item, person, _int(board_id), body.model_dump())


# --- teams (= people groups with a manager) -----------------------------------------------------------------

def _team_manager(person: Person = Depends(current_person)) -> Person:
    if not (person.can("teams.manage") or person.can("roles.manage")):
        raise HTTPException(status_code=403)
    return person


@teams_router.get("")
async def list_teams(person: Person = Depends(current_person)) -> dict:
    if not (person.can("boards.use") or person.can("employees.view") or person.can("teams.manage")
            or person.can("roles.manage")):
        raise HTTPException(status_code=403)
    mine = {g["id"] for g in permissions.groups_of(person.id)}
    return {"teams": [{**g, "mine": g["id"] in mine, "gives_roles": bool(g["roles"])} for g in permissions.groups()],
            "can_manage": person.can("teams.manage") or person.can("roles.manage"),
            "can_roles": person.can("roles.manage"),
            "people": [{"id": u["id"], "display_name": u["display_name"]} for u in db.query(
                "SELECT id, display_name FROM users WHERE active = 1 ORDER BY display_name")]
            if person.can("teams.manage") or person.can("roles.manage") else []}


@teams_router.post("")
async def create_team(body: TeamBody, person: Person = Depends(_team_manager)) -> dict:
    actor = permissions.actor_for(person)
    gid = _run(permissions.create_group, actor, body.name or "", body.description or "")
    if body.manager_id is not None or body.color is not None:
        _run(permissions.update_group, actor, gid, manager_id=body.manager_id, color=body.color)
    return {"id": gid}


@teams_router.patch("/{team_id}")
async def update_team(body: TeamBody, team_id: int = Path(ge=1), person: Person = Depends(_team_manager)) -> dict:
    _run(permissions.update_group, permissions.actor_for(person), team_id, name=body.name, description=body.description,
         manager_id=body.manager_id, color=body.color)
    return {"id": team_id}


@teams_router.put("/{team_id}/members")
async def set_members(body: Members, request: Request, team_id: int = Path(ge=1),
                      person: Person = Depends(_team_manager)) -> dict:
    changed = _run(permissions.set_group_members, permissions.actor_for(person), team_id, body.user_ids)
    for uid in changed:                         # a change to someone's groups ends their dashboard sessions
        revoke_user_sessions(uid, keep=request if uid == person.id else None)
    return {"changed": changed}


@teams_router.delete("/{team_id}")
async def delete_team(request: Request, team_id: int = Path(ge=1), person: Person = Depends(_team_manager)) -> dict:
    members = _run(permissions.delete_group, permissions.actor_for(person), team_id)
    for uid in members:
        revoke_user_sessions(uid, keep=request if uid == person.id else None)
    return {"deleted": team_id}
