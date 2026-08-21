"use client";

import { use, useEffect, useState } from "react";

import { TierNotice } from "@/components/chat/TierNotice";
import {
  fetchProducts,
  isAbortError,
  type ProductKind,
  type ProductsResponse,
} from "@/lib/api";
import { t } from "@/lib/i18n";

/**
 * F7 금융상품 비교.
 *
 * ★ **"외국인이 가입할 수 있는가"는 이 자료에 없다.** 금감원 공시는 상품 조건만
 * 담고 체류자격을 다루지 않는다. 조건만 보고 창구에 갔다가 거절당하면 그 안내는
 * 없느니만 못하므로, 주의문구를 목록보다 **위**에 상시로 둔다.
 *
 * ★ **`available: false` 를 빈 목록처럼 그리지 않는다.** 못 가져온 것이지 상품이
 * 없는 것이 아니다.
 *
 * **생성이 없다.** 조회 → 조인 → 정렬로 끝나는 결정적 경로다.
 */

const KINDS: { key: ProductKind; label: string }[] = [
  { key: "deposit", label: "product.kindDeposit" },
  { key: "saving", label: "product.kindSaving" },
  { key: "credit_loan", label: "product.kindCreditLoan" },
];

// 공시에 흔한 기간. 고르지 않으면 기간이 섞여 비교가 성립하지 않는다.
const TERMS = ["6", "12", "24", "36"];

