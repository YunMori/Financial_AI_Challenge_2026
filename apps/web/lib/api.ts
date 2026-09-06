"use client";

/**
 * REST 클라이언트 — F2 내비게이터 · F3 체크리스트 (planner §9.2).
 *
 * SSE(`lib/sse.ts`)와 달리 이쪽은 **생성이 없는 결정적 경로**다. 매트릭스를
 * 조회해 그대로 그린다 — 로딩 스피너가 길게 돌 이유가 없고, 실제로 돌지 않는다.
 */

import type { Purpose, SessionContext, VisaCode } from "./session";

/** API 원점의 **단일 출처**. `lib/sse.ts` 도 여기서 가져다 쓴다. */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";

/**
 * 이 거절이 **우리가 스스로 낸 취소**인가.
 *
 * ★ `AbortError` 를 연결 실패로 취급하면 안 된다. abort 는 정리(cleanup)·언어
 *   전환·페이지 이탈에서 우리가 부르는 것이지 서버나 회선의 문제가 아니다.
 *
 *   실제로 그 구분이 없어서 F2 화면에 **"연결에 문제가 있습니다"가 카드와 함께**
 *   떴다 (2026-08-21). `reactStrictMode` 가 effect 를 mount→cleanup→mount 로
 *   두 번 돌리는데, cleanup 의 `ac.abort()` 가 첫 요청을 취소하고 그 거절이
 *   에러 배너를 켠 뒤, 두 번째 요청이 성공해 데이터가 함께 그려졌다.
 *   측정: 브라우저가 시도한 요청 4건 · **서버가 받은 요청 2건(둘 다 200)**.
 *
 *   ⚠ 지금 눈에 보이는 것은 개발 빌드뿐이다. StrictMode 이중 실행은 dev 전용이고,
 *     언어 전환 UI(`LangSwitch`, planner §11)는 아직 없어서 `locale` 이 바뀌는
 *     경로가 없다. **그것이 생기는 순간 배포에서 그대로 터진다** — cleanup 은
 *     `locale` 이 바뀔 때도 돌기 때문이다. 그때 고치면 원인을 다시 찾아야 한다.
 */
