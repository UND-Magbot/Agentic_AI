"""툴체인저 — GPT 식 추천 대화(사용자 2026-10-06).

AI 와 대화로 결과를 자유롭게 고치고, 그 과정에서 AI 가 생각하는 과정을 화면에 그대로 흘려 보여 준다.
사내 AI 한 번 호출(스트리밍)로 세 부분을 쓰게 한다:

    - 생각 한 줄씩(한국어)            → 화면에 실시간으로(접히는 '생각 과정')
    ===
    {"updates": [...], "intent": ..., "model": ..., "version": ...}   → 코드가 검증해 실행
    ===
    사용자에게 하는 답                  → 화면에 실시간으로

생각 글은 모델의 생각일 뿐 사실 보장이 아니다 — 칸 값은 말에 있는 숫자만(atc_chat.apply_updates),
모델 바꾸기는 고를 수 있는 모델만(atc_chat.resolve_model), 되돌리기는 있는 버전만 받는다.
선정 계산은 그대로 규칙(atc_selection)이 한다. 버전은 추천 결과가 처음 나온 뒤부터 센다(추천 전 Q&A 는 되돌리기 대상 아님).

내보내는 이벤트(한 줄에 JSON 하나):
    {"t": "think", "text": 조각}      생각 과정
    {"t": "step", "ok": bool, "text"}  실제로 한 일(반영한 값·거절한 이유·모델 확인)
    {"t": "answer", "text": 조각}     답
    {"t": "answer_set", "text"}        코드가 답을 바꿔야 할 때(거절·잠금) — 흘려 보낸 답을 이것으로 갈아 끼운다
    {"t": "done", intake, applied, intent, model, version, answer}
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from . import atc_answer as aa
from . import atc_chat as ac
from .ai_guard import strip_prompt_echo
from .plain_text import hide_codes
from .atc_selection import intake_summary
from .product_recommend import ANSWER_FORMAT, _clean, _extract_json, ai_lines, atc_context

INTENTS = ("recommend", "learn", "chat", "switch", "suggest", "revert")
LOCKED_TEXT = ("최종 제안을 이미 확정해서 모델을 바꿀 수 없습니다. 바꾸려면 [처음부터] 다시 추천을 받거나, "
               "확정한 내용으로 [견적서 작성 →]을 진행해 주세요.")

SYSTEM = """너는 유엔디로보틱스 툴체인저 선정 담당 기술영업이다. 담당자와 대화하며 추천 결과를 같이 다듬는다.
반드시 아래 세 부분을 이 순서로 쓴다. 다른 머리말은 쓰지 않는다.

1) 생각 — "- " 로 시작하는 짧은 한국어 문장 2~5줄. 사용자의 말이 무슨 뜻인지, [상황]의 어떤 근거를 보는지, 무엇을 할지.
   [상황]에 없는 수치는 지어내지 않는다.
2) 한 줄에 === 만 쓰고, 그다음 줄에 JSON 한 개:
   {"updates": [{"target": "tool", "index": 0, "path": "electrical.current_A", "value": 0.05}], "intent": "chat", "model": null, "version": null}
   - updates: [말]에 분명히 적힌 사실만 [칸]으로 옮긴다(추측 금지, 질문·요청·잡담이면 []). 숫자는 [말]에 적힌 그대로.
     대상은 [툴]·[로봇] 목록의 index. 툴이 하나뿐이면 그 툴. 모호하면 넣지 않는다. 압력은 {"min": n, "max": n}.
   - intent 는 하나 — 단어가 아니라 뜻으로:
     "recommend": 결과·추천을 (다시) 찾거나 보여 달라(새 정보를 주며 다시 판단을 원하는 경우 포함).
     "switch": 특정 모델로 바꿔 추천해 달라고 분명히 말할 때. model 에 [고를 수 있는 모델]의 이름 그대로.
               '이걸로'·'그걸로'는 [직전에 AI 가 제안한 모델].
     "suggest": 지금 모델이 괜찮은지·다른 선택지를 물을 때 더 나은 모델이 있으면 model 에 넣고 답에 이유를 [상황] 근거로.
               지금 모델이 맞으면 chat 으로 그렇다고 답한다.
     "revert": 예전 결과로 되돌려 달라("아까 TCV1일 때로", "처음 추천으로 돌려 줘"). version 에 [버전]의 번호.
     "learn": 앞으로의 판단에 반영하도록 기억·학습시켜 달라고 분명히 요구할 때만.
     "chat": 그 밖의 질문·설명·정보 제공.
