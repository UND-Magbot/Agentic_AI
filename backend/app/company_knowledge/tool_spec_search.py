"""실제 제품인 툴(그리퍼 등)의 공개 사양 외부 검색(사용자 2026-10-02: 실제 있는 제품이면 외부 웹 검색 — Codex 사용).

흐름: 툴 이름·모델이 실제 제품처럼 보이면(looks_real) 화면이 사용자에게 검색할지 묻고 → 외부 AI(Codex 브리지 /v1/search)로
공개 사양을 찾고 → 사내 AI 가 검색 글에서 값만 뽑는다(외부로 다시 보내지 않음) → 코드가 검증(검색 글에 실제 적힌 숫자만,
다른 모델 값 금지) → 화면이 '추정(출처)'으로 보여 주고 사용자가 [빈 칸에 채우기]를 눌러야 질문 칸에 들어간다.
JSON data_policy: do_not_infer_model_specs — 사양을 지어내지 않는다. 값 상태는 estimated(출처 기록), 확정은 엔지니어.
외부로 나가는 것은 제품 모델명뿐(external_gateway 가 기록).
"""
from __future__ import annotations

import re
from typing import Any

from .. import codex_client
from .product_recommend import _extract_json

# 그리퍼·툴 제조사(공개 제품) — 이름에 있으면 실제 제품으로 본다
BRANDS = ("OnRobot", "Robotiq", "Schunk", "SMC", "Festo", "Zimmer", "Schmalz", "Piab", "Gimatic", "Weiss", "DH Robotics",
          "DH-Robotics", "JODELL", "Sommer", "Destaco", "Kosmek", "Koganei", "CKD", "Applied Robotics", "ATI", "Stäubli",
          "Staubli", "Bastian", "Coval", "Joulin", "IAI", "Ewellix", "Inspire")
_BRAND_RE = re.compile("|".join(re.escape(b) for b in BRANDS), re.I)
_MODEL_RE = re.compile(r"\b[A-Z]{1,5}-?\d{1,4}[A-Z]{0,2}\b")          # RG2, 2FG7, EGP-40, VGC10
_NOT_MODEL_RE = re.compile(r"RS-?\d{3}|DI\s*\d|DO\s*\d|\d+\s*(kg|mm|A|V|bar)\b", re.I)

_SYSTEM = """너는 그리퍼·툴 사양 정리 담당이다. [검색 결과]에서 [제품]의 공개 사양만 뽑아 JSON 으로 답한다.
- 검색 결과에 적힌 값만 쓴다. 지어내지 않는다. 같은 회사의 다른 모델 값은 쓰지 않는다. 확실하지 않으면 null.
- 무게는 kg(g 로 적혀 있으면 kg 로 바꿈), 크기는 mm, 전압 V, 전류 A(mA 면 A 로 바꿈), 압력 bar.
- current_A 는 정격·연속 전류, peak_A 는 최대·피크·기동 전류. needs_air 는 공압(압축공기)을 쓰면 true, 전동이라 안 쓰면 false.
- evidence 에 값마다 근거가 된 검색 결과 문구를 짧게.
{"mass_kg": null, "size_mm": {"x": null, "y": null, "z": null}, "voltage_V": null, "current_A": null, "peak_A": null,
 "needs_air": null, "pressure_bar": {"min": null, "max": null}, "signals": "", "evidence": {}}"""

TEXT_MAX = 6000


class SpecSearchError(Exception):
    """화면에 보여 줄 문구."""


def looks_real(tool: dict[str, Any]) -> str | None:
    """실제 제품처럼 보이면 검색할 제품명(모델 칸 우선, 없으면 이름), 아니면 None."""
    for text in (str(tool.get("model") or "").strip(), str(tool.get("name") or "").strip()):
        if not text:
            continue
        clean = _NOT_MODEL_RE.sub(" ", text)
        if _BRAND_RE.search(clean) or _MODEL_RE.search(clean):
            return text[:80]
    return None


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in re.findall(r"\d+(?:[.,]\d+)?", text or "")}


