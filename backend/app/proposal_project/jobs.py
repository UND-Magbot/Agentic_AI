"""오래 걸리는 작업(공정 제안·컨셉 이미지·PPT 제작·바로 만들기)의 백그라운드 실행과 진행 기록.

진행 상태는 proposal_projects.job 에 남겨 작업 화면이 주기적으로 읽는다.
  job = {kind, status: running|done|failed, step, message, started_at, finished_at}
프로젝트마다 한 번에 하나만 돈다. 서버가 재시작되면 메모리의 실행 표시가 사라지므로 API 가
alive=False 로 알려 주고, 화면은 '중단됨' 으로 보여 다시 시작하게 한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from ..database import SessionLocal

_log = logging.getLogger("proposal_project.jobs")

_running: set[int] = set()
_tasks: set[asyncio.Task] = set()

JOB_LABEL = {
    "propose": "공정 컨셉 제안",
    "concept": "공정 컨셉 제안·이미지 생성",
    "extra": "비교안 추가",
    "revise": "수정 요청 반영",
    "image": "컨셉 이미지 생성",
    "structure": "페이지 구성·견적 초안",
    "produce": "제안서 제작",
    "quick": "바로 제안서 만들기",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def set_job(project_id: int, **fields: Any) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE proposal_projects SET job = job || CAST(:j AS jsonb), updated_at = NOW() WHERE id = :p"),
            {"j": json.dumps(fields, ensure_ascii=False), "p": project_id},
        )
        await db.commit()


def is_running(project_id: int) -> bool:
    return project_id in _running


def start(project_id: int, kind: str, work: Callable[[Callable[[str], Awaitable[None]]], Awaitable[str]]) -> bool:
    """work(step) 을 백그라운드로 실행. step(문구) 로 진행 단계를 남기고, 반환 문구가 완료 메시지.
    이미 다른 작업이 돌고 있으면 False."""
    if project_id in _running:
        return False
    _running.add(project_id)

    async def step(msg: str) -> None:
        await set_job(project_id, step=msg)

    async def runner() -> None:
        try:
            await set_job(project_id, kind=kind, status="running", step="시작", message="",
                          started_at=_now(), finished_at=None)
            msg = await work(step)
            await set_job(project_id, status="done", step="", message=msg or "", finished_at=_now())
        except Exception as e:  # noqa: BLE001 — 백그라운드: 사유를 남기고 다시 시도할 수 있게 한다
            _log.exception("작업 실패 project=%s kind=%s", project_id, kind)
            await set_job(project_id, status="failed", message=f"{JOB_LABEL.get(kind, kind)} 실패: {e}",
                          finished_at=_now())
        finally:
            _running.discard(project_id)

    task = asyncio.create_task(runner())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
