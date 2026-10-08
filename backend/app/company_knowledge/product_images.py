"""제품 이미지 라이브러리 — 제품 카드(knowledge_cards kind='product')에 연결한 사진.

쓰임: 공정 컨셉 이미지를 GPT(Codex 브리지)로 그릴 때 대안에 나오는 회사 제품(AMR·그리퍼 등)의 사진을
"외형 참고" 로 같이 보낸다. 실물과 똑같을 필요는 없고 비슷한 모양이면 된다(사용자 결정 2026-09-30).

채우는 길 두 가지:
  - 소개서 추출(extract): 제품 카드의 출처 쪽에서 그림을 꺼내, 그 쪽에 제품이 여럿이면 제품 이름(글상자·표 머리칸)과
    가장 가까운 그림을 그 제품 것으로 본다(22쪽 AMR 5종: 표 열 중심 ↔ 사진 중심이 일치). 로고처럼 여러 쪽에
    반복되는 그림, 작은 아이콘은 뺀다.
  - 직접 올리기(upload): 작업자가 제품을 골라 사진을 올린다.
어느 쪽이든 영업 관리자가 승인해야(approved) 쓰인다 — 경험 카드와 같은 승인 규칙(experience.is_knowledge_approver).

실행:
    docker compose run --rm --no-deps -v ./docs:/docs backend python -m app.company_knowledge.product_images extract
    ... product_images extract --dry   # 저장 없이 짝짓기 결과만 출력
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
import posixpath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

_log = logging.getLogger("company_knowledge.product_images")

EMU_PER_PT = 12700
MIN_SIDE_PT = 45            # 이보다 작은 그림은 아이콘·기호
MIN_SIDE_PX = 120
LOGO_REPEAT = 4             # 이만큼 여러 쪽에 나오는 같은 그림은 로고·장식
PER_CARD_MAX = 3            # 제품 하나에 소개서에서 가져올 후보 수
MAX_EDGE = 1280             # 저장·전송 공통 상한(브리지 본문 한도 안)
UPLOAD_BYTES_MAX = 8 * 1024 * 1024
PICK_MAX = 2                # 컨셉 이미지 한 장에 붙일 제품 사진 수(참고 이미지 전체 한도 3 안에서)
_RASTER = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp")
_NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
       "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_EMBED = f"{{{_NS['r']}}}embed"

# 대안 문장에 제품 이름 대신 부류 이름만 나올 때(예: "AMR 로 이송") 그 부류 제품을 떠올리는 말.
# 값은 카드 family 를 정규화한 문자열에 들어 있으면 같은 부류로 본다.
CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "amr": ("amr", "자율주행로봇", "자율이동로봇", "이동형로봇", "이동로봇", "무인운반", "agv"),
    "acr": ("acr", "적재로봇"),
    "4족": ("4족", "사족", "4족보행", "보행로봇"),
    "atc": ("atc", "툴체인저", "툴체인져", "자동툴교환"),
    "eoat": ("그리퍼", "gripper", "eoat", "엔드이펙터"),
    # 관제 솔루션은 사진이 화면 캡처라 외형 참고가 못 된다 — 부류 말로는 고르지 않는다(이름이 나오면 고름).
}


def norm(s: str) -> str:
    """비교용 — 소문자, 공백·기호 제거(한글·영숫자만)."""
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def has_word(raw: str, word: str) -> bool:
    """부류 말이 문장에 있나. 영문은 단어 경계로 본다("batch" 안의 atc 를 잡지 않게), 한글은 붙여 쓴 말도 잡는다."""
    w = norm(word)
    if not w:
        return False
    if re.fullmatch(r"[0-9a-z]+", w):
        return re.search(rf"(?<![a-z]){w}(?![a-z])", (raw or "").lower()) is not None
    return w in norm(raw)


# ── 소개서 쪽 읽기 ────────────────────────────────────────────────────────
@dataclass
class DeckPic:
    slide: int
    media: str                  # zip 안 경로(ppt/media/imageN.png)
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass
class Anchor:
    text: str
    cx: float
    cy: float


@dataclass
class SlideLayout:
    pics: list[DeckPic] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)


def _xfrm(el: ET.Element) -> tuple[float, float, float, float] | None:
    x = el.find("a:off", _NS)
    e = el.find("a:ext", _NS)
    if x is None or e is None:
        return None
    return (int(x.get("x", 0)) / EMU_PER_PT, int(x.get("y", 0)) / EMU_PER_PT,
            int(e.get("cx", 0)) / EMU_PER_PT, int(e.get("cy", 0)) / EMU_PER_PT)


def _texts(el: ET.Element) -> str:
    return "".join(t.text or "" for t in el.iter(f"{{{_NS['a']}}}t")).strip()


def parse_slide(xml: bytes, rels: bytes, no: int) -> SlideLayout:
    """쪽 XML → 그림 위치·제품 이름 후보(글상자·표 칸) 위치. 좌표는 pt, 그룹 변환 반영."""
    rmap = {r.get("Id"): r.get("Target") for r in ET.fromstring(rels)}
    out = SlideLayout()
    root = ET.fromstring(xml).find(".//p:cSld/p:spTree", _NS)
    if root is None:
        return out

    def walk(parent: ET.Element, tf) -> None:
        for ch in parent:
            tag = ch.tag.rsplit("}", 1)[-1]
            if tag == "grpSp":
                g = ch.find("p:grpSpPr/a:xfrm", _NS)
                off, cho = (g.find("a:off", _NS), g.find("a:chOff", _NS)) if g is not None else (None, None)
                ext, che = (g.find("a:ext", _NS), g.find("a:chExt", _NS)) if g is not None else (None, None)
                if None in (off, cho, ext, che):
                    walk(ch, tf)
                    continue
                sx = int(ext.get("cx")) / max(int(che.get("cx")), 1)
                sy = int(ext.get("cy")) / max(int(che.get("cy")), 1)
                ox, oy = int(off.get("x")) / EMU_PER_PT, int(off.get("y")) / EMU_PER_PT
                cx0, cy0 = int(cho.get("x")) / EMU_PER_PT, int(cho.get("y")) / EMU_PER_PT

                def inner(x, y, w, h, _tf=tf, sx=sx, sy=sy, ox=ox, oy=oy, cx0=cx0, cy0=cy0):
                    return _tf(ox + (x - cx0) * sx, oy + (y - cy0) * sy, w * sx, h * sy)
                walk(ch, inner)
            elif tag == "pic":
                box = ch.find("p:spPr/a:xfrm", _NS)
                blip = ch.find(".//a:blip", _NS)
                target = rmap.get(blip.get(_EMBED)) if blip is not None else None
                pos = _xfrm(box) if box is not None else None
                if target and pos:
                    media = posixpath.normpath(posixpath.join("ppt/slides", target))
                    out.pics.append(DeckPic(no, media, *tf(*pos)))
            elif tag == "sp":
                box = ch.find("p:spPr/a:xfrm", _NS)
                pos = _xfrm(box) if box is not None else None
                t = _texts(ch)
                if t and pos:
                    x, y, w, h = tf(*pos)
                    out.anchors.append(Anchor(t, x + w / 2, y + h / 2))
            elif tag == "graphicFrame":
                box = ch.find("p:xfrm", _NS)
                pos = _xfrm(box) if box is not None else None
                tbl = ch.find(".//a:tbl", _NS)
                if pos is None or tbl is None:
                    continue
                tx, ty, _, _ = tf(*pos)
                cols = [int(g.get("w", 0)) / EMU_PER_PT for g in tbl.iter(f"{{{_NS['a']}}}gridCol")]
                y = ty
                for tr in tbl.findall("a:tr", _NS):
                    rh = int(tr.get("h", 0)) / EMU_PER_PT
                    ci, x = 0, tx
                    for tc in tr.findall("a:tc", _NS):
                        span = int(tc.get("gridSpan", 1))
                        w = sum(cols[ci:ci + span])
                        t = _texts(tc)
                        if t:
                            out.anchors.append(Anchor(t, x + w / 2, y + rh / 2))
                        ci, x = ci + span, x + w
                    y += rh

    walk(root, lambda x, y, w, h: (x, y, w, h))
    return out


def _anchor_cards(anchor: Anchor, names: dict[int, str]) -> list[int]:
    t = norm(anchor.text)
    return [cid for cid, n in names.items() if n and n in t]


def match_pics(layout: SlideLayout, cards: dict[int, str], skip_media: set[str]) -> dict[int, list[DeckPic]]:
    """이 쪽의 그림 → 제품 카드. cards = {card_id: 제품 이름}.
    제품이 하나면 큰 그림 전부 그 제품. 여럿이면 각 그림을 이름이 적힌 곳(글상자·표 칸)이 가장 가까운 제품에.
    제품 이름이 여럿 들어간 글(쪽 제목 등)은 위치 근거가 못 되므로 뺀다. 이름이 쪽에 없는 제품은 받지 않는다."""
    pics = [p for p in layout.pics
            if p.media not in skip_media and p.media.lower().endswith(_RASTER)
            and p.w >= MIN_SIDE_PT and p.h >= MIN_SIDE_PT]
    out: dict[int, list[DeckPic]] = {}
    if not pics or not cards:
        return out
    if len(cards) == 1:
        out[next(iter(cards))] = sorted(pics, key=lambda p: -p.area)[:PER_CARD_MAX]
        return out
    names = {cid: norm(n) for cid, n in cards.items()}
    points: list[tuple[int, Anchor]] = []
    for a in layout.anchors:
        hit = _anchor_cards(a, names)
        if len(hit) == 1:
            points.append((hit[0], a))
    if not points:
        return out
    for p in pics:
        px, py = p.center
        cid, _ = min(points, key=lambda t: math.hypot(t[1].cx - px, t[1].cy - py))
        out.setdefault(cid, []).append(p)
    return {cid: sorted(ps, key=lambda p: -p.area)[:PER_CARD_MAX] for cid, ps in out.items()}


def repeated_media(z: zipfile.ZipFile) -> set[str]:
    """LOGO_REPEAT 쪽 이상에 나오는 그림(로고·배경 장식)."""
    count: dict[str, int] = {}
    for name in z.namelist():
        if not re.fullmatch(r"ppt/slides/_rels/slide\d+\.xml\.rels", name):
            continue
        targets = {posixpath.normpath(posixpath.join("ppt/slides", r.get("Target", "")))
                   for r in ET.fromstring(z.read(name)) if r.get("Type", "").endswith("/image")}
        for t in targets:
            count[t] = count.get(t, 0) + 1
    return {t for t, n in count.items() if n >= LOGO_REPEAT}


def to_jpeg(data: bytes) -> tuple[bytes, int, int] | None:
    """긴 변 MAX_EDGE, 투명 배경은 흰색으로 → JPEG. 너무 작거나 못 여는 그림은 None."""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001 — 깨진 그림·EMF 등은 건너뛴다
        return None
    if min(im.size) < MIN_SIDE_PX:
        return None
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    if max(im.size) > MAX_EDGE:
        im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return buf.getvalue(), im.width, im.height


# ── 컨셉에 붙일 제품 고르기 ────────────────────────────────────────────────
def category_of(family: str, name: str) -> str:
    f = f"{family} {name}"
    for cat, words in CATEGORY_WORDS.items():
        if any(has_word(f, w) for w in (cat, *words)):
            return cat
    return ""


def pick_cards(alt_text: str, exclude_text: str, cards: list[dict[str, Any]], limit: int = PICK_MAX) -> list[int]:
    """대안 문장 → 사진을 붙일 제품 카드 id(점수 순). cards = [{id, name, family}] (승인 사진이 있는 제품만).
    제품 이름이 그대로 나오면 3점, 부류 말(AMR·그리퍼 …)만 나오면 1점 + 이름 조각 겹침 가산.
    '넣지 않을 것'에 걸리는 제품·부류는 뺀다. 같은 부류는 한 장만(부류 말만 나왔을 때 AMR 5종이 다 붙지 않게)."""
    t, ex = norm(alt_text), norm(exclude_text)
    scored: list[tuple[float, int, str]] = []
    for c in cards:
        n = norm(c["name"])
        cat = category_of(c.get("family") or "", c["name"])
        cat_words = CATEGORY_WORDS.get(cat, ())
        if (n and n in ex) or any(has_word(exclude_text, w) for w in cat_words):
            continue
        if n and n in t:
            score = 3.0
        elif any(has_word(alt_text, w) for w in cat_words):
            cat_norm = {norm(w) for w in cat_words}
            tokens = [norm(x) for x in re.split(r"[\s/·()\-–,]+", c["name"]) if len(norm(x)) >= 2]
            score = 1.0 + 0.5 * sum(1 for x in tokens if x in t and x not in cat_norm)
        else:
            continue
        scored.append((score, c["id"], cat or n))
    # 'LYNX M20 Pro' 가 나오면 이름이 그 안에 들어 있는 'LYNX M20' 은 따로 나온 게 아니다
    full = {c["id"]: norm(c["name"]) for c in cards if norm(c["name"]) and norm(c["name"]) in t}
    inner = {i for i, n in full.items() if any(n != f and n in f for f in full.values())}
    scored = [s for s in scored if s[1] not in inner]
    scored.sort(key=lambda s: (-s[0], s[1]))
    picked, seen = [], set()
    for score, cid, cat in scored:
        if score < 3.0 and cat in seen:
            continue
        seen.add(cat)
        picked.append(cid)
        if len(picked) >= limit:
            break
    return picked


# ── DB ─────────────────────────────────────────────────────────────────────
class ImageError(Exception):
    """사용자에게 그대로 보여도 되는 한국어 문구."""


def _object_key(card_id: int, sha: str) -> str:
    return f"product_images/{card_id}/{sha[:24]}.jpg"


async def _store(db, card_id: int, jpeg: bytes, w: int, h: int, *, source: str, source_ref: str,
                 status: str, user_id: int | None) -> int | None:
    """저장 + 행 추가. 같은 제품에 같은 그림이 이미 있으면 None."""
    from .. import storage

    sha = hashlib.sha256(jpeg).hexdigest()
    exists = (await db.execute(text("SELECT id FROM product_images WHERE card_id = :c AND sha256 = :s"),
                               {"c": card_id, "s": sha})).scalar()
    if exists:
        return None
    key = _object_key(card_id, sha)
    await asyncio.to_thread(storage.put_object, key=key, data=io.BytesIO(jpeg), length=len(jpeg), mime="image/jpeg")
    return int((await db.execute(text(
        "INSERT INTO product_images (card_id, object_key, sha256, width, height, source, source_ref, review_status, "
        "active, submitted_by, reviewed_by, reviewed_at) VALUES (:c, :k, :s, :w, :h, :src, :ref, :st, :a, :u, :rb, "
        "CASE WHEN :a THEN NOW() END) RETURNING id"),
        {"c": card_id, "k": key, "s": sha, "w": w, "h": h, "src": source, "ref": source_ref[:200],
         "st": status, "a": status == "approved", "u": user_id,
         "rb": user_id if status == "approved" else None})).scalar_one())


async def _product_cards(db) -> list[dict[str, Any]]:
    rows = (await db.execute(text(
        "SELECT id, name, slides, card->>'family' AS family FROM knowledge_cards "
        "WHERE kind = 'product' AND active ORDER BY id"))).mappings().all()
    return [dict(r) for r in rows]


async def extract(*, dry: bool = False) -> dict[str, int]:
    """소개서 → 제품 사진 후보(승인 대기). 이미 있는 그림은 건너뛴다(다시 돌려도 안전)."""
    from ..database import SessionLocal
    from .intro_deck import DECK_NAME, DOCS

    deck = DOCS / "sources" / DECK_NAME
    if not deck.is_file():
        raise ImageError(f"소개서가 없습니다: {deck}")
    async with SessionLocal() as db:
        cards = await _product_cards(db)
    by_slide: dict[int, dict[int, str]] = {}
    for c in cards:
        for s in c["slides"] or []:
            by_slide.setdefault(int(s), {})[c["id"]] = c["name"]
    names = {c["id"]: c["name"] for c in cards}
    stats = {"slides": 0, "matched": 0, "saved": 0, "skipped": 0, "no_image": 0}
    with zipfile.ZipFile(deck) as z:
        skip = repeated_media(z)
        async with SessionLocal() as db:
            for no in sorted(by_slide):
                try:
                    layout = parse_slide(z.read(f"ppt/slides/slide{no}.xml"),
                                         z.read(f"ppt/slides/_rels/slide{no}.xml.rels"), no)
                except KeyError:
                    continue
                stats["slides"] += 1
                for cid, pics in match_pics(layout, by_slide[no], skip).items():
                    for p in pics:
                        stats["matched"] += 1
                        if dry:
                            print(f"{no:>3}쪽  {names[cid]:<32} ← {p.media} ({p.w:.0f}×{p.h:.0f}pt)", flush=True)
                            continue
                        img = to_jpeg(z.read(p.media))
                        if img is None:
                            stats["no_image"] += 1
                            continue
                        iid = await _store(db, cid, *img, source="deck", source_ref=f"소개서 {no}쪽 {p.media.rsplit('/', 1)[-1]}",
                                           status="pending", user_id=None)
                        stats["saved" if iid else "skipped"] += 1
                    if not dry:
                        await db.commit()
    return stats


async def upload(card_id: int, data: bytes, *, user_id: int, approver: bool, note: str = "") -> tuple[int, str]:
    """사용자가 올린 사진 → (id, 상태). 승인권자가 올리면 바로 approved, 아니면 pending."""
    if len(data) > UPLOAD_BYTES_MAX:
        raise ImageError("사진은 8MB 이하만 올릴 수 있습니다.")
    img = await asyncio.to_thread(to_jpeg, data)
    if img is None:
        raise ImageError(f"열 수 없는 그림이거나 너무 작습니다(짧은 변 {MIN_SIDE_PX}px 이상).")
    from ..database import SessionLocal

    status = "approved" if approver else "pending"
    async with SessionLocal() as db:
        ok = (await db.execute(text("SELECT 1 FROM knowledge_cards WHERE id = :c AND kind = 'product' AND active"),
                               {"c": card_id})).scalar()
        if not ok:
            raise ImageError("제품 카드를 찾을 수 없습니다.")
        iid = await _store(db, card_id, *img, source="upload", source_ref=note or "직접 올림", status=status,
                           user_id=user_id)
        if iid is None:
            raise ImageError("이 제품에 같은 사진이 이미 있습니다.")
        await db.commit()
    return iid, status


async def library() -> list[dict[str, Any]]:
    """제품별 사진 현황(반려 제외) — 관리 화면용."""
    from ..database import SessionLocal

    async with SessionLocal() as db:
        cards = await _product_cards(db)
        rows = (await db.execute(text(
            "SELECT i.id, i.card_id, i.width, i.height, i.source, i.source_ref, i.review_status, i.created_at, "
            "COALESCE(u.alias, u.username) AS submitted_by FROM product_images i LEFT JOIN users u ON u.id = i.submitted_by "
            "WHERE i.review_status <> 'rejected' ORDER BY i.card_id, i.id"))).mappings().all()
    images: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat()
        images.setdefault(d["card_id"], []).append(d)
    return [{"card_id": c["id"], "name": c["name"], "family": c["family"] or "", "slides": list(c["slides"] or []),
             "images": images.get(c["id"], [])} for c in cards]


async def review(image_id: int, action: str, *, reviewer_id: int) -> str:
    from ..database import SessionLocal

    status = "approved" if action == "approve" else "rejected"
    async with SessionLocal() as db:
        row = (await db.execute(text(
            "UPDATE product_images SET review_status = :s, active = :a, reviewed_by = :r, reviewed_at = NOW() "
            "WHERE id = :i RETURNING id"), {"s": status, "a": status == "approved", "r": reviewer_id, "i": image_id})).first()
        if row is None:
            raise ImageError("사진을 찾을 수 없습니다.")
        await db.commit()
    return status


# 외형이 같은 상위 모델 → 기본 모델 (사용자 지정: 사진을 따로 두지 않고 같은 사진을 쓴다)
SHARED_PHOTOS = {
    "intro:product:lynxm20pro": "intro:product:lynxm20",
    # X30 Pro 는 소개서 71쪽에 자기 실물 그림이 있어 공유하지 않는다(사용자 2026-10-01)
}


async def share_photos() -> int:
    """SHARED_PHOTOS 대로 기본 모델의 승인 사진을 상위 모델에도 연결(같은 저장 파일). 다시 돌려도 안전."""
    from ..database import SessionLocal

    async with SessionLocal() as db:
        n = (await db.execute(text(
            "INSERT INTO product_images (card_id, object_key, sha256, width, height, source, source_ref, review_status, "
            "active, submitted_by, reviewed_by, reviewed_at) "
            "SELECT t.id, i.object_key, i.sha256, i.width, i.height, i.source, '같은 사진: ' || b.name, 'approved', "
            "TRUE, i.submitted_by, i.reviewed_by, NOW() "
            "FROM unnest(CAST(:to AS text[]), CAST(:base AS text[])) AS m(to_key, base_key) "
            "JOIN knowledge_cards t ON t.card_key = m.to_key JOIN knowledge_cards b ON b.card_key = m.base_key "
            "JOIN product_images i ON i.card_id = b.id AND i.active "
            "WHERE NOT EXISTS (SELECT 1 FROM product_images x WHERE x.card_id = t.id AND x.sha256 = i.sha256)"),
            {"to": list(SHARED_PHOTOS), "base": list(SHARED_PHOTOS.values())})).rowcount
        await db.commit()
    return n


async def read_image(image_id: int) -> bytes:
    from ..database import SessionLocal
    from ..storage import get_object_stream

    async with SessionLocal() as db:
        key = (await db.execute(text("SELECT object_key FROM product_images WHERE id = :i"), {"i": image_id})).scalar()
    if not key:
        raise ImageError("사진을 찾을 수 없습니다.")

    def _read() -> bytes:
        resp = get_object_stream(key)
        try:
            return b"".join(resp.stream(amt=64 * 1024))
        finally:
            resp.close()
            resp.release_conn()
    return await asyncio.to_thread(_read)


async def refs_for_alt(alt: dict[str, Any] | None, limit: int = PICK_MAX
                       ) -> list[tuple[bytes, str, str]]:
    """대안에 나오는 회사 제품의 승인된 사진 → [(jpeg, mime, 제품 이름)]. 제품마다 먼저 승인된 한 장."""
    if not alt or limit <= 0:
        return []
    alt_text = " ".join([alt.get("name") or "", alt.get("robot") or "", alt.get("summary") or "",
                         *[str(s) for s in alt.get("structure") or []]])
    exclude_text = " ".join(str(s) for s in alt.get("exclude") or [])
    return await photos_for_text(alt_text, exclude_text, limit)


async def photos_for_text(alt_text: str, exclude_text: str = "", limit: int = PICK_MAX
                          ) -> list[tuple[bytes, str, str]]:
    """문장에 나오는 회사 제품의 승인된 사진 → [(jpeg, mime, 제품 이름)] (제안서 주요 항목 쪽에서도 쓴다)."""
    from ..database import SessionLocal

    async with SessionLocal() as db:
        if not (await db.execute(text("SELECT to_regclass('product_images')"))).scalar():
            return []
        rows = (await db.execute(text(
            "SELECT DISTINCT ON (k.id) k.id, k.name, k.card->>'family' AS family, i.id AS image_id "
            "FROM knowledge_cards k JOIN product_images i ON i.card_id = k.id "
            "WHERE k.kind = 'product' AND k.active AND i.active ORDER BY k.id, i.reviewed_at, i.id"))).mappings().all()
    cards = [{"id": r["id"], "name": r["name"], "family": r["family"] or ""} for r in rows]
    image_of = {r["id"]: (r["image_id"], r["name"]) for r in rows}
    out = []
    for cid in pick_cards(alt_text, exclude_text, cards, limit):
        iid, name = image_of[cid]
        try:
            out.append((await read_image(iid), "image/jpeg", name))
        except Exception as e:  # noqa: BLE001 — 사진 하나 못 읽었다고 컨셉 이미지를 멈추지 않는다
            _log.warning("[product_images] %s 사진 읽기 실패: %r", name, e)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "extract":
        print(asyncio.run(extract(dry="--dry" in sys.argv)), flush=True)
    elif len(sys.argv) > 1 and sys.argv[1] == "share":
        print(asyncio.run(share_photos()), flush=True)
    else:
        print(__doc__)
