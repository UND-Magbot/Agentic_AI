/**
 * 제안서 작업 프로젝트 — 가이드 기준 대화형 절차(docs/design/proposal_wizard_design.md).
 *
 * 서버 컴포넌트는 여기 함수로 페이지 데이터를 가져오고, 클라이언트 컴포넌트는
 * `/api/proposal-projects/*` 프록시로 같은 백엔드와 통신한다.
 */
import { cookies } from "next/headers";

const TOKEN_COOKIE = "und_cortex_token";
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";

export type ItemStatus = "confirmed" | "adopted" | "assumed" | "unknown" | "conflict" | "empty";
export type AssetRole = "content" | "appearance" | "dimension" | "price";

export type ProposalCatalog = {
  sections: { key: string; title: string; prompt: string; codes: string[] }[];
  questions: Record<string, { group: string; question: string; required: boolean }>;
  statuses: Record<ItemStatus, string>;
  asset_roles: Record<AssetRole, string>;
  /** 첫 화면 필수 질문(묶음별). */
  essential: EssentialGroup[];
  /** 첫 화면 맨 아래 선택 질문(비워도 됨 — 그리퍼를 비우면 AI 가 회사 제품을 추천). */
  optional: EssentialGroup[];
};

export type EssentialGroup = {
  title: string;
  /** 선택 묶음 안내(비워 두면 어떻게 되는지). 필수 묶음은 빈 문자열. */
  note: string;
  questions: { code: string; question: string; example: string; required: boolean }[];
};

export type ProjectItem = {
  value: string;
  unit: string;
  status: ItemStatus;
  nature: string;
  source: string;
  evidence: string;
  checked_at: string | null;
  pages: number[];
  version: number;
};

export type ProjectAsset = {
  id: number;
  attachment_id: number;
  filename: string;
  mime: string;
  role: AssetRole;
  note: string;
  external_ok: boolean;
  readable: boolean | null;
  alt_id: string | null;
};

export type ProposalProject = {
  id: number;
  title: string;
  stage: string;
  request_text: string;
  intake: Record<string, string>;
  items: Record<string, ProjectItem>;
  assets: ProjectAsset[];
  messages: { role: string; content: string; at: string }[];
  approvals: { target: string; version: number; approved_at: string }[];
  created_at: string;
  updated_at: string;
  /** 자료 읽기가 백그라운드에서 도는 중. */
  reading: boolean;
  /** 'questioning' 단계의 다음 질문 묶음(최대 8개). */
  questions: ProjectQuestion[];
  /** 컨셉 초안에 아직 필요한 필수 문항 코드. */
  required_open: string[];
  alternatives: Alternative[];
  images: ConceptImage[];
  pages: DeckPage[];
  quote_lines: QuoteLine[];
  outputs: ProjectOutput[];
  job: ProjectJob;
  /** 백그라운드 작업이 이 서버에서 실제로 도는 중인지(재시작되면 false). */
  job_alive: boolean;
  /** 공정 컨셉 계획 확인 중(이미지 그리기 전). */
  plan_pending?: boolean;
  /** 이미지 검수 초안(gemma)을 뒤에서 작성 중. */
  vision_pending: boolean;
  output: { max_pages?: number };
  experience: ExperienceHit[];
  /** 지금 사용자가 회사 지식 승인권자(영업 관리자)인지. */
  knowledge_approver: boolean;
};

export type Alternative = {
  id: string;
  name: string;
  robot: string;
  summary: string;
  reason: string;
  structure: string[];
  exclude: string[];
  item_codes: string[];
  /** '회사 지식으로 저장' 한 경우 경험 카드 id. */
  knowledge_card_id?: number;
  /** 저장한 지식의 승인 상태 — 영업 관리자가 승인해야 회상에 쓰인다. */
  knowledge_status?: KnowledgeStatus;
  /** 마지막 수정 요청으로 구성에서 바뀐 점. */
  changes?: ConceptChanges;
  /** 이미지 전 계획(로봇 DB 후보·회사 그리퍼 후보·선택). 없으면 계획 단계 이전 프로젝트. */
  plan?: ConceptPlan;
};

export type RobotCandidate = {
  maker: string; model: string; label: string;
  payload_kg: number | null; reach_mm: number | null; repeatability_mm: number | null; weight_kg: number | null;
  ip_rating: string | null; unverified: string[]; line: string;
  /** IP 조건까지 맞는 모델이 없어 IP 조건을 풀고 고른 후보. */
  ip_short?: boolean;
  /** 도달거리 추정보다 조금(10% 이내) 짧음. */
  reach_short?: boolean;
  /** 회사 우선순위(K03)상 이 후보를 넣은 이유. */
  policy_note?: string;
};

export type ConceptPlan = {
  robot_need: {
    arm: boolean; payload_kg: number | null; reach_mm: number | null; ip_min: number | null; why: string;
    mobile?: boolean; exclude_makers?: string[]; makers?: string[]; priority?: "price" | "domestic";
  };
  robot_candidates: RobotCandidate[];
  robot_pick: string;
  robot_custom?: boolean;
  gripper_ai: string;
  gripper_why: string;
  gripper_candidates: string[];
  gripper_pick: string;
  confirmed: boolean;
};

export type ConceptChanges = {
  request: string;
  note: string;
  added: string[];
  removed: string[];
  robot?: [string, string];
  name?: [string, string];
  excluded?: string[];
};

