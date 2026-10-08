// 회사 제품 추천 + AI 학습 — 화면·BFF 공용 타입(backend/app/company_knowledge/product_recommend.py).

export type RecommendItem = {
  card_id: number;
  product: string;
  family: string;
  reason: string;
  fit: string;
  caution: string;
  /** 과거 정정에서 틀렸다고 한 제품이 다시 나왔을 때. */
  warning?: string;
  /** 승인된 제품 사진 id(/api/product-images/file/{id}). */
  image_id?: number | null;
  /** 공식 사양으로 확인된 제품인가(아니면 소개서 요약만 있음). */
  official?: boolean;
  /** 정보 미흡 표시 — 공식 사양 미확보. */
  info_note?: string;
  /** 조건 항목별 근거(AI) — ok=false 는 확인 필요. */
  checks?: { condition: string; basis: string; ok: boolean }[];
  /** 제품 카드에 적힌 주요 사양(DB, AI 가 만든 값 아님). */
  specs?: { item: string; value: string }[];
  applies_when?: string;
  how_it_works?: string;
};

// ── 툴체인저(ATC) 단품 선정 — 질문지 원본(backend data/atc_selection_v0.2.json)과 규칙 판정 ──

export type AtcField = {
  path: string;
  type: "string" | "integer" | "number" | "enum" | "boolean" | "array" | "attachment" | "object";
  label_ko: string;
  unit?: string;
  options?: (string | null | { value: boolean | null; label_ko: string })[];
};

export type AtcQuestion = {
  id: string;
  question_ko: string;
  scope: "robot" | "tool" | "project";
  fields: AtcField[];
  help_text_ko: string;
};

export type AtcSpec = {
  revision: string;
  question_bank: AtcQuestion[];
  followup_question_bank: AtcQuestion[];
  facts: Record<string, unknown>;
};

export type AtcFollowup = AtcQuestion & { entity_index: number | null; entity_label: string };

type AtcModel = { card_id: number; name: string; payload_kg: number | null };
export type AtcCandidate = {
  series: string;
  series_label: string;
  series_note: string;
  primary: boolean;
  models: AtcModel[];
  consider_up_to?: AtcModel[];
  /** 상위 후보와 비교하는 이유(협동 70 초과·정격 근접) — 담당자 확정(v0.8 C11). */
  compare_reasons?: string[];
  notes: string[];
  out_of_range?: boolean;
};

/** sentence: '왜 이 제품인가'로 이어지는 근거 문장(계산 결과로 만든 문장), short: 한눈 핵심값. */
export type AtcBasis = {
  item: string; input: string; judgement: string; rule: string; ok: boolean | null; short?: string; sentence?: string;
  /** selection: 이 모델을 고른 근거 · config: 함께 들어갈 구성품 수량 근거 · other: 다른 계열(참고) */
  group?: "selection" | "config" | "other";
};

/** 툴 하나의 포고핀 산정(v1.2 PGR02~PGR08) — status: confirmed | conditional(가정 포함) | needs_data(합계 미정). */
export type AtcPogoTool = {
  tool: string; pins: number | null; modules: number | null; missing: string[]; status?: "confirmed" | "conditional" | "needs_data";
  power_pins?: number | null; signal_pins?: number | null; extra_pins?: number; design_A?: number | null; interface?: string | null;
  assumptions?: string[];
};
/** 툴 하나의 공압 산정(v1.2 PGR09·PGR20·PGR21). */
export type AtcAirTool = {
  tool: string; paths: number | null; modules: number | null; status?: "confirmed" | "conditional" | "needs_data"; profile?: string | null;
  missing?: string[]; assumptions?: string[]; scenarios?: { label: string; paths: number; modules: number }[];
};
/** v1.2 액세서리 통합 결과 — 공통 마스터 배치·툴별 PPF/PMF·통합 수량표·확인할 질문. */
export type AtcAccessory = {
  revision: string; status: "needs_data" | "conditional_draft" | "engineering_review"; status_ko: string;
  master: {
    electrical_modules: string[][] | null; ppm_per_master: number | null; pmm_per_master: number | null; unique_contacts: number | null;
    electrical_lower_bound: number | null; positions: number | null; standard_positions: number; customization_required: boolean | null;
  };
  tool_layouts: { tool: string; quantity: number | null; PPF: number | null; PMF: number | null }[];
  bom: { PPM: number | null; PPF: number | null; PMM: number | null; PMF: number | null; PPM_30cm_cable: number | null; PPF_30cm_cable: number | null };
  calculation_basis: string[]; assumptions: string[]; open_questions: string[];
  /** 공통 배치 확인(교체 툴끼리 같은 접점·유로 사용) — 엔지니어 확인 사항 */
  review_notes?: string[];
};

