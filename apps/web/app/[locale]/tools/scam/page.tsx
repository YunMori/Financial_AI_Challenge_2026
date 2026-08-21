"use client";

import { use, useEffect, useState } from "react";

import { fetchScam, isAbortError, type ScamResponse } from "@/lib/api";
import { t } from "@/lib/i18n";

/**
 * S5-3 — F8 사기 유형 대조.
 *
 * ★ **판정 화면이 아니다.** 데모 시나리오 3 의 핵심이 "AI 가 답하지 않는 것을
 * 설계로 보여주는 것"이라, 이 화면은 맨 위에 판정 거부를 먼저 놓고 그 다음에
 * 대조표를 놓는다. 순서를 바꾸면 이용자가 목록을 판정기로 읽는다.
 *
 * **생성이 없다.** F2 와 같이 조회 결과를 그대로 그린다 — 답이 매번 같아
 * 심사 시연에서 재현성이 보장된다. 그래서 `AiDisclosure` 도 붙이지 않는다.
 */
export default function ScamPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = use(params);
  const [data, setData] = useState<ScamResponse | null>(null);
  const [error, setError] = useState(false);
  // ★ 체크는 **세기만 한다.** 개수를 판정으로 바꾸는 임계값(예: 3개 이상이면
  //   사기)을 두지 않는다 — 그 순간 이 화면이 판정기가 되고, 2개 체크한
  //   이용자는 "사기가 아니다"로 읽는다. 체크 상태는 서버로 보내지 않는다.
  const [checked, setChecked] = useState<Set<string>>(new Set());

  function toggle(code: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  useEffect(() => {
    const ac = new AbortController();
    setError(false);
    fetchScam(locale, ac.signal)
      .then(setData)
      .catch((e) => { if (!isAbortError(e)) setError(true); });
    return () => ac.abort();
  }, [locale]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">{t(locale, "scam.title")}</h1>
        <p className="mt-1 text-sm text-gray-600">{t(locale, "scam.subtitle")}</p>
      </div>

      {/* ★ 판정 거부가 목록보다 **위에** 온다. 이 화면의 설계 자체다. */}
      <div className="rounded-md border border-tierC-border bg-tierC-bg px-3 py-2.5">
        <p className="text-sm font-medium text-tierC-text">{t(locale, "tier.cLabel")}</p>
        <p className="mt-1 text-xs leading-relaxed text-tierC-text">
          {t(locale, "scam.noJudgement")}
        </p>
      </div>

      {error && <p className="text-sm text-red-700">{t(locale, "chat.error")}</p>}
      {!data && !error && <p className="text-sm text-gray-500">{t(locale, "common.loading")}</p>}

      {data && (
        <>
          <section>
            <h2 className="text-sm font-semibold">{t(locale, "scam.typesTitle")}</h2>
            <ul className="mt-2 space-y-3">
              {data.types.map((s) => (
                <li key={s.code} className="rounded-lg border border-gray-200 bg-white p-4">
                  <div className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      id={`scam-${s.code}`}
                      checked={checked.has(s.code)}
                      onChange={() => toggle(s.code)}
                      className="mt-1 h-4 w-4 shrink-0"
                    />
                    <span aria-hidden className="mt-0.5">
                      {s.certainty === "definite" ? "🚫" : "⚠️"}
                    </span>
                    <div className="min-w-0">
                      <label htmlFor={`scam-${s.code}`} className="cursor-pointer">
                      <h3 className="text-sm font-semibold">{s.title}</h3>
                      {/* 색상만으로 구분하지 않는다 — 아이콘과 라벨을 함께 쓴다 (planner §11.5) */}
                      <p
                        className={
                          "mt-0.5 inline-block rounded px-1.5 py-0.5 text-[11px] " +
                          (s.certainty === "definite"
                            ? "bg-tierC-bg text-tierC-text"
                            : "bg-tierB-bg text-tierB-text")
                        }
                      >
                        {t(locale, `scam.${s.certainty}`)}
                      </p>
                      </label>
                      <p className="mt-1.5 text-xs leading-relaxed text-gray-700">{s.body}</p>
                      <ul className="mt-2 space-y-0.5">
                        {s.evidence.map((e) => (
                          <li key={e.doc_id} className="text-[11px] text-gray-500">
                            {e.publisher}
                            {e.published_at ? ` · ${e.published_at}` : ""}{" "}
                            <a
                              href={e.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-blue-700 underline underline-offset-2"
                            >
                              {t(locale, "scam.source")} ↗
                            </a>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </section>

          {/* ★ 개수만 말한다. "따라서 사기입니다"로 잇지 않는다 —
              그 한 문장이 이 화면을 판정기로 만든다(planner §10-F8). */}
          {checked.size > 0 && (
            <div className="rounded-md border border-tierB-border bg-tierB-bg px-3 py-2.5">
              <p className="text-sm font-medium text-tierB-text">
                {t(locale, "scam.matchCount")
                  .replace("{n}", String(checked.size))
                  .replace("{total}", String(data.types.length))}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-tierB-text">
                {t(locale, "scam.matchNote")}
              </p>
            </div>
          )}

          <section>
            <h2 className="text-sm font-semibold">{t(locale, "scam.stepsTitle")}</h2>
            <p className="mt-1 text-xs text-gray-600">{t(locale, "scam.stepsNote")}</p>
            <ol className="mt-2 space-y-2">
              {data.response_steps.map((s) => (
                <li
                  key={s.seq}
                  className="flex items-center gap-3 rounded-md border border-gray-200 bg-white px-3 py-2.5"
                >
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white">
                    {s.seq}
                  </span>
                  <span className="text-sm">{s.label}</span>
                </li>
              ))}
            </ol>
            <ul className="mt-1.5 space-y-0.5">
              {data.response_evidence.map((e) => (
                <li key={e.doc_id} className="text-[11px] text-gray-500">
                  {e.publisher}{" "}
                  <a
                    href={e.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-700 underline underline-offset-2"
                  >
                    {t(locale, "scam.source")} ↗
                  </a>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="text-sm font-semibold">{t(locale, "scam.contactsTitle")}</h2>
            <ul className="mt-2 space-y-2">
              {data.contacts.map((c) => (
                <li
                  key={c.code}
                  className="flex items-baseline gap-3 rounded-md border border-gray-200 bg-white px-3 py-2.5"
                >
                  {/* 전화 연결은 이용자가 누르게 둔다 — 자동으로 걸지 않는다. */}
                  <a
                    href={`tel:${c.number}`}
                    className="shrink-0 text-base font-semibold text-blue-700 underline underline-offset-2"
                  >
                    {c.number}
                  </a>
                  <div className="min-w-0">
                    <p className="text-sm">
                      {c.org}
                      {c.primary && (
                        <span className="ml-1.5 rounded bg-tierA-bg px-1.5 py-0.5 text-[11px] text-tierA-text">
                          {t(locale, "scam.primaryBadge")}
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-gray-600">{c.role}</p>
                  </div>
                </li>
              ))}
            </ul>
          </section>

          <p className="border-t border-gray-200 pt-3 text-[11px] text-gray-500">
            {t(locale, "scam.updatedAt")} {data.updated_at}
          </p>
        </>
      )}
    </div>
  );
}
