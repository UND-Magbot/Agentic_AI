"""컨설팅 사례집 검색·관련성 판정 회귀 데이터셋.

제안서에서 가장 경계할 실패 = 엉뚱한 사례가 참고 사례로 들어가 제안 방향이 주제와 어긋나는 것.
그래서 "맞는 사례가 오는가" 만큼 "틀린 사례가 절대 안 오는가" 를 검사한다.

필드:
  name          : 케이스 식별자
  query         : 요청 원문(또는 짧은 요약) — 서비스와 같이 앞 600자로 검색
  top1_any      : 검색 1순위가 이 중 하나여야 함 (없으면 검사 안 함)
  never         : 검색 결과에 있으면 안 되는 사례 — 과거 실제 사고 재현
  expect_empty  : 사례집 밖 공정 — 검색 결과가 비어야 함
  judge_keep_any: (--with-llm) 관련성 판정 후 이 중 하나 이상은 남아야 함
  judge_never   : (--with-llm) 관련성 판정이 반드시 버려야 하는 사례
  judge_empty   : (--with-llm) 관련성 판정 후 남는 사례가 없어야 함 — 검색 점수가 고르게 퍼져
                  검색 계층에서 못 거르는 사례집 밖 공정(점수 규칙으로 막으면 정상 질의도 막힘)
사례 번호는 casebook.CaseHit.ref ("2024-03" = 2024 사례집 #03).
"""

WELD_REQUEST = """굴삭기 어태치먼트(브레이커 하우징) 용접 라인 자동화 검토 요청.
현재 가접 후 4면 본용접을 작업자 1인이 수동으로 하고 있고, 용접사 숙련도에 따라 품질 편차가 큼.
예열은 토치로 약 180℃, 제품 무게 350kg, 크기 1,200 x 600 x 500mm 수준.
신규 용접사 채용이 어려워 로봇 용접 도입 희망. 지그 고정 방식과 포지셔너 필요 여부 검토 필요.
용접 비드 품질 검사 방법도 제안 바람. 품종은 8종, 다품종 소량.
예산·일정은 미정, 우선 PoC 범위 협의 원함."""

FISH_REQUEST = """OO식품 어묵 포장 라인 자동화 문의.
꼬치 어묵을 작업자 4명이 트레이에 수작업으로 담고 있음. 트레이당 10개, 라인 속도는 분당 약 60개.
제품이 뜨겁고 기름기가 있어 파지가 까다로움. 제품 형상 편차가 있음.
위생(HACCP) 기준 충족 필요, 세척 가능한 그리퍼 요구.
로봇 몇 대가 필요한지, 비전으로 위치 인식이 가능한지 검토 요청."""

# 2026-09-28 사고: 업종 설명 오일치("센터"→임상교육센터, "제어"→로봇 제어기)로 임플란트 드릴 사례가 1순위.
CHEM_LONG_REQUEST = """한화로봇 14kg Class 100 되는지 확인. 기존 전용장비에서 센서 쓰던 것을 비전으로 대체, 보틀 목이 기울어진 경우 검출.
로봇 투입 → 비전으로 X,Y,Z 센터 잡은 후 너트러너로 뚜껑 풀기 → 액주입장비로 이동, 비전 XY 확인 후 병 놓기 →
필링 후 뚜껑 잠그기. 불량 시 병 치우기. 약액 주입 210초, 빈 병 1.5kg, 액 3.7kg.
비전은 Keyence 또는 리얼센스 검토, 로봇 장착형인지 고정형인지 검토. 제어는 PLC/IPC. 비전 대수 산정, CT 검토 필요.
비전 포지셔닝 표기 요청."""

CHEM_SHORT_REQUEST = ("한화로봇 14kg 약액 주입 공정. 로봇 투입 -> 비전 센터링 -> 너트러너로 뚜껑 풀기 -> 액주입 -> "
                      "불량 시 병 치우기 -> 뚜껑 잠그기. 약액 주입 210초.")