type AtcReason = { status: string; status_ko: string; text_ko: string; rule: string | null; ref: string | null };

export type AtcOutput = {
  status: string;
  statuses: { status: string; status_ko: string }[];
  screening_candidates: AtcCandidate[];
  final_selected_model_id: number | null;
  tool_side_mass: { tool: string; kg: number | null; lower_bound_kg: number | null; provisional: boolean; missing: string[] }[];
  required_payload_kg: number | null;
  /** 담당자가 대화로 고른 모델(C11) — applied=false 면 reason 때문에 규칙 후보를 유지. */
  preference?: { model: string; applied: boolean; reason: string | null; rule_model: string | null } | null;
  /** 후보 비교 하중 — 산업용은 툴측 총무게 × 약 2배(v0.8 C01), 협동로봇은 툴측 총무게. */
  screening_payload_kg?: number | null;
  industrial_factor?: number | null;
  tool_plates: { operating: number | null; spare: number | null; total: number | null };
  pogo_needed?: boolean;
  pogo_modules?: number | null;
  pogo_tools?: AtcPogoTool[];
  pneumatic_needed?: boolean;
  pneumatic_modules?: number | null;
  pneumatic_tools?: AtcAirTool[];
  accessory?: AtcAccessory;
  /** ATC 액세서리 참고 구성(마스터·툴플레이트·포고핀·공압 모듈) — 수량은 판정 결과로만, 판매 단위는 확인 전. */
  accessories?: {
    applicable: boolean;
    items: {
      code: string | null; name: string; card_id: number | null; side: string; qty: number | null; basis: string; image_id?: number | null;
      /** 단가표에는 있지만 견적 포함 여부를 건별로 정하는 품목(CONTROLBOX·4m·IB). */
      optional?: boolean;
      /** 선택 품목을 [견적에 포함]으로 체크했는가 — 체크한 것만 합계에 들어간다. */
      included?: boolean;
      /** 비고 — 예: '0.3m 케이블 포함'. */
      remark?: string;
      /** v1.2 조건부 수량(가정 포함·미확정 포함) — 견적 발행 전 담당자 확인. */
      conditional?: boolean;
      /** 회사 단가표 고객사가(국내). unset = 단가표 0원(가격 미정), none = 단가표에 없음. */
      unit_price?: number | null; amount?: number | null; price_status?: "set" | "unset" | "none";
    }[];
    notes: string[];
    /** 기본 줄(선택 제외) 합계 — 수량·단가가 다 있어야 complete. VAT 별도. */
    pricing?: {
      subtotal: number; complete: boolean; missing: string[]; optional_total: number; currency: string; vat: string; basis: string;
    } | null;
  };
  /** 회사 제품 DB 로 확정된 기준(P04·P06·P07·P08·P09 일부). */
  db_confirmed?: { id: string; text: string }[];
  /** 1순위 모델이 제품 DB 공식 사양(모델·정격 확정)인가. */
  catalog_approved?: boolean;
  /** 컨트롤러·전원(P09, 1순위 모델 카드의 전기 사양). */
  controller_info?: { power: string | null; control: string | null; supply: string | null } | null;
  /** 이번 판정에 쓴 잠정 기준(엔지니어 확정 전, 영업부 잠정값). */
  provisional_applied?: { id: string; text: string }[];
  engineering_review_reasons: AtcReason[];
  information_reasons: AtcReason[];
  missing_fields: string[];
  followup_questions: AtcFollowup[];
  /** 질문지에서 비어 있는 핵심 칸 + 아직 답이 없는 후속 질문(AI 확인 질문). '모름' 칸은 들어가지 않는다. */
  pending_questions: (AtcFollowup & { kind: "basic" | "followup" })[];
  /** '모름' 칸 → 받아 올 자료. */
  data_requests: string[];
  assumptions: string[];
  /** 실제 제품처럼 보이는 툴 — 외부 공개 사양 검색 제안 대상(/atc/evaluate 에만). */
  real_products?: { index: number; name: string; product: string }[];
  /** 추천 근거 — 입력값 → 규칙 → 판단(계산 결과로 만든 문장, AI 생성 아님). ok=null 은 참고(다른 계열). */
  basis?: AtcBasis[];
  /** 2·3순위 후보(규칙 — 같은 계열의 더 큰 모델 → 다른 계열, 무선은 유선 뒤). 1순위는 screening_candidates 첫 계열. */
  alternatives?: { rank: number; name: string; payload_kg: number; series: string; series_label: string; wireless: boolean; reason: string }[];
  junior_summary: {
    candidate_model: string | null;
    candidate_series: string | null;
    reason_in_plain_korean: string;
    tool_plate_quantity: number | null;
    pogo_modules: string;
    pneumatic_modules: string;
    controller_and_cables: string;
    evidence_status: string;
  };
};