export type KnowledgeStatus = "approved" | "pending" | "rejected";

/** 승인 대기 회사 지식(영업 관리자 화면). */
export type KnowledgeReview = {
  id: number;
  title: string;
  evidence: string;
  card: {
    title: string;
    process: string;
    problem: string;
    solution: string;
    key_ideas: string[];
    effect: string;
    effect_numbers: { metric: string; value: string }[];
    cautions: string[];
    applies_when: string;
  };
  submitted_by: string;
  submitted_at: string;
  project_id: number | null;
};

/** 공정 컨셉 때 떠올린 회사 경험 — [적용]/[빼기]. */
export type ExperienceHit = {
  card_id: number;
  card_table: string;
  evidence: string;
  title: string;
  source: string;
  process: string;
  /** 그 경험이 정확히 무슨 공정이었나(반영 판단용). */
  detail?: {
    process?: string; problem?: string; solution?: string; how_it_works?: string; applies_when?: string;
    industry?: string; summary?: string; pages?: number[]; source_doc?: string;
  };
  effect_numbers: { metric: string; value: string }[];
  facts?: string[];
  suggestion: string;
  fit: string;
  caution: string;
  /** 컨셉 단계 선택 — 이번 컨셉에 반영했는지. */
  status: "suggested" | "reflected" | "dismissed";
  /** 제안서 완성 후 지식 판정 — 이때만 지식 가중치에 기록된다. */
  judged?: "apply" | "skip";
  alt_id?: string | null;
};

export type ImageLabel = { text: string; x: number; y: number };
export type ImageCheck = { key: string; label: string; ok: boolean | null; note: string };

export type ConceptImage = {
  id: number;
  attachment_id: number | null;
  alt_id: string | null;
  version: number;
  approved: boolean;
  status: "generating" | "draft" | "approved" | "failed";
  labels: ImageLabel[];
  checks: ImageCheck[];
  prompt: string;
  ref_sent: number;
  ref_used: number | null;
  error: string;
  /** 이 버전을 만든 사용자 수정 요청(첫 버전은 빈 값). */
  revision: string;
  created_at: string;
};

export type PageType = "cover" | "overview" | "flow" | "common_concept" | "alternative" | "equipment" | "poc" | "quote";

export type DeckPage = {
  page_no: number;
  type: PageType;
  title: string;
  item_codes: string[];
  asset_ids: number[];
  alt_id?: string;
};

export type QuoteLine = {
  group: "공통" | "통합·실증" | "대안" | "옵션" | "현장 적용";
  alt_id: string | null;
  item: string;
  qty: string;
  unit: string;
  unit_price: number | null;
  currency: string;
  basis: string;
  included: boolean;
};

export type ProjectOutput = {
  id: number;
  version: number;
  attachment_id: number;
  filename: string;
  report: {
    pages?: number; overflow?: string[]; masked?: string[]; softened?: string[];
    missing_facts?: string[]; warnings?: string[];
    /** 말로 고친 버전이면 — 어느 버전에서 무엇을 고쳤나. */
    edit?: { from: number; request: string; changed: string[]; note: string };
  };
  created_at: string;
};

export type ProjectJob = {
  kind?: string;
  status?: "running" | "done" | "failed";
  step?: string;
  message?: string;
  started_at?: string;
  finished_at?: string | null;
};

export type ProjectQuestion = {
  code: string;
  question: string;
  group: string;
  required: boolean;
  status: ItemStatus;
  value: string;
  evidence: string;
};

async function backendGet<T>(path: string): Promise<T | null> {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return null;
  try {
    const r = await fetch(`${BACKEND_URL}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

/** 서버 컴포넌트 전용 — 폼 12 묶음·문항·상태 이름. */
export function getProposalCatalog(): Promise<ProposalCatalog | null> {
  return backendGet<ProposalCatalog>("/v1/proposal-projects/catalog");
}

/** 서버 컴포넌트 전용 — 승인 대기 회사 지식. 승인권자가 아니면 null(403). */
export function getKnowledgeReviews(): Promise<KnowledgeReview[] | null> {
  return backendGet<KnowledgeReview[]>("/v1/proposal-projects/knowledge-reviews");
}

export type ProductImage = {
  id: number;
  width: number;
  height: number;
  source: "deck" | "upload";
  source_ref: string;
  review_status: "pending" | "approved";
  submitted_by: string | null;
  created_at: string;
};

export type ProductImageCard = {
  card_id: number;
  name: string;
  family: string;
  slides: number[];
  images: ProductImage[];
};

export type ProductImageLibrary = { products: ProductImageCard[]; approver: boolean };

/** 서버 컴포넌트 전용 — 제품 이미지 라이브러리(제품별 사진·승인 상태). */
export function getProductImages(): Promise<ProductImageLibrary | null> {
  return backendGet<ProductImageLibrary>("/v1/proposal-projects/product-images");
}

/** 서버 컴포넌트 전용 — 프로젝트 전체. 없거나 권한 없으면 null. */
export function getProposalProject(id: string): Promise<ProposalProject | null> {
  return backendGet<ProposalProject>(`/v1/proposal-projects/${encodeURIComponent(id)}`);
}

/** 서버 컴포넌트 전용 — 제품 추천에서 학습된 정정 목록(+ 지금 사용자가 영업 관리자인지). */
export function getProductCorrections(): Promise<import("./product-recommend").CorrectionList | null> {
  return backendGet("/v1/product-recommend/corrections");
}