CASES: list[dict] = [
    # ── 실제 요청 원문 ───────────────────────────────────────────────────────
    {"name": "req_weld_breaker", "query": WELD_REQUEST, "top1_any": ["2024-03"],
     "judge_keep_any": ["2024-03"]},
    {"name": "req_fish_packing", "query": FISH_REQUEST, "top1_any": ["2024-12"],
     "judge_keep_any": ["2024-12"]},
    # 판정 사고(2026-09-28): 로봇 가반하중 14kg 를 "중량물", 피로도를 공통 문제로 보고 피스톤링 연마 채택.
    # 아이스팩(액체 충전·불량 배출)·세라믹 검사(센서→비전 대체)는 기술 문제를 실제로 공유 → 허용.
    {"name": "req_chem_long", "query": CHEM_LONG_REQUEST, "never": ["2024-19"],
     "judge_never": ["2024-19", "2024-21"]},
    {"name": "req_chem_short", "query": CHEM_SHORT_REQUEST, "top1_any": ["2023-20", "2023-01"],
     "never": ["2024-19"], "judge_keep_any": ["2023-20", "2023-01"], "judge_never": ["2024-19", "2024-21"]},
    # ── 공정별 짧은 질의 (2024) ─────────────────────────────────────────────
    {"name": "weld_generic", "query": "용접 로봇 자동화 숙련공 부족",
     "top1_any": ["2024-03", "2024-13", "2023-12", "2023-18", "2023-20"]},
    {"name": "amr_logistics", "query": "AMR 물류 이송 자동화", "top1_any": ["2024-02"],
     "judge_keep_any": ["2024-02"]},
    {"name": "cnc_tending", "query": "CNC 머신텐딩 로봇", "top1_any": ["2024-06"]},
    {"name": "depalletizing", "query": "박스 디팔렛타이징 비전", "top1_any": ["2024-04"]},
    {"name": "vision_inspection", "query": "비전 검사 불량 선별", "top1_any": ["2024-15"]},
    {"name": "press_unload", "query": "프레스 가공품 취출 적재", "top1_any": ["2024-07", "2024-22"]},
    {"name": "food_packing", "query": "식품 포장 공정 로봇", "top1_any": ["2024-12", "2023-08"]},
    {"name": "plasma_cut", "query": "로봇 플라즈마 절단 공정 자동화", "top1_any": ["2024-01"]},
    {"name": "diecasting", "query": "다이캐스팅 제품 취출 로봇 자동화", "top1_any": ["2024-08"]},
    {"name": "stapling", "query": "쿠션 제품 타카 작업 로봇 자동화", "top1_any": ["2024-18"]},
    {"name": "grinding_ring", "query": "피스톤링 연마 공정 로봇", "top1_any": ["2024-21"]},
    # ── 공정별 짧은 질의 (2023) ─────────────────────────────────────────────
    {"name": "powder_coating", "query": "방화문 분체 도장 공정 로봇 도장", "top1_any": ["2023-17"]},
    {"name": "press_brake", "query": "철판 절곡기 소재 로딩 언로딩 로봇", "top1_any": ["2023-03"]},
    {"name": "hot_forging", "query": "열간 단조 공정 소재 투입 로봇", "top1_any": ["2023-16"]},
    {"name": "crab_preprocess", "query": "붉은대게 전처리 공정 자동화", "top1_any": ["2023-10"]},
    {"name": "solar_module", "query": "태양광 모듈 생산 라인 자동화", "top1_any": ["2023-11"]},
    {"name": "bakery", "query": "빵 제조 공정 로봇 자동화", "top1_any": ["2023-19"]},
    {"name": "forklift_plt", "query": "무인 지게차 PLT 물류 이송", "top1_any": ["2023-14"]},
    # ── 사례집 밖 공정 — 검색 단계에서 비어야 함 ────────────────────────────
    {"name": "neg_gas_station_patrol", "query": "주유소 4족 보행 로봇 순찰", "expect_empty": True},
    # ── 사례집 밖 공정 — 검색은 통과하지만(무관 사례 0.56 안팎 고르게) 관련성 판정이 막아야 함 ─
    {"name": "neg_office_cleaning", "query": "사무실 바닥 청소 로봇 도입 검토", "judge_empty": True},
    {"name": "neg_hospital_delivery", "query": "병원 병동 간 약품 배송 로봇 도입", "judge_empty": True},
    {"name": "neg_restaurant_serving", "query": "식당 서빙 로봇 도입 검토", "judge_empty": True},
    {"name": "neg_drone_powerline", "query": "드론으로 송전탑 애자 점검", "expect_empty": True},
    {"name": "neg_wms_software", "query": "물류창고 WMS 소프트웨어 구축 및 재고 관리 시스템", "expect_empty": True},
]
