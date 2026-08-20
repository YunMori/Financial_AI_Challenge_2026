"use client";

import Link from "next/link";
import { use, useEffect } from "react";

import { LOCALES, LOCALE_FLAGS, LOCALE_NAMES, t } from "@/lib/i18n";
import { warmUp } from "@/lib/sse";

/** S1 — 언어 선택 */
export default function LanguagePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = use(params);

  // 이용자가 언어를 고르고 프로필을 입력하는 동안 백엔드를 깨운다.
  // 임베딩 모델 로드만 4초가 걸린다(실측) — planner §15.3-3
  useEffect(() => { warmUp(); }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">{t(locale, "onboarding.chooseLanguage")}</h1>

      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {LOCALES.map((loc) => (
          <li key={loc}>
            {/* 각 언어를 그 언어 자체로 표기한다 — 한국어를 못 읽어도 찾을 수 있어야 한다 */}
            <Link
              href={`/${loc}/profile`}
              className={`tap flex items-center justify-center gap-2 rounded-lg border text-base font-medium transition
                ${loc === locale ? "border-blue-500 bg-blue-50 text-blue-800" : "border-gray-300 hover:bg-gray-50"}`}
            >
              <span aria-hidden className="text-xl">{LOCALE_FLAGS[loc]}</span>
              {LOCALE_NAMES[loc]}
            </Link>
          </li>
        ))}
      </ul>

      <Link href={`/${locale}/dashboard`} className="block text-sm text-blue-700 underline underline-offset-2">
        {t(locale, "common.skip")} →
      </Link>
    </div>
  );
}