export default function ProductsPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = use(params);
  const [kind, setKind] = useState<ProductKind>("deposit");
  const [term, setTerm] = useState<string | null>("12");
  const [data, setData] = useState<ProductsResponse | null>(null);
  const [error, setError] = useState(false);
  const [showPolicy, setShowPolicy] = useState(false);

  const isLoan = kind === "credit_loan";

  useEffect(() => {
    const ac = new AbortController();
    setError(false);
    setData(null);
    fetchProducts(locale, kind, isLoan ? null : term, ac.signal)
      .then(setData)
      .catch((e) => { if (!isAbortError(e)) setError(true); });
    return () => ac.abort();
  }, [locale, kind, term, isLoan]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">{t(locale, "product.title")}</h1>
        <p className="mt-1 text-sm text-gray-600">{t(locale, "product.subtitle")}</p>
      </div>

      {/* ★ 주의문구가 목록보다 위에 온다. 조건 비교와 가입 가능은 다른 것이다. */}
      <div className="rounded-md border border-tierB-border bg-tierB-bg px-3 py-2.5">
        <p className="text-xs leading-relaxed text-tierB-text">{t(locale, "product.caveat")}</p>
      </div>

      <div className="flex flex-wrap gap-2">
        {KINDS.map((k) => (
          <button
            key={k.key}
            type="button"
            onClick={() => setKind(k.key)}
            className={
              "rounded-full border px-3 py-1.5 text-sm " +
              (kind === k.key
                ? "border-blue-600 bg-blue-50 text-blue-800"
                : "border-gray-300 text-gray-700")
            }
          >
            {t(locale, k.label)}
          </button>
        ))}
      </div>

      {!isLoan && (
        <div>
          <p className="text-xs font-medium text-gray-700">{t(locale, "product.term")}</p>
          <div className="mt-1.5 flex flex-wrap gap-2">
            {TERMS.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setTerm(m)}
                className={
                  "rounded-full border px-3 py-1 text-xs " +
                  (term === m
                    ? "border-blue-600 bg-blue-50 text-blue-800"
                    : "border-gray-300 text-gray-700")
                }
              >
                {t(locale, "product.termMonths").replace("{n}", m)}
              </button>
            ))}
          </div>
          <p className="mt-1 text-[11px] text-gray-500">{t(locale, "product.termHint")}</p>
        </div>
      )}

      {error && <p className="text-sm text-red-700">{t(locale, "chat.error")}</p>}
      {!data && !error && <p className="text-sm text-gray-500">{t(locale, "common.loading")}</p>}

      {/* ★ 못 가져온 것과 없는 것을 다르게 그린다. */}
      {data && !data.available && (
        <p className="rounded-md bg-gray-50 px-3 py-2.5 text-sm text-gray-700">
          {t(locale, "product.unavailable")}
        </p>
      )}

      {data && data.available && (
        <>
          <TierNotice tier={data.disclaimer_tier} locale={locale} />

          <ul className="space-y-3">
            {data.results.map((p) => (
              <li
                key={`${p.fin_co_no}-${p.fin_prdt_cd}`}
                className="rounded-lg border border-gray-200 bg-white p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-base font-semibold">{p.product}</h3>
                    <p className="text-xs text-gray-600">{p.company}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    {p.sort_value === null ? (
                      <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600">
                        {t(locale, "product.undisclosed")}
                      </span>
                    ) : (
                      <>
                        <p className="text-base font-semibold text-blue-700">
                          {p.sort_value.toFixed(2)}%
                        </p>
                        <p className="text-[11px] text-gray-500">
                          {t(locale, isLoan ? "product.rateAvg" : "product.rateMax")}
                        </p>
                      </>
                    )}
                  </div>
                </div>

                {p.sort_value === null && (
                  <p className="mt-1.5 text-[11px] text-gray-500">
                    {t(locale, "product.undisclosedNote")}
                  </p>
                )}

                <ul className="mt-2 flex flex-wrap gap-1.5">
                  {p.options.map((o, i) => (
                    <li
                      key={i}
                      className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
                    >
                      {o.save_trm
                        ? t(locale, "product.termMonths").replace("{n}", o.save_trm) + " · "
                        : ""}
                      {o.rate === null ? t(locale, "product.undisclosed") : `${o.rate}%`}
                      {o.rate_max !== null && ` → ${o.rate_max}%`}
                    </li>
                  ))}
                </ul>

                <dl className="mt-2 space-y-0.5 text-xs text-gray-600">
                  {p.join_way && (
                    <div>
                      <dt className="inline font-medium">{t(locale, "product.joinWay")}: </dt>
                      <dd className="inline">{p.join_way}</dd>
                    </div>
                  )}
                  {p.join_member && (
                    <div>
                      <dt className="inline font-medium">{t(locale, "product.joinMember")}: </dt>
                      <dd className="inline">{p.join_member}</dd>
                    </div>
                  )}
                  {/* 원문 코드를 라벨로 바꿔 보여주되, 외국인 가입 여부로 해석하지 않는다. */}
                  {["1", "2", "3"].includes(p.join_deny) && (
                    <div>
                      <dt className="inline font-medium">{t(locale, "product.joinDeny")}: </dt>
                      <dd className="inline">{t(locale, `product.joinDeny${p.join_deny}`)}</dd>
                    </div>
                  )}
                  {p.max_limit !== null && (
                    <div>
                      <dt className="inline font-medium">{t(locale, "product.maxLimit")}: </dt>
                      <dd className="inline">{p.max_limit.toLocaleString()}</dd>
                    </div>
                  )}
                </dl>
              </li>
            ))}
          </ul>

          {/* 기준 공개 (기획서 14.3) — F2 의 ranking-policy 와 같은 장치 */}
          <div className="border-t border-gray-200 pt-3">
            <button
              type="button"
              onClick={() => setShowPolicy((v) => !v)}
              className="text-xs text-blue-700 underline underline-offset-2"
            >
              {t(locale, "product.policyLink")} {showPolicy ? "▲" : "▼"}
            </button>
            {showPolicy && data.sort_policy && (
              <div className="mt-2 space-y-2 rounded-md bg-gray-50 px-3 py-2.5 text-xs text-gray-700">
                <p className="font-medium">{t(locale, "product.policyTitle")}</p>
                <p>
                  <span className="font-medium">{t(locale, "product.policySortInput")}: </span>
                  <code className="text-[11px]">{data.sort_policy.sort_input}</code> (
                  {data.sort_policy.direction})
                </p>
                <p>
                  <span className="font-medium">{t(locale, "product.policyExcluded")}: </span>
                  {data.sort_policy.excluded_inputs.join(" · ")}
                </p>
                <p className="leading-relaxed">
                  <span className="font-medium">{t(locale, "product.policyUndisclosed")}: </span>
                  {t(locale, "product.undisclosedNote")}
                </p>
              </div>
            )}
            <p className="mt-2 text-[11px] text-gray-500">
              {data.dcls_month && `${t(locale, "product.dclsMonth")} ${data.dcls_month} · `}
              {data.fetched_on && `${t(locale, "product.fetchedOn")} ${data.fetched_on}`}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
