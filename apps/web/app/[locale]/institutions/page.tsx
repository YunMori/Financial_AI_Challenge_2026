"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { UnknownNotice } from "@/components/common/UnknownNotice";
import { TierNotice } from "@/components/chat/TierNotice";
import { InstitutionCard } from "@/components/institution/InstitutionCard";
import {
  fetchInstitutions,
  fetchRankingPolicy,
  isAbortError,
  type InstitutionsResponse,
  type RankingPolicy,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { loadContext } from "@/lib/session";

/**
 * F2 — 계좌개설 내비게이터.
 *
 * **생성이 없다.** 매트릭스 조회 결과를 그대로 그린다 — 스피너가 길게 돌지
 * 않고, 답이 매번 같다. 심사에서 재현성이 중요한 화면이다.
 */
export default function InstitutionsPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = use(params);
  const [data, setData] = useState<InstitutionsResponse | null>(null);
  const [policy, setPolicy] = useState<RankingPolicy | null>(null);
  const [showPolicy, setShowPolicy] = useState(false);
  const [error, setError] = useState(false);
  const [visa, setVisa] = useState<string | undefined>();

  useEffect(() => {
    const ctx = loadContext();
    setVisa(ctx.visa);
    const ac = new AbortController();
    // 새 조회를 시작하면 지난 실패는 지운다 — 남겨 두면 성공한 화면 위에
    // 에러 문구가 계속 붙어 있다.
    setError(false);
    fetchInstitutions(locale, ctx.visa, ac.signal)
      .then(setData)
      // 취소는 실패가 아니다(`isAbortError` 주석 참조). 걸러내지 않으면
      // StrictMode 의 두 번째 실행이 성공해도 배너가 함께 뜬다.
      .catch((e) => { if (!isAbortError(e)) setError(true); });
    fetchRankingPolicy(ac.signal).then(setPolicy).catch(() => {});
    return () => ac.abort();
  }, [locale]);

  // 코드 목록을 화면에 그대로 내면 이용자가 못 읽는다 — 카드에서 이름을 찾는다.
  const unknownNames =
    data?.unknown_institutions
      .map((code) => data.results.find((c) => c.inst_code === code)?.inst_name ?? code)
      ?? [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">{t(locale, "institution.title")}</h1>
        <p className="mt-1 text-sm text-gray-600">{t(locale, "institution.subtitle")}</p>
      </div>

      <p className="text-xs text-gray-600">
        {visa ? (
          <>
            {t(locale, "institution.forVisa")}: <strong>{visa}</strong>
          </>
        ) : (
          <>
            {t(locale, "institution.noVisa")}{" "}
            <Link href={`/${locale}/profile`} className="text-blue-700 underline underline-offset-2">
              {t(locale, "dashboard.editProfile")}
            </Link>
          </>
        )}
      </p>

      {error && <p className="text-sm text-red-700">{t(locale, "chat.error")}</p>}
      {!data && !error && <p className="text-sm text-gray-500">{t(locale, "common.loading")}</p>}

      {data && (
        <>
          {/* 계좌개설 요건이 미확인이므로 A 는 나오지 않는다 — 서버가 정한다 */}
          <TierNotice tier={data.disclaimer_tier} locale={locale} />

          <ul className="space-y-3">
            {data.results.map((card) => (
              <InstitutionCard key={card.inst_code} card={card} locale={locale} />
            ))}
          </ul>

          <UnknownNotice names={unknownNames} locale={locale} />

          {/* 기준 공개 (기획서 14.3) — 산식은 서버가 상수에서 만들어 준다 */}
          <div className="border-t border-gray-200 pt-3">
            <button
              type="button"
              onClick={() => setShowPolicy((v) => !v)}
              className="text-xs text-blue-700 underline underline-offset-2"
            >
              {t(locale, "institution.policyLink")} {showPolicy ? "▲" : "▼"}
            </button>
            {showPolicy && policy && (
              <div className="mt-2 space-y-2 rounded-md bg-gray-50 px-3 py-2.5 text-xs text-gray-700">
                <p className="font-medium">{t(locale, "institution.policyTitle")}</p>
                <code className="block break-all text-[11px] text-gray-800">{policy.formula}</code>
                {/* ★ 산문은 화면의 i18n 이 갖는다. 서버 문구(`fit.py`)는 한국어
                    고정이라 vi 화면에 한국어가 그대로 뜬다 — 체크리스트 주의문구와
                    같은 문제다. 숫자(가중치·산식)는 계속 서버에서 온다. */}
                <p>
                  <span className="font-medium">{t(locale, "institution.policyExcluded")}: </span>
                  {t(locale, "institution.policyExcludedItems")}
                </p>
                <p className="leading-relaxed">
                  <span className="font-medium">{t(locale, "institution.policyUnverified")}: </span>
                  {t(locale, "institution.policyUnverifiedText")}
                </p>
              </div>
            )}
            <p className="mt-2 text-[11px] text-gray-500">
              {t(locale, "institution.updatedAt")} {data.updated_at}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