/** 채운 첫 고객 미팅 질문지 → AI 요약·추출(저장 전). */
export type AtcMeeting = {
  meeting: { customer: string | null; writer: string | null; date: string | null };
  summary: string;
  /** 원문에서 확인 안 된 숫자, 추정값 등 사람이 확인할 메모. */
  notes: string[];
  filename?: string;
};

export type AtcExtract = AtcMeeting & { intake: AtcIntake };

/** 질문지 답(백엔드 input_record 의 일부) — 모르는 값은 null. */
export type AtcIntake = {
  project: Record<string, unknown>;
  robots: Record<string, unknown>[];
  tools: Record<string, unknown>[];
};

export type Recommendation = {
  id: number;
  request: string;
  summary: string;
  items: RecommendItem[];
  /** 툴체인저 질문지로 받은 추천이면 'atc' — 규칙 판정(atc)·입력(intake)이 함께 온다. */
  kind?: "atc";
  atc?: AtcOutput;
  intake?: AtcIntake;
  meeting?: AtcMeeting;
  /** 과거 정정 때문에 판단을 바꾼 기록(AI 재고). */
  reconsidered: { correction_id: number; note: string; correction: string }[];
  /** 이번 요청과 비슷해서 AI 에 보여 준 정정. */
  corrections: { id: number; text: string }[];
  dropped: string[];
};

/** AI 학습 내용 종류 — 경험(실제 공정에서 써 본 결과) / 정정(A 말고 B) / 기준(다음부터 이렇게 판단). */
export type LearningKind = "experience" | "correction" | "rule";

export type CorrectionDraft = {
  kind: LearningKind;
  wrong_name: string;
  wrong_card_id: number | null;
  right_name: string;
  right_card_id: number | null;
  reason: string;
  situation: string;
  rule: string;
  note: string;
  missing: string[];
  right_in_catalog: boolean;
};

export type Correction = {
  id: number;
  kind: LearningKind;
  situation: string;
  wrong_name: string;
  right_name: string;
  reason: string;
  rule: string;
  note: string;
  active: boolean;
  /** pending=승인 대기 · kept=반영 · off=꺼짐 · rejected=거절. */
  review_status: "pending" | "kept" | "off" | "rejected";
  hits: number;
  recommendation_id: number | null;
  created_at: string;
  submitted_by: string | null;
};

export type AskResult =
  | { kind: "recommendation"; recommendation: Recommendation }
  | { kind: "answer"; answer: string; recommendation?: Recommendation }
  /** 대화에서 학습을 요청함 — 화면이 '학습시킬까요?'를 묻는다(바로 저장하지 않음). note = 그 요청 글. */
  | { kind: "learn_offer"; answer: string; note: string };

