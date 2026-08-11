import ko from "@/messages/ko.json";
import en from "@/messages/en.json";
import vi from "@/messages/vi.json";

// M1 공개 언어. planner 는 6개를 상정했으나 §10.3 이 "검수되지 않은 언어는
// 공개하지 않는다"를 요구하므로, 원어민 검수를 확보한 만큼만 늘린다.
export const LOCALES = ["ko", "en", "vi"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "ko";

// 화면에 표시할 이름은 **그 언어 자체로** 쓴다. 한국어를 못 읽는 이용자가
// 첫 화면에서 자기 언어를 찾을 수 있어야 한다.
export const LOCALE_NAMES: Record<Locale, string> = {
  ko: "한국어",
  en: "English",
  vi: "Tiếng Việt",
};

export const LOCALE_FLAGS: Record<Locale, string> = { ko: "🇰🇷", en: "🇬🇧", vi: "🇻🇳" };

const DICTS = { ko, en, vi } as const;
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
