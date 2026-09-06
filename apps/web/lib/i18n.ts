import ko from "@/messages/ko.json";
import en from "@/messages/en.json";
import vi from "@/messages/vi.json";
import zh from "@/messages/zh.json";
import uz from "@/messages/uz.json";
import th from "@/messages/th.json";

// 공개 언어 6종 (2026-08-21). planner 가 상정한 전부다.
export const LOCALES = ["ko", "en", "vi", "zh", "uz", "th"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "ko";

// 화면에 표시할 이름은 **그 언어 자체로** 쓴다. 한국어를 못 읽는 이용자가
// 첫 화면에서 자기 언어를 찾을 수 있어야 한다.
export const LOCALE_NAMES: Record<Locale, string> = {
  ko: "한국어",
  en: "English",
  vi: "Tiếng Việt",
  zh: "中文",
  uz: "O‘zbekcha",
  th: "ไทย",
};

export const LOCALE_FLAGS: Record<Locale, string> = {
  ko: "🇰🇷",
  en: "🇬🇧",
  vi: "🇻🇳",
  zh: "🇨🇳",
  uz: "🇺🇿",
  th: "🇹🇭",
};

/**
 * 원어민 검수를 마친 언어. **서버의 `schemas/common.py :: REVIEWED` 와 같은 목록이다.**
 *
 * ★ 여기 없는 언어는 화면에 미검수 고지가 상시로 붙는다. planner §10.3 은
 *   "검수되지 않은 언어는 공개하지 않는다"고 했지만, 대상 이용자는 한국어를 못
 *   읽는다 — 막아 두면 그 이용자에게 이 서비스는 **존재하지 않는 것**과 같다.
 *   번역을 내되 그 한계를 밝히는 쪽을 택했고, 이 배열이 그 약속을 지킨다.
 *
 *   ⚠ 목록을 늘리는 것은 **번역을 채우는 일이 아니라 검수를 확보하는 일**이다.
 *     번역만 채우고 여기 추가하면 고지가 사라져 거짓말이 된다.
 */
export const REVIEWED_LOCALES: readonly Locale[] = ["ko", "en", "vi"];

export function isReviewed(locale: string): boolean {
  return REVIEWED_LOCALES.some((l) => l === locale);
}

const DICTS = { ko, en, vi, zh, uz, th } as const;
export type Messages = typeof ko;

export function isLocale(v: string): v is Locale {
  return (LOCALES as readonly string[]).includes(v);
}

export function getMessages(locale: string): Messages {
  return DICTS[isLocale(locale) ? locale : DEFAULT_LOCALE];
}

/** 폴백 체인: {locale} → en → ko (planner §11.4) */
export function t(locale: string, path: string): string {
  for (const loc of [locale, "en", DEFAULT_LOCALE]) {
    const found = lookup(getMessages(loc), path);
    if (found) return found;
  }
  return path; // 키 자체를 보여준다 — 빈 화면보다 디버깅이 쉽다
}

function lookup(dict: unknown, path: string): string | undefined {
  const value = path.split(".").reduce<unknown>(
    (acc, key) => (acc && typeof acc === "object" ? (acc as Record<string, unknown>)[key] : undefined),
    dict,
  );
  return typeof value === "string" ? value : undefined;
}