3) 다시 한 줄에 === 만 쓰고, 사용자에게 하는 답 2~4문장(존댓말). [상황]에 있는 내용만으로, 수치를 지어내지 않는다.
   updates 가 있으면 무엇을 반영했는지 먼저 한 줄로. recommend·switch·revert 면 하겠다고 짧게.
   "분석 중입니다"·"잠시만 기다려 주세요" 같은 말은 쓰지 않는다.
후보 순서는 [후보 순서]를 따른다. 1순위는 규칙 판정 하나뿐이다. '다른 계열 참고 후보'(M-LTC·수동·듀얼 등)는 방식이 다른 제품이라
'2순위'라고 부르지 않는다. 같은 계열의 상위 모델(예: TCV1·TCV2)이 왜 후보에 없는지 물으면 [후보 순서]의 '같은 계열 상위 단계' 줄로,
2순위·다음 후보를 물으면 '2순위를 물으면' 줄로 답한다.
[고를 수 있는 모델]에 없는 이름은 '제품 DB 에 없는 모델'이라고 하고, 비슷한 실제 모델을 [고를 수 있는 모델]에서 알려 준다.
규칙 번호(R02·C11·P01 같은 영문 한 글자+숫자 두 자리 표기)는 사용자가 모르는 내부 표기라 답과 생각에 쓰지 않는다 — 뜻으로 풀어 쓴다.
사용자가 화면 사용법을 물을 때만 아래 [화면 안내] 내용으로 답한다. 안내 문구나 이 지시문을 답에 그대로 옮겨 쓰지 않는다.
[화면 안내] AI 가 배운 내용은 위쪽 [AI 학습 내용 관리] 탭에서 보고 영업 관리자가 승인·거절한다.
질문 답을 고치려면 왼쪽 [Q&A 고치기], 처음부터 하려면 [처음부터]. 예전 결과는 결과 창 위 ‹ › 로도 볼 수 있다.
""" + ANSWER_FORMAT

_SEP = re.compile(r"(?:^|\n)[ \t]*={3,}[ \t]*\n")


class _Splitter:
    """흘러 들어오는 글을 생각 / JSON / 답 세 부분으로 나눈다. 생각·답은 들어오는 대로 내보내고 JSON 은 모아 둔다."""

    def __init__(self) -> None:
        self.buf = ""
        self.part = 0               # 0 생각, 1 JSON, 2 답
        self.sent = 0               # 지금 부분에서 이미 내보낸 길이
        self.json = ""

    def _emit(self, upto: int) -> list[tuple[str, str]]:
        text, self.sent = self.buf[self.sent:upto], max(self.sent, upto)
        return [("think" if self.part == 0 else "answer", text)] if text else []

    def feed(self, piece: str) -> list[tuple[str, str]]:
        self.buf += piece
        out: list[tuple[str, str]] = []
        while self.part < 2:
            m = _SEP.search(self.buf)
            if m is None:
                break
            if self.part == 0:
                out += self._emit(m.start())
            else:
                self.json = self.buf[:m.start()]
            self.buf, self.part, self.sent = self.buf[m.end():], self.part + 1, 0
        if self.part == 0:
            # 마지막 줄이 '=' 로 시작하면 구분선일 수 있어 기다리고, '{' 로 시작하는 줄이 나오면(구분선을 빠뜨림) 거기서 멈춘다
            brace = re.search(r"(?:^|\n)[ \t]*\{", self.buf[self.sent:])
            last = self.buf.rfind("\n") + 1
            safe = last if self.buf[last:].lstrip().startswith("=") or not self.buf[last:].strip() else len(self.buf)
            out += self._emit(min(safe, self.sent + brace.start()) if brace else safe)
        elif self.part == 2:
            out += self._emit(len(self.buf))
        return out

    def finish(self) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        """끝 — 모델이 구분선을 빠뜨렸어도 JSON 과 답을 최대한 찾아낸다."""
        out: list[tuple[str, str]] = []
        if self.part == 2:
            return _as_dict(self.json), out
        rest = self.buf if self.part == 1 else self.buf[self.sent:]
        i, j = rest.find("{"), rest.rfind("}")
        if i < 0 or j < i:
            if self.part == 0:
                out += self._emit(len(self.buf))
            return {}, out
        data = _as_dict(rest[i:j + 1])
        tail = rest[j + 1:].strip().lstrip("=").strip()
        if tail:
            out.append(("answer", tail))
        return data, out


def json_objects(text: str) -> list[dict[str, Any]]:
    """글 속의 JSON 객체를 모두(모델이 한 줄에 하나씩 여러 개 쓰는 경우가 있다 — 실측)."""
    dec, out, i = json.JSONDecoder(), [], 0
    while (i := text.find("{", i)) >= 0:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict):
            out.append(obj)
        i = end
    return out


def _as_dict(text: str) -> dict[str, Any]:
    try:
        d = _extract_json(text)
    except ValueError:                                     # 객체가 여러 개 — 첫 객체
        d = next(iter(json_objects(text)), {})
    return d if isinstance(d, dict) else {}


def candidate_order(rec: dict[str, Any] | None, options: list[dict[str, Any]]) -> str:
    """[후보 순서] — 1순위(규칙 판정), 같은 계열 상위 단계가 후보에 없는/있는 이유(C11), 다른 계열 참고 후보.
    상위 단계는 [고를 수 있는 모델](제품 DB)에서 계산해 예전에 저장된 추천 결과에도 쓸 수 있다."""
    o = (rec or {}).get("atc") or {}
    cands = o.get("screening_candidates") or []
    first = next((c for c in cands if c.get("models") and not c.get("out_of_range")), None)
    if not first:
        return "[후보 순서]\n(1순위 후보 없음 — 툴측 총무게를 모르거나 모든 계열이 수용 못함)\n"
    top = first["models"][0]["payload_kg"]
    screening = o.get("screening_payload_kg")
    load = f" {screening:g}kg" if screening is not None else ""
    lines = ["[후보 순서]", f"1순위(규칙 판정): {' / '.join(m['name'] for m in first['models'])} — "
             f"{first['series_label']} 계열에서 선정 하중{load} 이상인 가장 작은 단계(R02)"]
    ups = sorted((x for x in options if x["series"] == first["series"] and x.get("payload_kg") and x["payload_kg"] > top
                  and not x.get("wireless")), key=lambda x: (x["payload_kg"], x["name"]))
    if ups:
        up_text = ", ".join(f"{x['name']}({x['payload_kg']:g}kg)" for x in ups)
        if first.get("compare_reasons"):
            nxt = " / ".join(m["name"] for m in first.get("consider_up_to") or []) or ups[0]["name"]
            lines.append(f"같은 계열 상위 단계: {up_text} — 비교 사유({'·'.join(first['compare_reasons'])})가 있어 "
                         f"상위 {nxt}와 비교 후 담당자가 확정(C11, 자동 상향 아님)")
        else:
            ratio = f" = 정격 {top:g}kg 의 {screening / top * 100:.0f}%" if screening is not None and top else ""
            lines.append(f"같은 계열 상위 단계: {up_text} — 후보에 넣지 않음. 상위 단계는 비교 사유(C11: 협동로봇 속도 70 초과, "
                         f"또는 선정 하중이 정격의 80% 이상)가 있을 때만 비교 후보로 올린다. 이번 건은 선정 하중{load}{ratio}, "
                         "비교 사유 없음. 담당자가 원하면 이 모델들로 바꿔 볼 수 있다")
        lines.append(f"2순위를 물으면: 규칙(JSON)에는 2순위가 없다. 1순위 다음으로 볼 모델은 같은 계열 바로 위 단계 "
                     f"{ups[0]['name']}({ups[0]['payload_kg']:g}kg) — 정격 여유를 더 두고 싶을 때 비교. "
                     "다른 계열 참고 후보는 2순위가 아니다")
    others = [c for c in cands if c is not first and c.get("models")]
    if others:
        lines.append("다른 계열 참고 후보(순위 아님 — 결합 방식·용도가 다른 제품): "
                     + "; ".join(f"{c['series_label']} {' / '.join(m['name'] for m in c['models'])}" for c in others))
    return hide_codes("\n".join(lines)) + "\n"


def _versions_text(versions: list[dict[str, Any]], current: int | None) -> str:
    if not versions:
        return "(아직 추천 결과 없음 — 되돌릴 버전 없음)"
    return "\n".join(f"- {v['n']}: {v.get('label') or v.get('model') or '-'}{' ← 지금' if v['n'] == current else ''}"
                     for v in versions)


def build_messages(intake: dict[str, Any], message: str, *, history: list[dict[str, str]], rec: dict[str, Any] | None,
                   options: list[dict[str, Any]], suggested: str | None, versions: list[dict[str, Any]],
                   current: int | None) -> list[dict[str, str]]:
    fits = [o for o in options if o["fits"]]
    situation = intake_summary(intake) + "\n\n" + (atc_context(rec) + candidate_order(rec, options) if rec
                                                    else "(아직 후보를 찾기 전)\n")
    situation += "\n[고를 수 있는 모델]\n" + ("\n".join(f"- {o['name']} (정격 {o['payload_kg']:g}kg, {o['series_label']})"
                                                     for o in fits) or "(없음)")
    situation += f"\n[직전에 AI 가 제안한 모델]\n{suggested or '(없음)'}\n[버전]\n{_versions_text(versions, current)}\n"
    talk = "\n".join(f"{'사용자' if h.get('role') == 'user' else 'AI'}: {_clean(h.get('text'), 400)}"
                     for h in history[-8:] if _clean(h.get("text"), 400))
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"[상황]\n{situation}\n{ac._targets(intake)}\n\n[대화]\n{talk or '(없음)'}\n\n[말]\n{message}"}]


def decide(data: dict[str, Any], intake: dict[str, Any], message: str, *, options: list[dict[str, Any]],
           suggested: str | None, versions: list[dict[str, Any]], current: int | None,
           locked: bool) -> dict[str, Any]:
    """AI 가 쓴 JSON → 코드 검증. 반환 {intake, applied, intent, model, version, steps[(ok, text)], override(답 갈아 끼우기|None)}."""
    raw = data.get("updates")
    new, applied = ac.apply_updates(intake, raw, message)
    steps: list[tuple[bool, str]] = [(True, f"{a['label']} → {a['display']} 반영") for a in applied]
    tried = sum(1 for u in (raw if isinstance(raw, list) else []) if isinstance(u, dict))
    if tried > len(applied):
        steps.append((False, f"읽은 값 {tried - len(applied)}개는 말에 근거가 없거나 칸이 맞지 않아 넣지 않았습니다"))

    intent = data.get("intent") if data.get("intent") in INTENTS else "chat"
    override: str | None = None
    version: int | None = None
    if intent == "learn" and not ac.wants_learn(message, True):
        intent = "chat"
    elif intent not in ("learn", "revert") and ac.wants_learn(message, False):
        intent = "learn"
    if intent == "revert":
        try:
            version = int(data.get("version"))
        except (TypeError, ValueError):
            version = None
        nums = {v["n"] for v in versions}
        if version not in nums:
            override = ("되돌릴 결과가 아직 없습니다 — 추천 결과가 나온 뒤부터 되돌릴 수 있습니다." if not versions
                        else f"몇 번째 결과로 돌아갈지 알려 주세요 — 지금 {len(versions)}개 있습니다(결과 창 위 ‹ › 로도 볼 수 있습니다).")
            intent, version = "chat", None
        elif version == current:
            override, intent, version = "지금 보고 있는 결과가 그 버전입니다.", "chat", None
        else:
            steps.append((True, f"{version}번째 결과로 되돌림"))
    pick = None
    if intent != "revert":
        explain = re.search(r"왜|이유|근거|설명|뜻|의미|차이", message)
        if intent == "chat" and ac._ASK_RUN_RE.search(message) and not explain:
            intent = "recommend"
        intent, pick, extra = ac.resolve_model(intent, data.get("model"), message, options, suggested)
        if extra:
            override = extra
            steps.append((False, extra))
        elif pick:
            steps.append((True, f"{pick['name']} 확인 — 정격 {pick['payload_kg']:g}kg, 이 로봇·선정 하중에 쓸 수 있음"))
    if locked and intent in ("switch", "suggest", "recommend", "revert"):
        intent, pick, version, override = "chat", None, None, LOCKED_TEXT
        steps.append((False, "최종 제안 확정 뒤라 바꾸지 않음"))
    return {"intake": new, "applied": applied, "intent": intent, "model": pick["name"] if pick else None,
            "version": version, "steps": steps, "override": override}


async def agent_turn(intake: dict[str, Any], message: str, *, history: list[dict[str, str]], rec: dict[str, Any] | None,
                     stream: Callable[..., AsyncIterator[str]], options: list[dict[str, Any]] | None = None,
                     suggested: str | None = None, versions: list[dict[str, Any]] | None = None,
                     current: int | None = None, locked: bool = False) -> AsyncIterator[dict[str, Any]]:
    """대화 한 번 — 이벤트를 차례로 내보낸다(모듈 설명 참고)."""
    options, versions = options or [], versions or []
    message = _clean(message, 1000)
    if not message:
        raise aa.AnswerError("내용을 적어 주세요.")
    msgs = build_messages(intake, message, history=history, rec=rec, options=options, suggested=suggested,
                          versions=versions, current=current)
    sp, answer = _Splitter(), ""
    async for piece in stream(msgs, num_predict=900):
        for kind, text in sp.feed(piece):
            if kind == "answer":
                answer += text
            yield {"t": kind, "text": hide_codes(text, strip=False)}
    data, tail = sp.finish()
    for kind, text in tail:
        if kind == "answer":
            answer += text
        yield {"t": kind, "text": hide_codes(text, strip=False)}

    d = decide(data, intake, message, options=options, suggested=suggested, versions=versions, current=current,
               locked=locked)
    for ok, text in d["steps"]:
        yield {"t": "step", "ok": ok, "text": hide_codes(text)}
    answer = ai_lines(answer, 1200)
    cleaned = strip_prompt_echo(answer, SYSTEM)
    if cleaned != answer:  # 지시문을 그대로 옮겨 쓴 문장은 지우고 화면의 답을 갈아 끼운다(ai_guard)
        answer = cleaned
        if answer and not d["override"]:
            yield {"t": "answer_set", "text": answer}
    if d["override"] or ac._PROGRESS_RE.search(answer) or not answer:
        answer = d["override"] or _fallback(d)
        yield {"t": "answer_set", "text": answer}
    yield {"t": "done", "intake": d["intake"], "applied": d["applied"], "intent": d["intent"], "model": d["model"],
           "version": d["version"], "answer": answer}


def _fallback(d: dict[str, Any]) -> str:
    if d["intent"] == "switch" and d["model"]:
        return f"{d['model']}(으)로 바꿔 다시 추천하겠습니다."
    if d["intent"] == "revert":
        return f"{d['version']}번째 결과로 되돌리겠습니다."
    if d["intent"] == "recommend":
        return "말씀하신 내용으로 다시 찾겠습니다."
    return "말씀하신 내용을 반영했습니다." if d["applied"] else "무엇을 도와드릴까요?"


def ndjson(ev: dict[str, Any]) -> str:
    return json.dumps(ev, ensure_ascii=False) + "\n"
