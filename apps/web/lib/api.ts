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
