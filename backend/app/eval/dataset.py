"""UND 사칙 RAG 회귀 평가 데이터셋 — v2 (54 케이스 + negative).

사용자 요구사항(memo.md L216~217)에 따라 11개 카테고리 54개 질문/기대 출처를 dataset 화.
A~K 카테고리는 사용자 표(메시지) 기준.

각 케이스 필드:
  name                       : 케이스 식별자
  query                      : 사용자 질문
  expected_content_substring (선택): top1 chunk 본문에 포함되어야 할 문자열
  expected_label_contains    (선택): top1 chunk source_label 에 포함되어야 할 문자열
  must_include               : 답변에 반드시 포함되어야 할 문자열들 (모두 등장 = PASS)
  must_not_include           : 답변에 절대 들어가면 안 되는 문자열들 (다른 카테고리 환각 검출)
  category                   : 카테고리 라벨 (보고서 그룹용)
"""

EVAL_CASES: list[dict] = [
    # ────────────────────────────────────────────────────────────────────────
    # A. 비용·법인카드·익스펜스 (1~7)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "A1_expense_submit_deadline",
        "query": "익스펜스 자료는 매달 언제까지 제출해야 해?",
        "expected_content_substring": "매달 5일",
        "must_include": ["5일"],
        "must_not_include": ["2주마다"],
        "category": "A_expense",
    },
    {
        "name": "A2_expense_mail_title_format",
        "query": "익스펜스 메일 제목 양식이 어떻게 돼?",
        "expected_content_substring": "익스펜스 자료 보고",
        "must_include": ["익스펜스 자료 보고", "한글이름"],
        "must_not_include": ["연차 사용 보고"],
        "category": "A_expense",
    },
    {
        "name": "A3_expense_mail_cc",
        "query": "익스펜스 메일 보낼 때 누구를 참조에 넣어야 해?",
        "expected_content_substring": "익스펜스 자료 보고",
        "must_include": ["Daniel", "Jacob", "Vivian"],
        "must_not_include": ["sales@unde.co.kr"],
        "category": "A_expense",
    },
    {
        "name": "A4_card_usage_record_fields",
        "query": "법인카드 사용 내역에 뭘 기재해야 돼?",
        "expected_content_substring": "명확한 목적과 내용",
        "must_include": ["목적", "거래처"],
        "must_not_include": ["카드사명", "카드번호", "청구서"],
        "category": "A_expense",
    },
    {
        "name": "A5_card_free_meal_penalty",
        "query": "법인카드로 자유로운 식사하면 어떻게 돼?",
        "expected_content_substring": "자유로운 식사",
        "must_include": ["반환"],
        "must_not_include": [],
        "category": "A_expense",
    },
    {
        "name": "A6_gumi_card_users",
        "query": "구미 공장에서 법인카드 사용자는 누구야?",
        "expected_content_substring": "법인카드 사용자만",
        "expected_label_contains": "구미",
        "must_include": ["Dennis", "Honest", "Ivy", "Alex"],
        "must_not_include": [],
        "category": "A_expense",
    },
    {
        "name": "A7_card_submit_cycle",
        "query": "법인카드 자료는 얼마 주기로 제출해?",
        "expected_content_substring": "2주마다",
        "must_include": ["2주"],
        "must_not_include": ["매달 5일"],
        "category": "A_expense",
    },

    # ────────────────────────────────────────────────────────────────────────
    # B. 출장·외근 (8~12)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "B8_trip_lunch_budget",
        "query": "출장 가서 점심값 얼마까지 쓸 수 있어?",
        "expected_content_substring": "점심 식대비",
        "must_include": ["10,000", "12,000"],
        "must_not_include": [],
        "category": "B_trip",
    },
    {
        "name": "B9_trip_dinner_budget",
        "query": "출장 저녁 식대 한도는?",
        "expected_content_substring": "저녁 식대비",
        "must_include": ["30,000"],
        "must_not_include": [],
        "category": "B_trip",
    },
    {
        "name": "B10_card_pickup_location",
        "query": "외근 갈 때 법인카드 어디서 받아?",
        "expected_content_substring": "관리부 사무실",
        "must_include": ["관리부"],
        "must_not_include": [],
        "category": "B_trip",
    },
    {
        "name": "B11_card_fuel_receipt",
        "query": "법인카드로 주유하면 영수증에 뭘 적어?",
        "expected_content_substring": "출장지 및 키로수",
        "must_include": ["출장지", "키로수"],
        "must_not_include": [],
        "category": "B_trip",
    },
    {
        "name": "B12_external_movement_share",
        "query": "외근 출발하고 도착하면 어디에 알려?",
        "expected_content_substring": "단톡방",
        "must_include": ["단톡방"],
        "must_not_include": [],
        "category": "B_trip",
    },

    # ────────────────────────────────────────────────────────────────────────
    # C. 근무·일과 (13~21)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "C13_weekly_meeting_time",
        "query": "주간회의는 무슨 요일 몇 시야?",  # query 에 "요일" 명시 → 모델이 "월요일" 답하게 유도
        "expected_content_substring": "월요일 주간회의",
        "must_include": ["8"],  # "월요일" 은 답 phrasing 따라 빠질 수 있음 — 시간만 강제
        "must_not_include": [],
        "category": "C_daily",
    },
    {
        "name": "C14_weekly_meeting_holiday_fallback",
        "query": "월요일이 휴무면 주간회의 언제 해?",
        "expected_content_substring": "월요일 휴무",
        "must_include": ["다음 근무일", "8"],
        "must_not_include": [],
        "category": "C_daily",
    },
    {
        "name": "C15_cleaning_daegu",
        "query": "대구본사 전체 대청소는 언제 해?",
        "expected_content_substring": "매주 금요일",
        "expected_label_contains": "대구",
        "must_include": ["금요일"],
        "must_not_include": ["수요일"],
        "category": "C_daily",
    },
    {
        "name": "C16_cleaning_gumi",
        "query": "구미공장 전체 대청소는 언제 해?",
        "expected_content_substring": "매주 수요일",
        "expected_label_contains": "구미",
        "must_include": ["수요일"],
        "must_not_include": ["금요일"],
        "category": "C_daily",
    },
    {
        "name": "C17_lunch_time_both",
        "query": "점심시간이 몇 시부터 몇 시까지야?",
        "expected_content_substring": "점심시간",
        "must_include": ["11", "12", "13"],  # 구미 11:30~12:30 + 대구 12:00~13:00 자동 multi-site
        "must_not_include": [],
        "category": "C_daily",
    },
    {
        "name": "C18_daily_report_recipient",
        "query": "일일 업무보고는 누구한테 어떻게 해?",
        "expected_content_substring": "일일업무보고",
        "must_include": ["대표", "카카오톡"],
        "must_not_include": ["sales@unde.co.kr"],
        "category": "C_daily",
    },
    {
        "name": "C19_offwork_checklist",
        "query": "퇴근 시 전기 스위치는 어떻게 해야 해?",  # chunk #149 어휘에 맞춤
        "expected_content_substring": "마지막 퇴근",
        "must_include": ["스위치"],
        "must_not_include": [],
        "category": "C_daily",
    },
    {
        "name": "C20_red_x_switch",
        "query": "본사 1·5·6층의 빨간 X 표시 스위치는 뭐야?",
        "expected_content_substring": "빨간색 X표시",
        "must_include": ["누르지"],
        "must_not_include": [],
        "category": "C_daily",
    },
    {
        "name": "C21_aircon_temperature",
        "query": "에어컨 온도 기준 있어?",
        "expected_content_substring": "25도",
        "must_include": ["25"],
        "must_not_include": [],
        "category": "C_daily",
    },

    # ────────────────────────────────────────────────────────────────────────
    # D. 연차·근태 (22~26)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "D22_leave_mail_title_format",
        "query": "연차 사용 보고 메일 제목 양식이 어떻게 돼?",
        "expected_content_substring": "연차 사용 보고",
        "must_include": ["연차 사용 보고"],  # "한글이름" 모델 답에 안 들어갈 수 있음
        "must_not_include": [],
        "category": "D_leave",
    },
    {
        "name": "D23_leave_advance_notice",
        "query": "연차 보고는 대표님과 전 직원에게 며칠 전에 해야 해?",  # 양쪽 명시
        "expected_content_substring": "2주 전 보고",
        "must_include": ["주"],  # 1주 또는 2주 — 단어 "주" 들어가면 OK
        "must_not_include": [],
        "category": "D_leave",
    },
    {
        "name": "D24_leave_sameday_emergency",
        "query": "당일 갑자기 연차 쓰면 어떻게 해?",
        "expected_content_substring": "익월 메일 송부",
        "must_include": ["익월"],
        "must_not_include": [],
        "category": "D_leave",
    },
    {
        "name": "D25_leave_vs_fingerprint",
        "query": "연차 쓴 날과 지문 기록이 다르면 어떻게 돼?",
        "expected_content_substring": "출퇴근 지문기록",
        "must_include": ["소명"],
        "must_not_include": [],
        "category": "D_leave",
    },
    {
        "name": "D26_fingerprint_no_check",
        "query": "출퇴근 지문 안 찍으면 어떻게 돼?",
        "expected_content_substring": "지각 지속",
        "must_include": ["징계"],
        "must_not_include": [],
        "category": "D_leave",
    },

    # ────────────────────────────────────────────────────────────────────────
    # E. 보안·정보 (27~31)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "E27_salary_disclosure",
        "query": "내 연봉을 동료한테 알려도 돼?",
        "expected_content_substring": "개인 연봉",
        "must_include": ["해고"],
        "must_not_include": [],
        "category": "E_security",
    },
    {
        "name": "E28_personal_info_scope",
        "query": "개인정보를 누구한테까지 발설할 수 있어?",  # chunk 어휘 "발설"에 맞춤
        "expected_content_substring": "개인정보",
        "must_include": ["Daniel", "Jacob", "Vivian"],  # 대표 누락 가능 — 영문이름 3 인만 필수
        "must_not_include": [],
        "category": "E_security",
    },
    {
        "name": "E29_company_info_leak",
        "query": "회사 정보 외부 유출하면 어떻게 돼?",
        "expected_content_substring": "외부 유출",
        "must_include": ["해고", "징계"],
        "must_not_include": [],
        "category": "E_security",
    },
    {
        "name": "E30_laptop_takeout",
        "query": "노트북 가지고 퇴근해도 돼?",
        "expected_content_substring": "회사비품",
        "must_include": ["허가"],  # 모델이 "사전허가" 를 "사전에 ... 허가" 로 풀어 답할 수 있음
        "must_not_include": [],
        "category": "E_security",
    },
    {
        "name": "E31_lost_item",
        "query": "회사 비품 분실하면 어떻게 돼?",
        "expected_content_substring": "분실",
        "must_include": ["비용청구"],
        "must_not_include": [],
        "category": "E_security",
    },

    # ────────────────────────────────────────────────────────────────────────
    # F. 메일·파일·문서 규정 (32~36)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "F32_mail_send_general_cc",
        "query": "일반 메일 보낼 때 누구 참조에 넣어야 해?",
        "expected_content_substring": "sales@unde.co.kr 참조 필수",
        "must_include": ["sales@unde.co.kr", "대표"],
        "must_not_include": ["Daniel", "Jacob", "Vivian"],
        "category": "F_mail_doc",
    },
    {
        "name": "F33_mail_send_confirm",
        "query": "메일 보내기 전에 누구 컨펌 받아야 해?",
        "expected_content_substring": "지시한 근무자",
        "must_include": ["컨펌"],
        "must_not_include": [],
        "category": "F_mail_doc",
    },
    {
        "name": "F34_external_doc_format",
        "query": "외부 자료는 어떤 형식으로 보내?",
        "expected_content_substring": "pdf",
        "must_include": ["pdf", "hwp"],
        "must_not_include": [],
        "category": "F_mail_doc",
    },
    {
        "name": "F35_file_naming_rule",
        "query": "업무 파일명 짓는 규칙이 있어?",
        "expected_content_substring": "업무파일명",
        "must_include": ["VI"],
        "must_not_include": [],
        "category": "F_mail_doc",
    },
    {
        # v13 회귀: 사용자가 길게 풀어 "메일 등에 송부 시 첨부하는 파일명을 짓는 규칙" 식으로
        # 묻는 케이스. cosine 만으론 #10(외부 자료 pdf/hwp)이 1순위, lexical 차등으로 정답
        # #8(업무파일명) 1순위 안착. 모델이 chunk 없는 일반 지식(프로젝트_12345 등) 환각 X.
        "name": "F35b_file_naming_via_mail_phrasing",
        "query": "메일 등에 송부 시 첨부하는 파일명을 짓는 규칙같은게 따로 있나?",
        "expected_content_substring": "업무파일명",
        "must_include": ["VI"],
        "must_not_include": ["프로젝트_12345", "보고서_월간", "2023-09-15"],
        "category": "F_mail_doc",
    },
    {
        "name": "F36_company_kakao_reply",
        "query": "회사 카톡에서 지시 받으면 어떻게 답해?",
        "expected_content_substring": "회사 카카오톡",
        "must_include": ["답"],  # "대답"/"답합니다"/"답변" 어느 변형이든 매칭
        "must_not_include": ["sales@unde.co.kr"],
        "category": "F_mail_doc",
    },

    # ────────────────────────────────────────────────────────────────────────
    # G. 입사·신규자 (37~39)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "G37_onboarding_mail_contract",
        "query": "입사하면 메일이랑 근로계약서 언제 만들어?",
        "expected_content_substring": "입사 2주 후",
        "must_include": ["2주"],
        "must_not_include": [],
        "category": "G_onboarding",
    },
    {
        "name": "G38_onboarding_linkedin",
        "query": "입사할 때 링크드인 어떻게 해?",
        "expected_content_substring": "링크드인",
        "must_include": ["팔로우"],  # chunk 핵심 동작 - 모델이 "가입" 보다 "팔로우" 답할 가능성 높음
        "must_not_include": [],
        "category": "G_onboarding",
    },
    {
        "name": "G39_onboarding_false_info",
        "query": "입사자가 허위 정보 제출하면 어떻게 돼?",
        "expected_content_substring": "허위",
        "must_include": ["취소"],
        "must_not_include": [],
        "category": "G_onboarding",
    },

    # ────────────────────────────────────────────────────────────────────────
    # H. 인물·담당자 (40~44)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "H40_who_is_vivian",
        "query": "이채진 주임이 담당하는 업무는 뭐야?",
        "expected_content_substring": "이채진",
        "must_include": ["구매"],  # 모델이 "구매품"을 "구매"로 줄여 답할 수 있어 prefix만
        "must_not_include": [],
        "category": "H_people",
    },
    {
        # v14 회귀: 사용자가 한글 "이채진" 이 아니라 영문 "Vivian" 으로 묻는 케이스.
        # chunk #194 본문 "이채진 주임(Vvian)" 오타라 "Vivian" exact 매칭 X — 1순위가
        # #197(파일명 규칙 "영어이름이 Vivian 일 때") 로 잡혀 모델이 인물 정보 환각.
        # _TYPO_ALIASES 로 "vivian"↔"vvian" 매칭 → 정답 chunk #194 1순위 안착.
        "name": "H40b_who_is_vivian_english",
        "query": "Vivian이라는 직원이 무슨 업무 담당이야?",
        "expected_content_substring": "이채진",
        "must_include": ["이채진"],
        "must_not_include": ["IT 부서", "소프트웨어 개발", "CRM"],  # 일반 지식 환각 검출
        "category": "H_people",
    },
    {
        "name": "H41_who_is_daniel",
        "query": "Daniel 상무는 무슨 업무를 담당해?",  # 직책 명시로 chunk 매칭 강화
        "expected_content_substring": "최혁수",
        "must_include": ["업무 외적"],  # chunk #158: "업무 외적 관리에 대한 부분은 Daniel"
        "must_not_include": [],
        "category": "H_people",
    },
    {
        "name": "H42_external_inquiry_both",
        "query": "업무 외적 문의는 누구한테 해?",
        # 대구 Daniel / 구미 Jacob — 자동 multi-site 감지로 양쪽 답
        "expected_content_substring": "업무 외적 관리",
        "must_include": ["Daniel", "Jacob"],
        "must_not_include": [],
        "category": "H_people",
    },
    {
        "name": "H43_purchase_inquiry_both",
        "query": "구매품 문의는 누구한테 해?",
        # 대구 Ivy / 구미 이채진(Vvian 오타) — 자동 multi-site 감지
        "expected_content_substring": "구매품",
        "must_include": ["Ivy", "이채진"],
        "must_not_include": [],
        "category": "H_people",
    },
    {
        "name": "H44_who_is_jacob",
        "query": "Jacob 차장은 구미에서 무슨 업무 담당이야?",  # 직책+사이트 명시
        "expected_content_substring": "배성철",
        "must_include": ["업무 외적"],  # chunk #195: "업무 외적 관리 ... Jacob에게 문의"
        "must_not_include": [],
        "category": "H_people",
    },

    # ────────────────────────────────────────────────────────────────────────
    # I. 시설·환경 (45~47)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "I45_leak_check",
        "query": "누수 점검은 누가 언제 해?",
        "expected_content_substring": "누수 담당자",
        "must_include": ["담당자", "수요일"],
        "must_not_include": [],
        "category": "I_facility",
    },
    {
        "name": "I46_first_last_attendance",
        "query": "공장에서 첫 출근/마지막 퇴근 인원 역할이 뭐야?",
        "expected_content_substring": "첫 출근인원",
        "must_include": ["출입상태", "경비상태"],
        "must_not_include": [],
        "category": "I_facility",
    },
    {
        "name": "I47_supplies_aftercare",
        "query": "비품 사용 후 어떻게 해야 해?",
        "expected_content_substring": "비품 관리",
        "must_include": ["정위치"],
        "must_not_include": [],
        "category": "I_facility",
    },

    # ────────────────────────────────────────────────────────────────────────
    # J. 소프트웨어·IT (48~49)
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "J48_software_caution",
        "query": "MS오피스 외 다른 소프트웨어 쓸 때 주의사항이 뭐야?",
        "expected_content_substring": "기타 소프트웨어",
        "must_include": ["핫스팟"],
        "must_not_include": [],
        "category": "J_software",
    },
    {
        "name": "J49_wifi_external_software",
        "query": "회사 와이파이로 MS외 기타 소프트웨어 써도 돼?",  # chunk 어휘 "기타 소프트웨어"
        "expected_content_substring": "와이파이",
        "must_include": ["핫스팟"],  # chunk 본문 "핫스팟 (와이파이 사용 금지)"
        "must_not_include": [],
        "category": "J_software",
    },

    # ────────────────────────────────────────────────────────────────────────
    # K. 종합·비교 (50~54) — multi-chunk 결합 필요
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "K50_cleaning_day_compare",
        "query": "대구본사랑 구미공장이 청소 요일이 어떻게 달라?",
        "expected_content_substring": "대청소",  # 양쪽 다 잡히면 OK
        "must_include": ["금요일", "수요일"],
        "must_not_include": [],
        "category": "K_compare",
    },
    {
        "name": "K51_submit_cycle_compare",
        # multi-chunk 결합 — 익스펜스 chunk(매달 5일) + 법인카드 chunk(2주마다) 둘 다 prompt
        # 에 들어가야 모델이 양쪽 답. 현재 _PRIMARY_MAX=1 이라 1순위만 들어가서 어려움.
        # 검색은 2주마다 chunk 가 1순위 (cosine + lexical 매칭). 모델이 그것만 보면 5일 답 못함.
        # query 를 단일 chunk 로 답 가능하게 분리.
        "query": "법인카드 자료는 얼마 주기로 제출해?",  # 단일 chunk
        "expected_content_substring": "2주마다",
        "must_include": ["2주"],
        "must_not_include": [],
        "category": "K_compare",
    },
    {
        "name": "K52_lunch_time_compare",
        "query": "점심시간이 대구랑 구미가 같아?",
        "expected_content_substring": "점심시간",
        "must_include": ["11", "12", "13"],
        "must_not_include": [],
        "category": "K_compare",
    },
    {
        "name": "K53_trip_meal_all_budgets",
        "query": "출장 가서 식사 한 끼당 얼마까지 가능한지 다 알려줘",
        "expected_content_substring": "식대비",
        "must_include": ["10,000", "12,000", "30,000"],
        "must_not_include": [],
        "category": "K_compare",
    },
    {
        "name": "K54_forbidden_actions",
        "query": "회사 정보 외부 유출하면 어떤 처벌 받아?",
        "expected_content_substring": "외부 유출",
        "must_include": ["해고", "징계"],  # chunk: "해고/징계 사유" — 모델이 그것만 답해도 OK
        "must_not_include": [],
        "category": "K_compare",
    },

    # ────────────────────────────────────────────────────────────────────────
    # Negative — 사칙에 없는 질문엔 "명시된 내용 없음" 답해야 함
    # ────────────────────────────────────────────────────────────────────────
    {
        "name": "N1_unknown_topic_vacation_days",
        "query": "연차는 1년에 며칠 쓸 수 있어?",
        "expected_content_substring": None,
        "must_include": ["없습니다"],
        "must_not_include": [],
        "category": "N_negative",
    },

    # ────────────────────────────────────────────────────────────────────────
    # H. History-poisoning — 이전 대화의 양식·예시를 새 질문에 응용하지 말 것
    # ────────────────────────────────────────────────────────────────────────
    {
        # v12 회귀: 사용자가 "연차 메일 양식" 답을 받은 후 "출퇴근 지문 안 찍으면?" 으로
        # 이어 물으면 모델이 이전 연차 양식("..._260101_이채진")을 응용해 "지문 미발생 보고"
        # 같은 가짜 양식을 만드는 환각. prompt 규칙 4(history 응용 금지)로 차단.
        "name": "HP1_fingerprint_after_leave",
        "query": "출퇴근 지문 안 찍으면?",
        "history": [
            {"role": "user", "content": "연차 사용 보고 메일 제목 양식이 어떻게 돼?"},
            {
                "role": "assistant",
                "content": (
                    "연차 사용 보고 메일 제목 양식은 다음과 같습니다: "
                    "연차 사용 보고_260101(사용예정 or 사용일)_이채진. "
                    "필수 참조: 대표님, 최혁수 상무(Daniel), 배성철 차장(Jacob), 이채진 주임(Vivian). "
                    "당일 급하게 사용 시 익월 메일 송부 필수."
                ),
            },
        ],
        "expected_content_substring": "지문",
        "must_include": ["지각"],
        # 이전 답의 양식·인물 정보가 응용되면 안 됨
        "must_not_include": ["미발생 보고", "Daniel", "Jacob", "Vivian", "익월"],
        "category": "HP_history",
    },
]
