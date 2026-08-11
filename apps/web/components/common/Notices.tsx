/**
 * 상시 고지 3종 (planner §11.2).
 *
 * **레이아웃에 박는다.** 나중에 붙이면 반드시 빠진다. 기획서 15장(규제 준수)이
 * UI 요건으로 번역된 것이므로 화면마다 조건부로 넣으면 안 된다.
 */
import { t } from "@/lib/i18n";

/** 화면 상단 상시 배너 — 오안내 손해 대응 */
export function FinalAuthorityBanner({ locale }: { locale: string }) {
  return (
    <div className="bg-amber-50 border-b border-amber-200 px-4 py-2">
      <p className="mx-auto max-w-3xl text-xs leading-relaxed text-amber-900">
        <span aria-hidden className="mr-1">⚖️</span>
        {t(locale, "legal.finalAuthority")}
      </p>
    </div>
  );
}

/** AI 생성 표시 — 인공지능기본법·금융 AI 가이드라인 대응 */
export function AiDisclosure({ locale, className = "" }: { locale: string; className?: string }) {
  return (
    <p className={`flex items-center gap-1.5 text-xs text-gray-500 ${className}`}>
      <span aria-hidden>🤖</span>
      {t(locale, "legal.aiDisclosure")}
    </p>
  );
}

/** 서비스 사칭 대응 — 공식 도메인 명시 */
export function AntiPhishingNotice({ locale }: { locale: string }) {
  const domain = process.env.NEXT_PUBLIC_OFFICIAL_DOMAIN;
  return (
    <section className="border-t border-gray-200 bg-gray-50 px-4 py-5" aria-label="anti-phishing">
      <div className="mx-auto max-w-3xl space-y-1">
        <p className="flex items-center gap-1.5 text-sm font-semibold text-gray-900">
          <span aria-hidden>🛡️</span>
          {t(locale, "legal.antiPhishingTitle")}
        </p>
        <p className="text-xs leading-relaxed text-gray-600">
          {t(locale, "legal.antiPhishingBody")}
        </p>
        <p className="text-xs text-gray-500">
          {t(locale, "legal.officialDomain")}:{" "}
          <span className="font-mono">
            {domain || t(locale, "legal.domainUnset")}
          </span>
        </p>
      </div>
    </section>
  );
}
