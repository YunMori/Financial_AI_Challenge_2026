/**
 * 기관 카드 (F2, planner §11.3).
 *
 * ★ **`status` 와 `account_open` 을 하나로 합치지 않는다.** 모바일
 * 외국인등록증 수용 여부는 official 로 확인됐지만 그 은행의 체류자격별
 * 계좌개설 요건은 별개이고 대개 unknown 이다. 합치면 "공식 확인됨" 배지가
 * 실제보다 넓게 읽힌다.
 *
 * ★ **확인하지 못한 항목을 숨기지 않는다.** `unverified` 를 카드 안에 그대로
 * 적는다 — 모르는 칸을 모른다고 표시하는 것이 이 서비스의 어필 포인트다.
 */
import { t } from "@/lib/i18n";
import type { EvidenceStatus, InstitutionCard as Card } from "@/lib/api";

const STATUS_STYLE: Record<EvidenceStatus, { box: string; icon: string }> = {
  // 색상만으로 구분하지 않는다 — 아이콘을 함께 쓴다 (planner §11.5)
  official: { box: "bg-tierA-bg border-tierA-border text-tierA-text", icon: "📘" },
  inferred: { box: "bg-tierB-bg border-tierB-border text-tierB-text", icon: "⚠️" },
  unknown: { box: "bg-tierC-bg border-tierC-border text-tierC-text", icon: "❔" },
};

export function InstitutionCard({ card, locale }: { card: Card; locale: string }) {
  const s = STATUS_STYLE[card.status];

  return (
    <li className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold">{card.inst_name}</h3>
          {card.inst_name !== card.inst_name_ko && (
            <p className="text-xs text-gray-500">{card.inst_name_ko}</p>
          )}
        </div>
        {card.fit_score !== null && (
          <span className="shrink-0 rounded bg-gray-100 px-2 py-1 text-xs text-gray-700">
            {t(locale, "institution.fitScore")} {Math.round(card.fit_score * 100)}
          </span>
        )}
      </div>

      {/* 확인된 사실 */}
      <div className={`mt-3 rounded-md border px-3 py-2 text-xs ${s.box}`}>
        <p className="flex items-center gap-1.5 font-medium">
          <span aria-hidden>{s.icon}</span>
          {t(locale, "institution.mobileArc")}
          {card.mobile_arc_since && <> · {t(locale, "institution.since")} {card.mobile_arc_since}</>}
        </p>
      </div>

      <ul className="mt-2 flex flex-wrap gap-1.5">
        {card.channels.map((ch) => (
          <li key={ch} className="rounded-full border border-gray-300 px-2.5 py-0.5 text-xs text-gray-700">
            {ch === "online"
              ? t(locale, "institution.channelOnline")
              : t(locale, "institution.channelBranch")}
          </li>
        ))}
      </ul>

      {card.fit_reason.length > 0 && (
        <p className="mt-2 text-xs text-gray-600">
          <span className="font-medium">{t(locale, "institution.fitReason")}: </span>
          {card.fit_reason.map((r) => t(locale, `institution.reason_${r}`)).join(" · ")}
        </p>
      )}

      {/* ★ 확인하지 못한 것을 카드 안에서 밝힌다 */}
      {card.unverified.length > 0 && (
        <p className="mt-1.5 text-xs text-gray-500">
          <span aria-hidden className="mr-1">❔</span>
          <span className="font-medium">{t(locale, "institution.unverified")}: </span>
          {card.unverified.map((u) => t(locale, `institution.unverified_${u}`)).join(" · ")}
        </p>
      )}

      {card.required_docs.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {card.required_docs.map((d) => (
            <li key={d.code} className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
              {d.label}
            </li>
          ))}
        </ul>
      )}

      {/* 근거 링크. official 인데 링크가 없는 카드는 서버가 만들지 않는다. */}
      {card.evidence.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-gray-100 pt-2">
          {card.evidence.map((e) => (
            <li key={e.doc_id} className="text-[11px] text-gray-500">
              {e.publisher}
              {e.published_at && <> · {e.published_at}</>}{" "}
              <a href={e.url} target="_blank" rel="noopener noreferrer"
                 className="text-blue-700 underline underline-offset-2">
                {t(locale, "chat.viewOriginal")} ↗
              </a>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
