/**
 * 출처 표시 — 이 서비스의 시각적 정체성 (planner §11.3).
 *
 * `verified_at`(운영자가 원문을 확인한 날)을 발행일과 **함께** 보여준다.
 * 발행일만 보여주는 서비스는 많지만, 확인일을 보여주면 정보의 최신성을
 * 운영자가 책임진다는 뜻이 된다.
 */
import { t } from "@/lib/i18n";
import type { EvidenceRef } from "@/lib/sse";

export function CitationBadge({ item, locale }: { item: EvidenceRef; locale: string }) {
  return (
    <li className="rounded-md border border-gray-200 bg-white px-3 py-2">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0 rounded bg-blue-100 px-1.5 py-0.5 text-[11px] font-semibold text-blue-800">
          {item.ref}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-gray-900">
            {item.publisher} 「{item.title}」
          </p>
          <p className="mt-0.5 text-[11px] text-gray-500">
            {item.published_at && <>{t(locale, "chat.published")} {item.published_at}</>}
            {item.verified_at && <> · {t(locale, "chat.verified")} {item.verified_at}</>}
          </p>
          {item.stale && (
            <p className="mt-1 text-[11px] text-amber-700">
              <span aria-hidden className="mr-1">⏳</span>
              {t(locale, "chat.staleWarning")}
            </p>
          )}
          {item.url && (
            <a href={item.url} target="_blank" rel="noopener noreferrer"
               className="mt-1 inline-block text-[11px] text-blue-700 underline underline-offset-2">
              {t(locale, "chat.viewOriginal")} ↗
            </a>
          )}
        </div>
      </div>
    </li>
  );
}
