"""Research Project 记忆：多项目隔离（users → projects → memory）。"""
from __future__ import annotations

from companion import db
from companion.conversation import dialogue_manager


def create_project(user_id: str = "default", name: str = "") -> str:
    return dialogue_manager.create_project(user_id=user_id, name=name)


def list_projects(user_id: str) -> list[dict]:
    rows = db.get_conn().execute(
        "SELECT id, name, state, created_at, updated_at FROM projects WHERE user_id=? "
        "ORDER BY updated_at DESC", (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_project(project_id: str) -> dict | None:
    row = db.get_conn().execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return dict(row) if row else None