export function isAbortError(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

export type EvidenceStatus = "official" | "inferred" | "unknown";
export type Tier = "A" | "B" | "C";

export interface MatrixEvidence {
  doc_id: string;
  publisher: string;
  published_at: string | null;
  url: string;
  note: string;
}

export interface RequiredDoc {
  code: string;
  label: string;
  label_ko: string;
}

export interface InstitutionCard {
  inst_code: string;
  inst_name: string;
  inst_name_ko: string;
  /** 기관 단위(외국인 채널) 확인 상태 */
  status: EvidenceStatus;
  /** ★ 요청한 체류자격의 계좌개설 요건 상태 — `status` 와 **다른 것**이다 */
  account_open: EvidenceStatus;
  channels: string[];
  mobile_arc_accepted: boolean;
  mobile_arc_since: string | null;
  required_docs: RequiredDoc[];
  purpose_docs: RequiredDoc[];
  /** 아는 항목이 하나도 없으면 null — 0.0 과 구분한다 */
  fit_score: number | null;
  fit_reason: string[];
  /** 확인하지 못한 항목. 점수에서 제외됐고 화면에 그대로 표시한다 */
  unverified: string[];
  notes: string;
  evidence: MatrixEvidence[];
}

export interface InstitutionsResponse {
  visa: string | null;
  lang: string;
  results: InstitutionCard[];
  unknown_institutions: string[];
  disclaimer_tier: Tier;
  updated_at: string;
}

export interface RankingPolicy {
  formula: string;
  weights: Record<string, number>;
  visa_fit_by_status: Record<string, number>;
  channel_access_weights: Record<string, number>;
  excluded_inputs: string[];
  unverified_handling: string;
}

export interface ChecklistSection {
  key: "base" | "account_opening" | "foreign_registration";
  title: string;
  status: EvidenceStatus;
  items: RequiredDoc[];
  notes: string;
  caveat: string;
  evidence: MatrixEvidence[];
}

export interface ChecklistResponse {
  lang: string;
  visa: string | null;
  purpose: string | null;
  institution: string | null;
  sections: ChecklistSection[];
  disclaimer_tier: Tier;
  generated_at: string;
  filename: string;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { signal, cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export function fetchInstitutions(
  lang: string,
  visa: VisaCode | undefined,
  signal?: AbortSignal,
): Promise<InstitutionsResponse> {
  const q = new URLSearchParams({ lang });
  if (visa) q.set("visa", visa);
  return getJson<InstitutionsResponse>(`/institutions?${q}`, signal);
}

export function fetchRankingPolicy(signal?: AbortSignal): Promise<RankingPolicy> {
  return getJson<RankingPolicy>("/institutions/ranking-policy", signal);
}

// ── F8 사기 유형 대조 ────────────────────────────────────────────────
//
// ★ **판정 파라미터가 없다.** "이 전화가 사기인가?"를 물을 수 있는 인자를 두면
//   그 순간 계층 C 설계가 무너진다. 이 호출은 대조표를 가져올 뿐이다.

export interface ScamType {
  code: string;
  /** 원문의 단정 강도이지 우리 판단이 아니다. */
  certainty: "definite" | "warning";
  title: string;
  body: string;
  evidence: MatrixEvidence[];
}

export interface ResponseStep {
  seq: number;
  label: string;
}

export interface ScamContact {
  code: string;
  number: string;
  org: string;
  role: string;
  primary: boolean;
  evidence: MatrixEvidence[];
}

export interface ScamResponse {
  lang: string;
  types: ScamType[];
  response_steps: ResponseStep[];
  response_evidence: MatrixEvidence[];
  contacts: ScamContact[];
  updated_at: string;
  disclaimer_tier: Tier;
}

export function fetchScam(lang: string, signal?: AbortSignal): Promise<ScamResponse> {
  return getJson<ScamResponse>(`/scam?lang=${encodeURIComponent(lang)}`, signal);
}

// ── F7 금융상품 비교 ─────────────────────────────────────────────────
//
// ★ `available: false` 는 **빈 목록과 다르다.** 못 가져온 것이지 상품이 없는
//   것이 아니다. 화면이 이 둘을 같게 그리면 거짓말이 된다.

export type ProductKind = "deposit" | "saving" | "credit_loan";

export interface RateOption {
  save_trm: string | null;
  rate_type: string;
  rate: number | null;
  /** 대출에는 없다. `null` 은 공시 없음이지 0% 가 아니다. */
  rate_max: number | null;
}

export interface ProductCard {
  fin_co_no: string;
  fin_prdt_cd: string;
  company: string;
  product: string;
  join_way: string;
  /** 원문 코드 그대로("1"/"2"/"3"). 외국인 가입 가능 여부가 **아니다**. */
  join_deny: string;
  join_member: string;
  etc_note: string;
  max_limit: number | null;
  options: RateOption[];
  sort_value: number | null;
  dcls_month: string;
}

export interface ProductSortPolicy {
  kind: ProductKind;
  sort_input: string;
  direction: "asc" | "desc";
  excluded_inputs: string[];
  undisclosed_handling: string;
  foreigner_eligibility: string;
}

export interface ProductsResponse {
  lang: string;
  kind: ProductKind;
  save_trm: string | null;
  available: boolean;
  unavailable_reason: string | null;
  results: ProductCard[];
  sort_policy: ProductSortPolicy | null;
  dcls_month: string;
  fetched_on: string | null;
  disclaimer_tier: Tier;
}

// ── F6 해외송금 시뮬레이터 ───────────────────────────────────────────
//
// ★ `exceeds: null` 은 "한도 안"이 **아니다.** 한도 근거가 없어 판단하지 않은
//   것이며, 화면이 이것을 `false` 처럼 그리면 근거 없는 안심을 주게 된다.

export interface RemittanceRate {
  value: number;
  /** 고시 기준일. ECOS 는 일별 고시라 실시간이 아니다. */
  quoted_at: string;
  source: string;
  basis: string;
  is_stale: boolean;
}

export interface RemittanceLimit {
  status: EvidenceStatus;
  limit_usd: number | null;
  used_usd: number | null;
  remaining_usd: number | null;
  exceeds: boolean | null;
  evidence: MatrixEvidence[];
}

export interface RemittanceResponse {
  lang: string;
  amount_krw: number;
  amount_usd: number | null;
  rate: RemittanceRate | null;
  rate_unavailable_reason: string | null;
  annual: RemittanceLimit;
  per_transaction: RemittanceLimit;
  used_is_self_declared: boolean;
  notes: string;
  disclaimer_tier: Tier;
}

export function simulateRemittance(
  body: { lang: string; amount_krw: number; self_declared_ytd_usd?: number | null },
  signal?: AbortSignal,
): Promise<RemittanceResponse> {
  return postJson<RemittanceResponse>("/remittance/simulate", body, signal);
}

export function fetchProducts(
  lang: string,
  kind: ProductKind,
  saveTrm: string | null,
  signal?: AbortSignal,
): Promise<ProductsResponse> {
  const q = new URLSearchParams({ lang, kind });
  if (saveTrm) q.set("save_trm", saveTrm);
  return getJson<ProductsResponse>(`/products?${q}`, signal);
}

export interface ChecklistParams {
  lang: string;
  visa?: VisaCode;
  purpose?: Purpose;
  inst_code?: string;
  include_ko?: boolean;
}

export function checklistParamsFrom(
  lang: string,
  ctx: SessionContext,
  instCode?: string,
): ChecklistParams {
  return {
    lang,
    visa: ctx.visa,
    // 서버가 첫 번째 목적만 쓴다(F4 와 같은 규칙) — 화면도 같은 것을 보낸다.
    purpose: ctx.purposes[0],
    inst_code: instCode,
  };
}

export function fetchChecklist(
  params: ChecklistParams,
  signal?: AbortSignal,
): Promise<ChecklistResponse> {
  return postJson<ChecklistResponse>("/checklist/preview", params, signal);
}

/**
 * PDF 내려받기.
 *
 * `<a download>` 로 GET 링크를 걸 수 없다 — 프로필을 body 로 보내는 POST 이기
 * 때문이다. blob 을 만들어 임시 링크로 클릭한다.
 *
 * **503 은 정상 경로다.** 조판 의존성이 없는 배포에서 PDF 만 빠지고 화면 내
 * 체크리스트는 살아 있어야 한다(planner §12.3). 호출자가 그 상태를 구분할 수
 * 있도록 `ok:false` 로 돌려준다.
 */
export async function downloadChecklistPdf(
  params: ChecklistParams,
  filename: string,
): Promise<{ ok: boolean; status: number }> {
  const res = await fetch(`${API_BASE}/checklist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) return { ok: false, status: res.status };

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return { ok: true, status: res.status };
}
