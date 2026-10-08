"""자료 읽기 실행 + 핵심 질문 대화 (가이드 표 2 의 1·2 단계).

- run_reading(project_id): 첨부를 읽고 extract 로 문항을 채운 뒤 'questioning' 단계로 넘긴다.
  프로젝트 생성 직후 백그라운드로 돈다(api_proposal_projects). 실패하면 'intake' 로 되돌리고 사유를 남긴다.
- next_questions(project): 자료에서 못 찾은 것 중 꼭 필요한 것만, 첫 화면 뒤 합쳐서 최대 5개(FOLLOWUP_MAX).
  설계 문항은 묻지 않는다(공정 제안 단계에서 AI 가 제안). 같은 묶음 안에서는 자료 충돌을 먼저.
  사용자가 '모름' 으로 답한 문항(unknown)은 다시 묻지 않는다(컨셉 진행 조건인 필수 문항은 예외).
- answer(...): 사용자 답을 source=dialog, status=confirmed 로 기록(모름은 unknown).
- finish_questions(...): 필수 문항이 채워졌을 때만 'concept' 단계로 넘긴다.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import SessionLocal
from ..storage import get_object_stream
from . import extract as ex
from .catalog import ESSENTIAL_CODES, PRIORITY_OF, QUESTIONS
from .service import AUTO_TITLE, ProjectError, get_project, set_item

_log = logging.getLogger("proposal_project.dialog")

BATCH_MAX = 5          # 가이드 첫 응답 예시는 3개 — 한 번에 몇 가지만 묻는다
FOLLOWUP_MAX = 5       # 첫 화면 뒤 추가 질문 전체 한도(컨셉 필수 문항은 예외)
# 첫 화면에 없지만 공정 컨셉(배치·흐름)을 세우는 데 꼭 필요한 것 — 보관·목적지, 작업자 역할, 연동 설비·신호,
# 공간 변경 범위, 만재 교환. 나머지(출력 형식·첨부 우선순위 등)는 묻지 않고 기본값·AI 제안으로 간다.
FOLLOWUP_EXTRA = ("P25", "P37", "P38", "P34", "P27")
ANSWER_MAX = 4000
FILLED = ("confirmed", "adopted", "assumed")
_running: set[int] = set()
_tasks: set[asyncio.Task] = set()


def required_open(project: dict[str, Any]) -> list[str]:
    return [c for c, q in QUESTIONS.items()
            if q.required and project["items"].get(c, {}).get("status") not in FILLED]


def _group(code: str) -> tuple[int, str]:
    return PRIORITY_OF.get(code, (len(PRIORITY_OF), QUESTIONS[code].group))


def next_questions(project: dict[str, Any], limit: int = BATCH_MAX) -> list[dict[str, Any]]:
    """첫 화면 뒤 추가 질문 — 합쳐서 FOLLOWUP_MAX 개까지만(사용자 결정 2026-09-30: 질문이 너무 많다).
    후보: 컨셉 필수 문항(한도와 무관하게 채워질 때까지) → 첫 화면 문항 중 빈 것 → FOLLOWUP_EXTRA.
    이미 작업 화면에서 물은 문항(source=dialog, 답·모름·건너뜀 모두)만큼 한도에서 뺀다."""
    items = project["items"]

    def open_(c: str) -> bool:
        st = items.get(c, {}).get("status")
        # 필수 문항은 '모름' 이어도 다시 묻는다 — 컨셉 초안의 진행 조건이라 비워 둔 채 넘어갈 수 없다.
        return st in ("empty", "conflict") or (QUESTIONS[c].required and st == "unknown")

    asked = sum(1 for it in items.values() if it.get("source") == "dialog")
    must = [c for c, q in QUESTIONS.items() if q.required and open_(c)]
    rest = [c for c in dict.fromkeys((*ESSENTIAL_CODES, *FOLLOWUP_EXTRA)) if c not in must and open_(c)]
    rest.sort(key=lambda c: items[c]["status"] != "conflict")        # 자료 충돌은 먼저 확인(안정 정렬)
    room = max(0, min(limit, FOLLOWUP_MAX - asked) - len(must))
    order = {c: i for i, c in enumerate(QUESTIONS)}
    # 보여 주는 순서는 가이드 표 3 묶음 순서, 같은 묶음 안에서는 자료 충돌을 먼저.
    picked = sorted(must + rest[:room], key=lambda c: (_group(c)[0], items[c]["status"] != "conflict", order[c]))
    return [
        {"code": c, "question": QUESTIONS[c].question, "group": _group(c)[1],
         "required": QUESTIONS[c].required, "status": items[c]["status"], "value": items[c]["value"],
         "evidence": items[c]["evidence"]}
        for c in picked
    ]


async def _set_stage(db: AsyncSession, project_id: int, stage: str) -> None:
    await db.execute(text("UPDATE proposal_projects SET stage = :s, updated_at = NOW() WHERE id = :p"),
                     {"s": stage, "p": project_id})


async def _message(db: AsyncSession, project_id: int, role: str, content: str) -> None:
    await db.execute(text("INSERT INTO project_messages (project_id, role, content) VALUES (:p, :r, :c)"),
                     {"p": project_id, "r": role, "c": content})


def _read_object(object_key: str) -> bytes:
    resp = get_object_stream(object_key)
    try:
        return b"".join(resp.stream(amt=64 * 1024))
    finally:
        resp.close()
        resp.release_conn()


async def _asset_texts(db: AsyncSession, project_id: int) -> tuple[dict[int, tuple[str, str]], dict[int, bool]]:
    rows = (await db.execute(
        text("SELECT pa.id, pa.role, a.object_key, a.original_filename, a.mime FROM project_assets pa "
             "JOIN attachments a ON a.id = pa.attachment_id WHERE pa.project_id = :p ORDER BY pa.id"),
        {"p": project_id},
    )).mappings().all()
    texts: dict[int, tuple[str, str]] = {}
    readable: dict[int, bool] = {}
    for r in rows:
        if r["role"] not in ex._TEXT_ROLES:
            continue          # 외형 참조 이미지는 글자 근거가 아니다(컨셉 이미지 단계에서 쓴다).
        try:
            data = await asyncio.to_thread(_read_object, r["object_key"])
            txt = await asyncio.to_thread(ex.read_document, data, r["original_filename"], r["mime"])
        except Exception as e:  # noqa: BLE001 — 저장소 오류도 '읽기 실패' 로 기록하고 계속한다
            _log.warning("첨부 %s 읽기 실패: %s", r["original_filename"], e)
            txt = None
        readable[r["id"]] = bool(txt and txt.strip())
        if readable[r["id"]]:
            texts[r["id"]] = (r["original_filename"], txt)
    return texts, readable


_GREETING = re.compile(r"^(안녕하세요|안녕하십니까|수고\s*많으십니다|수고하십니다|감사합니다|반갑습니다|\S+\s*입니다\.?$)")


def auto_title(found: dict[str, str], request_text: str) -> str:
    if found.get("P01"):
        return found["P01"][:60].strip()
    if found.get("P06"):
        return f"{found['P06'][:40].strip()} 자동화 제안"
    for ln in request_text.splitlines():
        ln = ln.strip()
        if ln and not _GREETING.match(ln):
            return ln[:60]
    return ""


async def run_reading(project_id: int, *, chat=None) -> None:
    """자료 읽기 1회. 같은 프로젝트가 이미 읽는 중이면 무시한다."""
    if project_id in _running:
        return
    _running.add(project_id)
    try:
        async with SessionLocal() as db:
            await _set_stage(db, project_id, "reading")
            await db.commit()
            project = await get_project(db, project_id)
            if project is None:
                return
            texts, readable = await _asset_texts(db, project_id)
            res = await ex.extract(project, texts, **({"chat": chat} if chat else {}))
            written = 0
            for it in res.items:
                if project["items"][it.code]["source"] in ("dialog", "form") \
                        and project["items"][it.code]["status"] != "empty":
                    continue      # 사용자가 직접 답한 값(필수 질문·추가 질문)은 자료 읽기로 덮지 않는다.
                await set_item(db, project_id, it.code, value=it.value, status=it.status, source=it.source,
                               evidence=it.evidence, unit=it.unit, reason="자료 읽기")
                written += 1
            for aid, ok in readable.items():
                await db.execute(text("UPDATE project_assets SET readable = :r WHERE id = :i"), {"r": ok, "i": aid})
            unread = [aid for aid, ok in readable.items() if not ok]
            if project["title"] == AUTO_TITLE:
                # 제목을 비워 뒀으면 자료에서 찾은 고객·프로젝트(P01) → 대상물(P06)+"자동화 제안" →
                # 인사말이 아닌 요청 원문 첫 줄 순으로 정한다(실측: 첫 줄이 "안녕하세요…" 인 메일이 많다).
                found = {it.code: it.value for it in res.items}
                # 사용자가 직접 답한 값(필수 질문)이 자료에서 뽑은 값보다 우선.
                found.update({c: v["value"] for c, v in project["items"].items()
                              if v["source"] in ("form", "dialog") and v["value"]})
                new_title = auto_title(found, project["request_text"])
                if new_title:
                    await db.execute(text("UPDATE proposal_projects SET title = :t WHERE id = :p"),
                                     {"t": new_title, "p": project_id})
            await _set_stage(db, project_id, "questioning")
            await db.commit()
            project = await get_project(db, project_id)
            qs = next_questions(project)
            msg = f"자료를 읽고 {written}개 문항을 채웠습니다."
            if res.dropped:
                msg += f" 원문에서 근거를 찾지 못한 값 {len(res.dropped)}개는 넣지 않았습니다."
            if unread:
                msg += f" 첨부 {len(unread)}개는 글자를 읽지 못했습니다(스캔 이미지·지원하지 않는 형식)."
            msg += f" 이어서 {len(qs)}개를 여쭙겠습니다." if qs else " 더 여쭐 문항이 없습니다."
            await _message(db, project_id, "assistant", msg)
            await db.commit()
    except Exception as e:  # noqa: BLE001 — 백그라운드 작업: 사유를 남기고 다시 시도할 수 있게 한다
        _log.exception("자료 읽기 실패 project=%s", project_id)
        async with SessionLocal() as db:
            await _set_stage(db, project_id, "intake")
            await _message(db, project_id, "assistant", f"자료 읽기에 실패했습니다: {e}. '자료 다시 읽기' 를 눌러 주세요.")
            await db.commit()
    finally:
        _running.discard(project_id)


def start_reading(project_id: int) -> bool:
    """백그라운드로 자료 읽기 시작. 이미 도는 중이면 False."""
    if project_id in _running:
        return False
    task = asyncio.create_task(run_reading(project_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def is_reading(project_id: int) -> bool:
    return project_id in _running


async def answer(db: AsyncSession, project_id: int, answers: list[dict[str, Any]]) -> int:
    """질문 답 기록. answers: [{code, value, unknown}]. 빈 답은 선택 문항이면 미확인, 필수 문항이면 그대로 둔다."""
    lines, n = [], 0
    for a in answers:
        code = str(a.get("code", "")).upper()
        if code not in QUESTIONS:
            raise ProjectError(f"알 수 없는 문항입니다: {code}")
        value = str(a.get("value") or "").strip()
        if len(value) > ANSWER_MAX:
            raise ProjectError(f"답은 {ANSWER_MAX}자 이내로 입력해 주세요.")
        if a.get("unknown"):
            await set_item(db, project_id, code, value="", status="unknown", source="dialog",
                           reason="사용자: 모름")
            lines.append(f"{code} 모름")
        elif value:
            await set_item(db, project_id, code, value=value, status="confirmed", source="dialog",
                           evidence="[작업 화면 답변]", reason="사용자 답변")
            lines.append(f"{code} {value}")
        elif not QUESTIONS[code].required:
            # 빈칸으로 넘긴 선택 문항 — 같은 질문을 계속 되묻지 않도록 '미확인' 으로 둔다.
            await set_item(db, project_id, code, value="", status="unknown", source="dialog",
                           reason="사용자: 건너뜀")
            lines.append(f"{code} 건너뜀")
        else:
            continue
        n += 1
    if n:
        await _message(db, project_id, "user", "\n".join(lines))
        await db.execute(text("UPDATE proposal_projects SET updated_at = NOW() WHERE id = :p"), {"p": project_id})
    await db.commit()
    return n


async def finish_questions(db: AsyncSession, project: dict[str, Any]) -> None:
    missing = required_open(project)
    if missing:
        raise ProjectError("핵심 문항에 먼저 답해 주세요: " + ", ".join(missing))
    await _set_stage(db, project["id"], "concept")
    await _message(db, project["id"], "assistant",
                   "핵심 질문을 마쳤습니다. 이어서 공정 컨셉을 제안하고 컨셉 이미지를 그립니다.")
    await db.commit()
