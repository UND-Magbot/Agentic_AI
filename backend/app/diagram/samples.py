"""샘플 스펙.

두 가지 용도:
  1) 렌더러 회귀 확인 — 스펙이 그림으로 정확히 떨어지는지
  2) LLM few-shot 예시 — gemma3 에게 "이런 JSON 을 뱉어라" 고 보여주는 기준 답안

내용은 기존 제안서 개념도(약액 주입 공정) 에서 읽어낸 항목을 옮긴 것이다.
"""
from __future__ import annotations

from .spec import (
    Callout,
    ConceptMapSpec,
    Flow,
    ImageRef,
    InspectionItem,
    LineLayout,
    ProcessStep,
    SpecTable,
    Station,
    SysEdge,
    SysNode,
    SystemDiagram,
    VisionCam,
)


def chemical_filling_line() -> ConceptMapSpec:
    """약액 주입 자동화 공정 개념도 (협동로봇 14kg + 비전 4대)."""
    return ConceptMapSpec(
        title="약액 주입 공정 개념도 (협동로봇 14kg + 비전시스템)",
        subtitle="기존 센서 기반 전용장비 → 비전으로 대체하여 유연하고 정밀한 공정",
        process_steps=[
            ProcessStep(
                title="투입 / 반제 적치",
                bullets=["투입부에 반제품 적치", "비전으로 X, Y, Z 센터 확인"],
                image=ImageRef(symbol="conveyor"),
            ),
            ProcessStep(
                title="캐핑 (마개 열기)",
                bullets=["스크류 캡 개방", "로봇이 용기 파지"],
                image=ImageRef(symbol="capper"),
            ),
            ProcessStep(
                title="이송 (주입 위치로 이동)",
                bullets=["로봇이 용기를 들고", "약액 주입 위치로 이동"],
                image=ImageRef(symbol="robot"),
            ),
            ProcessStep(
                title="주입 전 비전 검사",
                bullets=["주입 위치에서 X, Y 확인", "노즐 정렬 미세 보정"],
                image=ImageRef(symbol="filler"),
            ),
            ProcessStep(
                title="약액 주입",
                bullets=["주입량 및 속도 제어", "정밀 정량 주입"],
                image=ImageRef(symbol="filler"),
                badge="주입시간\n210초",
                badge_tone="amber",
            ),
            ProcessStep(
                title="주입 실패 시 처리",
                bullets=["주입량 미달 시 리젝 존 이송", "재작업 또는 폐기 분류"],
                image=ImageRef(symbol="reject"),
            ),
            ProcessStep(
                title="캐핑 (마개 잠그기)",
                bullets=["주입 완료 후 캐핑 존 복귀", "스크류 캡 체결"],
                image=ImageRef(symbol="capper"),
            ),
            ProcessStep(
                title="완제품 배출",
                bullets=["최종 외관 비전 검사", "합격품 배출 컨베이어"],
                image=ImageRef(symbol="outfeed"),
                badge="OK",
                badge_tone="green",
            ),
        ],
        line_layout=LineLayout(
            stations=[
                Station(id="infeed", kind="conveyor", label="투입 존",
                        sublabel="(반제 적치)", width=1.15),
                Station(id="cap", kind="capper", label="캐핑 존",
                        sublabel="(마개 열기/잠그기)", width=1.0),
                Station(id="rbt", kind="robot", label="", width=1.15,
                        show_zone_label=False),
                Station(id="fill", kind="filler", label="약액 주입 존",
                        sublabel="(주입시간: 210초)", width=1.1),
                Station(id="tank", kind="tank", label="", width=0.62,
                        show_zone_label=False),
                Station(id="rej", kind="reject", label="", width=0.72,
                        show_zone_label=False),
                Station(id="out", kind="outfeed", label="", width=1.25,
                        show_zone_label=False),
            ],
            cameras=[
                VisionCam(id="v1", at="infeed", label="비전 1",
                          caption="(정렬/자세 확인)", offset=0.30),
                VisionCam(id="v4", at="cap", label="비전 4",
                          caption="(투입 제품 확인)", offset=0.62),
                VisionCam(id="v2", at="fill", label="비전 2",
                          caption="(주입 전 X·Y 확인)", offset=0.46),
                VisionCam(id="v3", at="out", label="비전 3",
                          caption="(외관 검사 : 누액, 기울어짐)", offset=0.34),
            ],
            callouts=[
                Callout(at="rbt", offset=0.52,
                        lines=["협동로봇", "가반중량 : 14 kg", "용도 : 용기 이송·주입"]),
            ],
            flows=[
                Flow(src="infeed", dst="cap"),
                Flow(src="cap", dst="rbt"),
                Flow(src="rbt", dst="fill"),
                Flow(src="fill", dst="rej", style="dashed",
                     label="주입 실패 시 불량/리젝 존"),
                Flow(src="rej", dst="out"),
            ],
            outfeed_label="완제품 배출",
        ),
        inspections=[
            InspectionItem(title="비전 1 : 투입/자세",
                           detail="반제품 정렬 상태 및 X·Y·Z 센터 확인",
                           image=ImageRef(symbol="conveyor")),
            InspectionItem(title="비전 2 : 주입 전 정렬",
                           detail="노즐 대비 용기 입구 X·Y 편차 측정",
                           image=ImageRef(symbol="filler")),
            InspectionItem(title="비전 3 : 외관 검사",
                           detail="누액·기울어짐·캡 체결 상태 판정",
                           image=ImageRef(symbol="outfeed")),
            InspectionItem(title="비전 4 : 투입 제품 확인",
                           detail="품번 혼입 방지 및 캡 유무 확인",
                           image=ImageRef(symbol="capper")),
        ],
        inspections_title="비전 검사 항목",
        system_diagram=SystemDiagram(
            nodes=[
                SysNode(id="c1", label="비전 카메라", sublabel="4 EA", kind="camera", col=0),
                SysNode(id="ipc", label="비전 IPC", sublabel="검사 · 판정", kind="ipc", col=1),
                SysNode(id="plc", label="PLC", sublabel="설비 제어", kind="plc", col=2),
                SysNode(id="rc", label="로봇 컨트롤러", sublabel="협동로봇",
                        kind="controller", col=3),
            ],
            edges=[
                SysEdge(src="c1", dst="ipc", label="영상"),
                SysEdge(src="ipc", dst="plc", label="판정 결과"),
                SysEdge(src="plc", dst="rc", label="동작 지령"),
            ],
        ),
        system_title="시스템 구성도",
        tables=[
            SpecTable(
                title="예상 Cycle Time",
                headers=["공정", "시간"],
                rows=[
                    ["투입 · 정렬", "10 ~ 15 초"],
                    ["캐핑 (개방)", "8 ~ 12 초"],
                    ["이송 · 정렬", "10 ~ 15 초"],
                    ["약액 주입", "210 초"],
                    ["캐핑 (체결)", "8 ~ 12 초"],
                    ["배출 · 검사", "10 초"],
                    ["합계", "약 256 ~ 274 초"],
                ],
                accent=True,
            ),
            SpecTable(
                title="주요 구성 설비",
                headers=["항목", "사양"],
                rows=[
                    ["협동로봇", "가반중량 14 kg"],
                    ["비전 카메라", "4 EA"],
                    ["주입 유닛", "정량 펌프 방식"],
                ],
            ),
        ],
        footnote=(
            "※ Cycle Time 은 약액 주입 210 초를 포함한 값이며, 설비 조건에 따라 변동될 수 있음."
        ),
    )
