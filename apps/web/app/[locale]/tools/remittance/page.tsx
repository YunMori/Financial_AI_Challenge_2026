"use client";

import { use, useState } from "react";

import { TierNotice } from "@/components/chat/TierNotice";
import {
  isAbortError,
  simulateRemittance,
  type RemittanceLimit,
  type RemittanceResponse,
} from "@/lib/api";
import { t } from "@/lib/i18n";

/**
 * S5-2 — F6 해외송금 시뮬레이터.
 *
 * ★ **`exceeds === null` 을 "한도 안"으로 그리지 않는다.** 근거가 없어 판단하지
 * 않은 것이며, 안심을 주는 초록 배지를 붙이면 그것이 근거 없는 주장이 된다.
 * 지금 한도는 전부 unknown 이라(fact-check B4·B5·B6 미확인) "확인 필요"가 나간다.
 *
 * ★ **환율에는 항상 기준일이 붙는다.** ECOS 는 일별 고시라 실시간이 아니다
 * (`spec-changes.md` #A). 날짜 없는 환율은 이 화면에서 낼 수 없다.
 *
 * **생성이 없다.** 순수 함수 계산이다 (planner §10-F6 3번).
 */
function LimitRow({
  label,
  limit,
  locale,
}: {
  label: string;
  limit: RemittanceLimit;
  locale: string;
}) {
  const unknown = limit.status === "unknown" || limit.limit_usd === null;
  return (
    <div className="rounded-md border border-gray-200 bg-white px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium">{label}</p>
        {unknown ? (
          <span className="shrink-0 rounded bg-tierC-bg px-1.5 py-0.5 text-[11px] text-tierC-text">
            ❔ {t(locale, "remittance.limitUnknown")}
          </span>
        ) : (
          <p className="shrink-0 text-sm font-semibold">
            ${limit.limit_usd?.toLocaleString()}
          </p>
        )}
      </div>

      {unknown ? (
        <p className="mt-1 text-[11px] leading-relaxed text-gray-600">
          {t(locale, "remittance.limitUnknownNote")}
        </p>
      ) : (
        <div className="mt-1 space-y-0.5 text-xs text-gray-700">
          {limit.remaining_usd !== null && (
            <p>
              {t(locale, "remittance.remaining")}: ${limit.remaining_usd.toLocaleString()}
            </p>
          )}
          {/* exceeds 가 null 이면 아무 배지도 붙이지 않는다 */}
          {limit.exceeds === true && (
            <p className="font-medium text-red-700">{t(locale, "remittance.exceeds")}</p>
          )}
          {limit.exceeds === false && (
            <p className="text-green-700">{t(locale, "remittance.within")}</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function RemittancePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = use(params);
  const [amount, setAmount] = useState("3000000");
  const [ytd, setYtd] = useState("");
  const [data, setData] = useState<RemittanceResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const krw = Number(amount);
    if (!Number.isFinite(krw) || krw <= 0 || busy) return;
    setBusy(true);
    setError(false);
    try {
      const res = await simulateRemittance({
        lang: locale,
        amount_krw: krw,
        self_declared_ytd_usd: ytd ? Number(ytd) : null,
      });
      setData(res);
    } catch (err) {
      if (!isAbortError(err)) setError(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">{t(locale, "remittance.title")}</h1>
        <p className="mt-1 text-sm text-gray-600">{t(locale, "remittance.subtitle")}</p>
      </div>

      <form onSubmit={submit} className="space-y-3">
        <div>
          <label htmlFor="amount" className="text-xs font-medium text-gray-700">
            {t(locale, "remittance.amount")}
          </label>
          <input
            id="amount"
            inputMode="numeric"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label htmlFor="ytd" className="text-xs font-medium text-gray-700">
            {t(locale, "remittance.ytd")}
            <span className="ml-1.5 rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600">
              {t(locale, "remittance.selfDeclaredBadge")}
            </span>
          </label>
          <input
            id="ytd"
            inputMode="numeric"
            value={ytd}
            onChange={(e) => setYtd(e.target.value.replace(/[^\d]/g, ""))}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
          {/* ★ ORIS 를 조회하지 않는다는 사실을 입력 바로 아래에 둔다 */}
          <p className="mt-1 text-[11px] text-gray-500">{t(locale, "remittance.ytdHint")}</p>
        </div>
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md bg-blue-600 py-2.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? t(locale, "common.loading") : t(locale, "remittance.submit")}
        </button>
      </form>

      {error && <p className="text-sm text-red-700">{t(locale, "chat.error")}</p>}

      {data && (
        <>
          <TierNotice tier={data.disclaimer_tier} locale={locale} />

          {data.rate ? (
            <div className="rounded-md border border-gray-200 bg-white px-3 py-2.5">
              <p className="text-xs text-gray-600">{t(locale, "remittance.converted")}</p>
              <p className="text-xl font-semibold">
                ${data.amount_usd?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </p>
              <p className="mt-1 text-xs text-gray-600">
                {t(locale, "remittance.rateLabel")}: {data.rate.value.toLocaleString()} KRW/USD
                {" · "}
                {t(locale, "remittance.quotedAt")} {data.rate.quoted_at}
              </p>
              <p className="text-[11px] text-gray-500">
                {data.rate.source} · {data.rate.basis}
              </p>
              {/* ★ 옛 값을 쓰고 있으면 숨기지 않는다 */}
              {data.rate.is_stale && (
                <p className="mt-1.5 rounded bg-tierB-bg px-2 py-1 text-[11px] text-tierB-text">
                  ⚠️ {t(locale, "remittance.rateStale")}
                </p>
              )}
            </div>
          ) : (
            <p className="rounded-md bg-gray-50 px-3 py-2.5 text-sm text-gray-700">
              {t(locale, "remittance.rateUnavailable")}
            </p>
          )}

          <section>
            <h2 className="text-sm font-semibold">{t(locale, "remittance.limitsTitle")}</h2>
            <div className="mt-2 space-y-2">
              <LimitRow
                label={t(locale, "remittance.annual")}
                limit={data.annual}
                locale={locale}
              />
              <LimitRow
                label={t(locale, "remittance.perTransaction")}
                limit={data.per_transaction}
                locale={locale}
              />
            </div>
            {data.notes && (
              <p className="mt-2 text-xs leading-relaxed text-gray-600">{data.notes}</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