def _seen(v: Any, nums: set[float], scale: tuple[float, ...] = (1,)) -> float | None:
    """검색 글에 실제 적힌 숫자(단위 변환 g→kg, mA→A 포함)만 받는다."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if any(round(f * k, 6) in nums for k in scale) else None


def clean(data: dict[str, Any], text: str) -> list[dict[str, Any]]:
    """사내 AI 가 뽑은 값 → 질문 칸(path)별 제안. 검색 글에 없는 숫자는 버린다."""
    nums = _numbers(text)
    ev = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    out: list[dict[str, Any]] = []

    def add(path: str, label: str, value: Any, display: str, key: str) -> None:
        out.append({"path": path, "label": label, "value": value, "display": display,
                    "evidence": str(ev.get(key) or "")[:200]})

    m = _seen(data.get("mass_kg"), nums, (1, 1000))
    if m is not None:
        add("tools[].intake.tool_assembly_mass_kg", "툴 무게(그리퍼 본체 — 브래킷·어댑터는 별도 확인)", m, f"{m:g}kg", "mass_kg")
    size = data.get("size_mm") if isinstance(data.get("size_mm"), dict) else {}
    for ax in ("x", "y", "z"):
        v = _seen(size.get(ax), nums)
        if v is not None:
            add(f"tools[].intake.dimensions_mm.{ax}", f"크기 {ax.upper()}", v, f"{v:g}mm", "size_mm")
    cur = _seen(data.get("current_A"), nums, (1, 1000))
    peak = _seen(data.get("peak_A"), nums, (1, 1000))
    volt = _seen(data.get("voltage_V"), nums)
    if cur is not None or peak is not None or volt is not None:
        add("tools[].intake.needs_power", "전기 필요", True, "있음" + (f"(DC {volt:g}V)" if volt else ""), "voltage_V")
    if cur is not None:
        add("tools[].electrical.current_A", "연속 전류", cur, f"{cur:g}A", "current_A")
    if peak is not None:
        add("tools[].electrical.peak_A", "피크 전류", peak, f"{peak:g}A", "peak_A")
    if isinstance(data.get("needs_air"), bool):
        add("tools[].intake.needs_air", "공압 필요", data["needs_air"], "있음" if data["needs_air"] else "없음", "needs_air")
    pb = data.get("pressure_bar") if isinstance(data.get("pressure_bar"), dict) else {}
    lo, hi = _seen(pb.get("min"), nums), _seen(pb.get("max"), nums)
    if lo is not None or hi is not None:
        lo, hi = lo if lo is not None else hi, hi if hi is not None else lo
        add("tools[].pneumatic.pressure_bar", "사용 압력", {"min": lo, "max": hi},
            f"{lo:g}bar" if lo == hi else f"{lo:g}~{hi:g}bar", "pressure_bar")
    return out


async def search_spec(product: str, *, user_id: int | None, chat=None, search=None) -> dict[str, Any]:
    """제품명 → {product, fields[제안], sources, signals, answer}. 외부 검색은 Codex(브리지) — 다른 제공자로 돌리지 않는다."""
    product = (product or "").strip()[:80]
    if not product:
        raise SpecSearchError("검색할 제품명이 없습니다.")
    search = search or codex_client.search
    if search is codex_client.search and not codex_client.is_configured():
        raise SpecSearchError("외부 AI 연결이 설정되지 않아 검색할 수 없습니다(관리자 설정 필요).")
    try:
        res = await search(f"{product} gripper datasheet specifications weight kg dimensions supply voltage current A "
                           f"air pressure bar inputs outputs", user_id=user_id)
    except codex_client.CodexError as e:
        raise SpecSearchError(str(e)) from e
    results = [r for r in res.get("results") or [] if isinstance(r, dict)]
    text = "\n\n".join([res.get("answer") or ""] + [f"{r.get('title') or ''}\n{r.get('content') or ''}" for r in results])[:TEXT_MAX]
    if not text.strip():
        return {"product": product, "fields": [], "sources": [], "signals": "", "answer": ""}
    if chat is None:
        from .product_recommend import _default_chat
        chat = _default_chat()
    data = _extract_json(await chat([{"role": "system", "content": _SYSTEM},
                                     {"role": "user", "content": f"[제품]\n{product}\n\n[검색 결과]\n{text}"}], num_predict=900))
    data = data if isinstance(data, dict) else {}
    return {"product": product, "fields": clean(data, text),
            "sources": [{"title": str(r.get("title") or r.get("url"))[:120], "url": r["url"]} for r in results if r.get("url")][:5],
            "signals": str(data.get("signals") or "")[:200], "answer": (res.get("answer") or "")[:600],
            "status": "estimated"}