/** 대화로 받은 답을 질문 칸에 옮긴 결과(/atc/answer). */
export type AtcAnswer = { understood: boolean; intake: AtcIntake; values?: Record<string, unknown>; unknown?: string[] };

export type CorrectionList = { items: Correction[]; approver: boolean };

/** 실제 제품 툴의 공개 사양 외부 검색 결과 — 값은 '추정'(출처 기록), 사용자가 확인해야 칸에 들어간다. */
export type SpecSearch = {
  product: string;
  fields: { path: string; label: string; value: unknown; display: string; evidence: string }[];
  sources: { title: string; url: string }[];
  signals: string;
  answer: string;
  status?: "estimated";
};

/** 확정 제안 — [최종 제안 확정]으로 저장한 고객 제안 한 건(미팅·입력·선정 결과·근거·구성품·대화). */
export type ProductProposal = {
  id: number;
  recommendation_id: number | null;
  kind: "atc" | "other";
  customer: string;
  title: string;
  model: string;
  summary: string;
  memo: string;
  created_at: string;
  updated_at: string;
  submitted_by: string | null;
  snapshot?: { recommendation: Recommendation; history: { role: string; text: string }[] };
};

/** 견적서 작성(quote_session) — 대화·직접 수정으로 채우는 견적 초안·발행본. 금액은 원화(해외는 발행 때 환율로 달러). */
export type QuoteLine = {
  id: string; kind: "product" | "option" | "freight" | "work" | "extra"; key: string; name: string;
  qty: number | null; unit_price: number | null; remark?: string; unit?: string; price_status?: string | null;
  /** 액세서리 조건부 수량(v1.2) — 담당자가 확인(confirmed)해야 발행 가능. basis = 계산 근거 */
  conditional?: boolean; confirmed?: boolean; basis?: string;
};
export type QuoteForm = {
  customer: string; to: string; cc: string; subject: string; initials: string; contact_name: string; contact_title: string;
  contact_mobile: string; contact_email: string; delivery: string; place: string; payment: string; comments: string; note: string;
  lang: "ko" | "en"; fx_rate: number | null;
  /** 고객사가 입금할 계좌(기본 = 회사 계좌, 견적마다 바꿀 수 있음) — 예전 초안엔 없을 수 있음 */
  bank?: string; account_no?: string; account_holder?: string;
};
export type QuoteWork = { needed: boolean | null; people: number | null; days: number | null; rate: number | null };
/** 납품 방식 — 1 택배(품목 그대로) · 2 화물 직납(물류비) · 3 UND 인원 설치 납품(인건비) · 4 화물 + 설치(물류비+인건비) · 5 별도 협의(품목 그대로). */
export type QuoteDelivery = { case: 1 | 2 | 3 | 4 | 5 | null; freight: number | null };
export type QuoteSession = {
  id: number; recommendation_id: number | null; status: "draft" | "issued"; quote_no: string | null; revision: number;
  /** 견적서 수기 작성(AI 추천 없이, 사용자 2026-10-08) · 발행 뒤 이어진 영업 건 */
  manual?: boolean; deal_id?: number | null;
  file_name: string | null; customer: string | null;
  state: { model: string; payload_kg: number | null; applicable: boolean; form: QuoteForm; lines: QuoteLine[];
           options: Omit<QuoteLine, "id">[]; work: QuoteWork; delivery?: QuoteDelivery };
  subtotal: number; question: { id: string; text: string; choices?: string[] } | null; problems: string[];
  chat: { role: "user" | "assistant"; text: string }[];
  history: { revision: number; issued_at: string; total: number; currency: "KRW" | "USD"; file_name: string }[];
  created_at: string; updated_at: string;
};
export type QuoteListItem = {
  id: number; recommendation_id: number | null; status: "draft" | "issued"; quote_no: string | null; revision: number;
  customer: string | null; total: number | null; model: string | null; updated_at: string; manual?: boolean;
};

/** 테스트용 정답지 샘플(제품 추천 질문 모두 채우기) — quotable: 유선 자동 툴체인저라 견적 단가가 있음. */
export type AtcTestSample = { id: string; label: string; file: string; expected_model: string | null; quotable: boolean };
