"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { TierNotice } from "@/components/chat/TierNotice";
import {
  checklistParamsFrom,
  downloadChecklistPdf,
  fetchChecklist,
  isAbortError,
  type ChecklistParams,
  type ChecklistResponse,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { loadContext } from "@/lib/session";

/**
 * S5-1 — 서류 체크리스트 (F3).
 *
 * **화면 안에서 먼저 완결된다.** PDF 는 그 위에 얹히는 것이고, 조판이 검증되지
 * 않았거나 렌더가 불가능한 환경에서는 PDF 만 빠진다(planner §12.3).
 * 그래서 PDF 버튼이 실패해도 목록은 그대로 남는다.
 */
export default function ChecklistPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ visa?: string; purpose?: string }>;
}) {
  const { locale } = use(params);
  const query = use(searchParams);

  const [doc, setDoc] = useState<ChecklistResponse | null>(null);
  const [req, setReq] = useState<ChecklistParams | null>(null);
  const [busy, setBusy] = useState(false);
  const [pdfFailed, setPdfFailed] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    const ctx = loadContext();
    // F4 가 넘겨준 파라미터가 있으면 그것을 우선한다 — 이용자가 방금 본
    // 안내와 같은 조건이어야 한다.
    const p = checklistParamsFrom(locale, {
      ...ctx,
      visa: (query.visa as typeof ctx.visa) ?? ctx.visa,
      purposes: query.purpose
        ? [query.purpose as (typeof ctx.purposes)[number]]
        : ctx.purposes,
    });
    setReq(p);
    const ac = new AbortController();
    // F2 와 같은 이유다 — 새 조회는 지난 실패를 지우고, 취소는 실패로 세지
    // 않는다(`isAbortError`).
    setError(false);
    fetchChecklist(p, ac.signal)
      .then(setDoc)
      .catch((e) => { if (!isAbortError(e)) setError(true); });
    return () => ac.abort();
  }, [locale, query.visa, query.purpose]);

  async function download() {
    if (!req || !doc || busy) return;
    setBusy(true);
    setPdfFailed(false);
    try {
      const r = await downloadChecklistPdf(req, doc.filename);
      if (!r.ok) setPdfFailed(true);
    } catch {
      setPdfFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">{t(locale, "checklist.title")}</h1>
        <p className="mt-1 text-sm text-gray-600">{t(locale, "checklist.subtitle")}</p>
      </div>

      {error && <p className="text-sm text-red-700">{t(locale, "chat.error")}</p>}
      {!doc && !error && <p className="text-sm text-gray-500">{t(locale, "common.loading")}</p>}

      {doc && (
        <>
          <TierNotice tier={doc.disclaimer_tier} locale={locale} />

          <button
            type="button"
            onClick={download}
            disabled={busy}
            className="tap w-full rounded-lg bg-blue-700 font-medium text-white hover:bg-blue-800 disabled:opacity-60"
          >
            {busy ? t(locale, "checklist.downloading") : t(locale, "checklist.download")}
          </button>

          {/* ★ PDF 가 안 되어도 목록은 남는다 (planner §12.3) */}
          {pdfFailed && (
            <p className="rounded-md border border-tierB-border bg-tierB-bg px-3 py-2 text-xs text-tierB-text">
              {t(locale, "checklist.pdfUnavailable")}
            </p>
          )}

          {doc.sections.map((section) => (
            <section key={section.key} className="space-y-2">
              <h2 className="border-b border-gray-300 pb-1 text-sm font-semibold">
                {section.title}
              </h2>

              {section.caveat && (
                <p className="rounded-md border border-tierB-border bg-tierB-bg px-3 py-2 text-xs leading-relaxed text-tierB-text">
                  {section.caveat}
                </p>
              )}

              {section.items.length > 0 ? (
                <ul className="space-y-1.5">
                  {section.items.map((item) => (
                    <li
                      key={item.code}
                      className="flex items-baseline gap-2 rounded border border-gray-200 px-3 py-2"
                    >
                      <span aria-hidden className="text-gray-400">☐</span>
                      <span className="text-sm">{item.label}</span>
                      {/* 창구에서 보여줄 한국어를 반드시 함께 적는다 */}
                      {item.label !== item.label_ko && (
                        <span className="ml-auto text-xs text-gray-500">{item.label_ko}</span>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="rounded border border-dashed border-gray-300 px-3 py-2 text-xs text-gray-600">
                  {t(locale, "checklist.notConfirmed")}
                </p>
              )}

              {section.notes && (
                <p className="text-xs leading-relaxed text-gray-600">{section.notes}</p>
              )}

              {section.evidence.length > 0 && (
                <ul className="space-y-0.5">
                  {section.evidence.map((e) => (
                    <li key={e.doc_id} className="text-[11px] text-gray-500">
                      {t(locale, "checklist.sources")} · {e.publisher}{" "}
                      <a href={e.url} target="_blank" rel="noopener noreferrer"
                         className="text-blue-700 underline underline-offset-2">
                        {t(locale, "chat.viewOriginal")} ↗
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}

          <p className="text-[11px] text-gray-500">
            {t(locale, "checklist.sources")} · {doc.generated_at}
            {" · "}
            <Link href={`/${locale}/profile`} className="text-blue-700 underline underline-offset-2">
              {t(locale, "dashboard.editProfile")}
            </Link>
          </p>
        </>
      )}
    </div>
  );
}
