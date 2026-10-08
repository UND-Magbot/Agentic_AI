"""제안서 프로젝트 저장·조회 — 가이드 "개발자가 저장해야 할 데이터".

값은 문단 하나가 아니라 문항(P01~P48)별로 값·단위·상태·성격·근거·확인일·반영 페이지·버전을 보존한다.
get_project() 가 돌려주는 dict 는 PPT 생성(5c 세션, generate_pptx)의 입력 계약이다
(docs/design/proposal_wizard_design.md §3).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .catalog import ASSET_ROLES, CODES, SECTION_KEYS, STATUSES

TITLE_MAX = 300
SECTION_MAX = 4000
REQUEST_MAX = 8000
NOTE_MAX = 300
ASSETS_MAX = 20
_IMAGE_MIMES = ("image/png", "image/jpeg", "image/jpg", "image/webp")
DEFAULT_OUTPUT = {"max_pages": 10, "language": "ko", "style": "und"}
# 제목을 비우면 임시 제목으로 만들고, 자료 읽기가 P01(고객·프로젝트)을 찾으면 그것으로 바꾼다(dialog.run_reading).
AUTO_TITLE = "새 제안서 프로젝트"


class ProjectError(ValueError):
    """사용자 입력 오류 — API 가 400 으로 돌려준다."""


@dataclass
class AssetInput:
    attachment_id: int
    role: str
    note: str = ""
    external_ok: bool = False


async def create_project(
    db: AsyncSession,
    *,
    user_id: int,
    title: str,
    request_text: str,
    intake: dict[str, str],
    assets: list[AssetInput],
) -> int:
    """첫 화면 제출 → 프로젝트 생성. intake 는 필수 질문 답 {문항 코드: 답}(예전 폼의 묶음 키도 받는다).
    답한 문항은 곧바로 '확인됨'(출처 form)으로 저장하고, 나머지는 자료 읽기(dialog.run_reading)가 답·메모·첨부에서
    찾는다. request_text 는 선택 메모(고객 메일 등)."""
    title = (title or "").strip() or AUTO_TITLE
    if len(title) > TITLE_MAX:
        raise ProjectError(f"프로젝트 이름은 {TITLE_MAX}자 이내로 입력해 주세요.")
    request_text = (request_text or "").strip()
    if len(request_text) > REQUEST_MAX:
        raise ProjectError(f"고객 요청 원문은 {REQUEST_MAX}자 이내로 입력해 주세요.")
    clean: dict[str, str] = {}
    for key, value in (intake or {}).items():
        if key not in SECTION_KEYS and key not in CODES:
            raise ProjectError(f"알 수 없는 입력 항목입니다: {key}")
        value = (value or "").strip()
        if len(value) > SECTION_MAX:
            raise ProjectError(f"한 항목은 {SECTION_MAX}자 이내로 입력해 주세요.")
        if value:
            clean[key] = value
    if not clean and not request_text and not assets:
        raise ProjectError("필수 질문에 답하거나 자료를 첨부해 주세요.")
    if len(assets) > ASSETS_MAX:
        raise ProjectError(f"첨부는 {ASSETS_MAX}개까지 가능합니다.")

    atts = await _owned_attachments(db, user_id, [a.attachment_id for a in assets])
    for a in assets:
        if a.role not in ASSET_ROLES:
            raise ProjectError(f"알 수 없는 첨부 용도입니다: {a.role}")
        mime = atts[a.attachment_id]["mime"]
        # 외부(Codex) 전송은 사용자가 허용한 이미지만. 가격 근거는 허용해도 보내지 않는다(사용자 결정 2026-09-29).
        if a.external_ok and mime not in _IMAGE_MIMES:
            raise ProjectError("외부 전송 허용은 이미지에만 선택할 수 있습니다.")
        if a.external_ok and a.role == "price":
            raise ProjectError("가격 근거 자료는 외부로 보낼 수 없습니다.")

    pid = int((await db.execute(
        text("INSERT INTO proposal_projects (user_id, title, request_text, intake, output) "
             "VALUES (:u, :t, :r, CAST(:i AS jsonb), CAST(:o AS jsonb)) RETURNING id"),
        {"u": user_id, "t": title, "r": request_text, "i": json.dumps(clean, ensure_ascii=False),
         "o": json.dumps(DEFAULT_OUTPUT)},
    )).scalar_one())
    await db.execute(
        text("INSERT INTO project_items (project_id, code) SELECT :p, unnest(CAST(:codes AS text[]))"),
        {"p": pid, "codes": list(CODES)},
    )
    for code, value in clean.items():
        if code in CODES:
            await db.execute(
                text("UPDATE project_items SET value = :v, status = 'confirmed', source = 'form', "
                     "evidence = '[필수 질문 답변]', checked_at = NOW() WHERE project_id = :p AND code = :c"),
                {"v": value, "p": pid, "c": code})
    for a in assets:
        await db.execute(
            text("INSERT INTO project_assets (project_id, attachment_id, role, note, external_ok) "
                 "VALUES (:p, :a, :r, :n, :e)"),
            {"p": pid, "a": a.attachment_id, "r": a.role, "n": (a.note or "")[:NOTE_MAX],
             "e": bool(a.external_ok)},
        )
    await db.execute(
        text("INSERT INTO project_messages (project_id, role, content) VALUES (:p, 'system', :c)"),
        {"p": pid, "c": f"필수 질문 답 {sum(1 for k in clean if k in CODES)}개, 첨부 {len(assets)}개"
                        + (f", 메모 {len(request_text)}자" if request_text else "") + "로 프로젝트를 만들었습니다."},
    )
    await db.commit()
    return pid


async def _owned_attachments(db: AsyncSession, user_id: int, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = (await db.execute(
        text("SELECT id, user_id, mime, original_filename FROM attachments WHERE id = ANY(:ids)"),
        {"ids": ids},
    )).mappings().all()
    found = {r["id"]: dict(r) for r in rows if r["user_id"] == user_id}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ProjectError(f"첨부를 찾을 수 없습니다: {missing}")
    return found


async def _load_project_row(db: AsyncSession, project_id: int, user_id: int | None,
                            is_superadmin: bool) -> dict | None:
    row = (await db.execute(
        text("SELECT * FROM proposal_projects WHERE id = :id"), {"id": project_id},
    )).mappings().first()
    if row is None or (user_id is not None and row["user_id"] != user_id and not is_superadmin):
        return None
    return dict(row)


async def get_project(
    db: AsyncSession, project_id: int, *, user_id: int | None = None, is_superadmin: bool = False,
) -> dict[str, Any] | None:
    """프로젝트 전체 — PPT 생성·작업 화면 공용. user_id 를 주면 소유자만(없으면 내부 호출)."""
    p = await _load_project_row(db, project_id, user_id, is_superadmin)
    if p is None:
        return None
    items = {
        r["code"]: {
            "value": r["value"], "unit": r["unit"], "status": r["status"], "nature": r["nature"],
            "source": r["source"], "evidence": r["evidence"],
            "checked_at": r["checked_at"].isoformat() if r["checked_at"] else None,
            "pages": r["pages"] or [], "version": r["version"],
        }
        for r in (await db.execute(
            text("SELECT * FROM project_items WHERE project_id = :p ORDER BY code"), {"p": project_id},
        )).mappings().all()
    }
    assets = [
        {"id": r["id"], "attachment_id": r["attachment_id"], "filename": r["original_filename"],
         "mime": r["mime"], "role": r["role"], "note": r["note"], "external_ok": r["external_ok"],
         "readable": r["readable"], "alt_id": r["alt_id"]}
        for r in (await db.execute(
            text("SELECT pa.*, a.original_filename, a.mime FROM project_assets pa "
                 "JOIN attachments a ON a.id = pa.attachment_id WHERE pa.project_id = :p ORDER BY pa.id"),
            {"p": project_id},
        )).mappings().all()
    ]
    messages = [
        {"role": r["role"], "content": r["content"], "at": r["created_at"].isoformat()}
        for r in (await db.execute(
            text("SELECT role, content, created_at FROM project_messages WHERE project_id = :p ORDER BY id"),
            {"p": project_id},
        )).mappings().all()
    ]
    approvals = [
        {"target": r["target"], "version": r["version"], "approved_at": r["approved_at"].isoformat()}
        for r in (await db.execute(
            text("SELECT target, version, approved_at FROM project_approvals WHERE project_id = :p ORDER BY id"),
            {"p": project_id},
        )).mappings().all()
    ]
    images = [
        {"id": r["id"], "attachment_id": r["attachment_id"], "alt_id": r["alt_id"], "version": r["version"],
         "approved": r["status"] == "approved", "status": r["status"], "labels": r["labels"] or [],
         "checks": r["checks"] or [], "prompt": r["prompt"], "ref_sent": r["ref_sent"], "ref_used": r["ref_used"],
         "error": r["error"], "revision": r["revision"], "created_at": r["created_at"].isoformat()}
        for r in (await db.execute(
            text("SELECT * FROM project_images WHERE project_id = :p ORDER BY id"), {"p": project_id},
        )).mappings().all()
    ]
    outputs = [
        {"id": r["id"], "version": r["version"], "attachment_id": r["attachment_id"], "filename": r["filename"],
         "report": r["report"] or {}, "created_at": r["created_at"].isoformat()}
        for r in (await db.execute(
            text("SELECT * FROM project_outputs WHERE project_id = :p ORDER BY id"), {"p": project_id},
        )).mappings().all()
    ]
    return {
        "id": p["id"], "user_id": p["user_id"], "title": p["title"], "stage": p["stage"],
        "request_text": p["request_text"], "intake": p["intake"] or {},
        "items": items, "assets": assets, "messages": messages, "approvals": approvals,
        # 5c 의 PPT 계약 필드(docs/design/proposal_wizard_design.md §3).
        "alternatives": p["alternatives"] or [], "images": images, "pages": p["pages"] or [],
        "quote_lines": p["quote_lines"] or [], "outputs": outputs, "job": p["job"] or {},
        "experience": p.get("experience") or [],
        "output": p["output"] or DEFAULT_OUTPUT,
        "created_at": p["created_at"].isoformat(), "updated_at": p["updated_at"].isoformat(),
    }


async def list_projects(db: AsyncSession, user_id: int, limit: int = 50) -> list[dict]:
    rows = (await db.execute(
        text("SELECT p.id, p.title, p.stage, p.updated_at, "
             "count(i.id) FILTER (WHERE i.status <> 'empty') AS filled "
             "FROM proposal_projects p LEFT JOIN project_items i ON i.project_id = p.id "
             "WHERE p.user_id = :u GROUP BY p.id ORDER BY p.updated_at DESC LIMIT :l"),
        {"u": user_id, "l": limit},
    )).mappings().all()
    return [{"id": r["id"], "title": r["title"], "stage": r["stage"],
             "updated_at": r["updated_at"].isoformat(), "filled": r["filled"], "total": len(CODES)}
            for r in rows]


async def set_item(
    db: AsyncSession, project_id: int, code: str, *, value: str, status: str, source: str,
    evidence: str = "", unit: str = "", nature: str = "", reason: str = "",
) -> None:
    """문항 값 갱신 + 이력. 추출·대화·사용자 수정이 모두 이 함수로 쓴다."""
    if code not in CODES:
        raise ProjectError(f"알 수 없는 문항입니다: {code}")
    if status not in STATUSES:
        raise ProjectError(f"알 수 없는 상태입니다: {status}")
    old = (await db.execute(
        text("SELECT value, status FROM project_items WHERE project_id = :p AND code = :c"),
        {"p": project_id, "c": code},
    )).first()
    if old is None:
        raise ProjectError("프로젝트 문항을 찾을 수 없습니다.")
    if old.value == value and old.status == status:
        return
    await db.execute(
        text("UPDATE project_items SET value = :v, status = :s, source = :src, evidence = :e, unit = :u, "
             "nature = :n, checked_at = NOW(), version = version + 1, updated_at = NOW() "
             "WHERE project_id = :p AND code = :c"),
        {"v": value, "s": status, "src": source, "e": evidence, "u": unit, "n": nature,
         "p": project_id, "c": code},
    )
    await db.execute(
        text("INSERT INTO project_item_history (project_id, code, old_value, new_value, old_status, "
             "new_status, reason) VALUES (:p, :c, :ov, :nv, :os, :ns, :r)"),
        {"p": project_id, "c": code, "ov": old.value, "nv": value, "os": old.status, "ns": status,
         "r": reason},
    )
    await db.execute(text("UPDATE proposal_projects SET updated_at = NOW() WHERE id = :p"), {"p": project_id})
