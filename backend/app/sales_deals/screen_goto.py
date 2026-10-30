# -*- coding: utf-8 -*-
"""채팅에서 영업 화면으로 이동 버튼(사용자 2026-10-28) — 'AI 제품 추천 페이지로 이동해줘'·'대여 관리 화면 열어줘'.

건 하나를 찾아 보내는 deal_goto 와 달리 화면(탭) 자체로 보낸다. 채팅은 화면을 직접 열 수 없어 버튼(`[이름](/주소)`)으로.
이동 말(이동·열어·가줘·띄워 …)과 화면 이름이 함께 있어야 가로챈다 — 방법을 묻는 말('어떻게')은 AI 대화로.
"""
from __future__ import annotations

import re

_NAV = re.compile(r"이동|가자|가\s*줘|가\s*고\s*싶|열어|열기|띄워|켜\s*줘|들어가|바로\s*가기|보여\s*줘|화면\s*줘|페이지\s*줘")
_HOWTO = re.compile(r"어떻게|방법|어디서|어디에서|뭐야|뭔가요|무엇")

# (버튼 이름, 화면 말, 주소) — 앞에서부터 맞는 것
SCREENS: list[tuple[str, re.Pattern[str], str]] = [
    ("제품 추천으로 이동", re.compile(r"제품\s*추천|추천\s*(화면|페이지|탭)|툴\s*체인저\s*추천|ATC\s*추천|AI\s*(와|랑)?\s*같이\s*찾기", re.I),
     "/proposals/recommend?tab=recommend"),
    ("견적서 수기 작성으로 이동", re.compile(r"수기\s*(작성|견적)"), "/proposals/recommend?tab=quote&new=manual"),
    ("견적서 탭으로 이동", re.compile(r"견적서?\s*(탭|화면|페이지|목록|메뉴)|견적서\s*(로|으로)\s"), "/proposals/recommend?tab=quote"),
    ("수주 진행으로 이동", re.compile(r"수주\s*진행|거래\s*명세서\s*(발급\s*)?(탭|화면|페이지|목록)|출하\s*대기|입금\s*확인\s*(탭|화면|페이지|목록)"),
     "/proposals/recommend?tab=order"),
    # 대여 제품 목록(사용자 2026-10-30) — 대여 나간 제품 한 줄씩(반납일·상태). 대여 건 자체는 제품 영업 건 관리에서
    ("대여 제품 목록으로 이동", re.compile(r"대여\s*(관리|제품|목록|탭|화면|페이지)|렌탈\s*(관리|제품|목록)|반납\s*(일|예정|목록)"),
     "/proposals/recommend?tab=rentals"),
    ("제품 영업 건 관리로 이동", re.compile(r"판매\s*관리|영업\s*건\s*(관리|목록|리스트|탭|화면|페이지)|건\s*목록|딜\s*목록|영업\s*관리"),
     "/proposals/recommend?tab=deals"),
    ("승인 요청으로 이동", re.compile(r"승인\s*요청"), "/proposals/recommend?tab=memory&sub=approvals"),
    ("제품·A/S로 이동", re.compile(r"A\s*/\s*S\s*(탭|화면|페이지|기록|목록)|제품\s*·?\s*A\s*/\s*S|S\s*/\s*N\s*찾기|무상\s*기간\s*기본값", re.I),
     "/proposals/recommend?tab=units"),
    ("검토·승인으로 이동", re.compile(r"검토\s*·?\s*승인|학습\s*내용"), "/proposals/recommend?tab=memory"),
    ("제안서 만들기로 이동", re.compile(r"제안서"), "/proposals/new"),
]


def detect(messages: list[dict]) -> list[tuple[str, str]] | None:
    """마지막 사용자 말이 영업 화면으로 가자는 말이면 [(버튼 이름, 주소)] · 아니면 None."""
    if not messages or messages[-1].get("role") != "user":
        return None
    t = str(messages[-1].get("content") or "").strip()
    if not t or len(t) > 80 or not _NAV.search(t) or _HOWTO.search(t):
        return None
    hits = [(label, href) for label, pat, href in SCREENS if pat.search(t)]
    return hits[:3] or None


def answer(hits: list[tuple[str, str]]) -> str:
    names = ", ".join(label.replace("으로 이동", "").replace("로 이동", "") for label, _ in hits)
    return (f"{names} 화면은 아래 버튼을 눌러 여실 수 있습니다(채팅에서는 화면을 바로 열 수 없어 버튼으로 이동합니다).\n"
            + "  ".join(f"[{label}]({href})" for label, href in hits))
