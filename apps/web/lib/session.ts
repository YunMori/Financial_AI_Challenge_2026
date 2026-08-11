"use client";

/**
 * 세션 컨텍스트 — sessionStorage 래퍼 (planner §2.1, §5.1).
 *
 * **localStorage 를 쓰지 않는다.** 공용 PC(PC방·도서관·주민센터)에서 다음
 * 이용자에게 체류자격이 남는다. 탭을 닫으면 사라지는 것이 맞다.
 *
 * 서버는 이 값을 저장하지 않는다 — 매 요청 body 로 보내고 처리 후 폐기된다.
 */

export type VisaCode =
  | "E-9" | "E-8" | "E-7" | "D-2" | "D-4" | "D-8"
  | "D-10" | "F-2" | "F-4" | "F-5" | "F-6" | "H-2";

export type StayPeriod = "under_6m" | "6m_12m" | "1y_2y" | "over_2y";
export type Purpose = "salary" | "tuition" | "living" | "remittance" | "business";

export interface SessionContext {
  nationality?: string;
  visa?: VisaCode;
  stay?: StayPeriod;
  purposes: Purpose[];
}

// 2026-06 기준 체류외국인의 68.7% 를 덮는 12종.
// 근거: corpus/stats/moj_24_foreigners_by_visa_monthly.csv
export const VISA_CODES: VisaCode[] = [
  "E-9", "E-8", "E-7", "D-2", "D-4", "D-8", "D-10", "F-2", "F-4", "F-5", "F-6", "H-2",
];
export const STAY_PERIODS: StayPeriod[] = ["under_6m", "6m_12m", "1y_2y", "over_2y"];
export const PURPOSES: Purpose[] = ["salary", "tuition", "living", "remittance", "business"];

const KEY = "kbuddy.context";
const EMPTY: SessionContext = { purposes: [] };

export function loadContext(): SessionContext {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    return raw ? { ...EMPTY, ...JSON.parse(raw) } : EMPTY;
  } catch {
    return EMPTY; // 손상된 값 때문에 화면이 죽지 않게 한다
  }
}

export function saveContext(ctx: SessionContext): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(ctx));
  } catch {
    /* 프라이빗 모드 등에서 실패할 수 있다 — 기능을 막지 않는다 */
  }
}

export function clearContext(): void {
  if (typeof window !== "undefined") window.sessionStorage.removeItem(KEY);
}
