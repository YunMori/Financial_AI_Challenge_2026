import { isReviewed, t } from "@/lib/i18n";

/**
 * 미검수 언어 고지 (planner §10.3).
 *
 * ★★ **이 컴포넌트가 언어 6종 공개를 정당화하는 유일한 장치다.**
 *    §10.3 은 "검수되지 않은 언어는 공개하지 않는다"고 했다. 우리는 공개하는
 *    쪽을 택했고 — 대상 이용자는 한국어를 못 읽으므로 막아 두면 그들에게 이
 *    서비스는 존재하지 않는 것과 같다 — 대신 **검수되지 않았다는 사실을
 *    숨기지 않기로** 했다. 이 고지를 지우면 그 약속이 깨진다.
 *
 * ★ **그 언어와 영어를 함께 낸다.** 번역이 미검수라는 사실을 그 언어로만 적으면,
 *   정작 그 번역이 이상할 때 읽히지 않을 수 있다. 영어를 병기해 최소한 하나는
 *   전달되게 한다.
 *
 * ★ **검수된 언어에서는 아무것도 렌더하지 않는다.** 상시 배너가 늘어나면 정작
 *   중요한 고지(최종 확인 주체, 사칭 대응)의 주목도가 떨어진다.
 */
export function UnreviewedLanguageNotice({ locale }: { locale: string }) {
  if (isReviewed(locale)) return null;

  const localText = t(locale, "legal.unreviewedLanguage");
  const englishText = t("en", "legal.unreviewedLanguage");

  return (
    <div
      role="note"
      className="border-b border-amber-200 bg-amber-50 px-4 py-2"
      aria-label="translation status"
    >
      <div className="mx-auto max-w-3xl space-y-0.5">
        <p className="text-xs leading-relaxed text-amber-900" lang={locale}>
          <span aria-hidden className="mr-1">
            🈳
          </span>
          {localText}
        </p>
        {localText !== englishText && (
          <p className="text-[11px] leading-relaxed text-amber-800" lang="en">
            {englishText}
          </p>
        )}
      </div>
    </div>
  );
}
