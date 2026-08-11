"use client";

import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";

import { t } from "@/lib/i18n";
import {
  PURPOSES, STAY_PERIODS, VISA_CODES,
  type Purpose, type SessionContext, type StayPeriod, type VisaCode,
  loadContext, saveContext,
} from "@/lib/session";
import { warmUp } from "@/lib/sse";

/**
 * S2 — 프로필 입력.
 *
 * **자유 입력 필드가 하나도 없다.** 전부 선택지다. 개인정보 최소 수집의
 * 구현이자, 프롬프트 인젝션 표면을 온보딩 단계에서 0으로 만드는 설계다.
 */
export default function ProfilePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = use(params);
  const router = useRouter();
  const [ctx, setCtx] = useState<SessionContext>({ purposes: [] });

  useEffect(() => {
    setCtx(loadContext());
    warmUp();
  }, []);

  function update(patch: Partial<SessionContext>) {
    const next = { ...ctx, ...patch };
    setCtx(next);
    saveContext(next);
  }

  function togglePurpose(p: Purpose) {
    update({
      purposes: ctx.purposes.includes(p)
        ? ctx.purposes.filter((x) => x !== p)
        : [...ctx.purposes, p],
    });
  }

  return (
    <div className="space-y-7">
      <div>
        <h1 className="text-lg font-semibold">{t(locale, "onboarding.profileTitle")}</h1>
        <p className="mt-1 text-sm text-gray-600">{t(locale, "onboarding.profileSubtitle")}</p>
      </div>

      <fieldset>
        <legend className="mb-2 text-sm font-medium">{t(locale, "onboarding.visa")}</legend>
        <div className="flex flex-wrap gap-2">
          {VISA_CODES.map((v) => (
            <Chip key={v} selected={ctx.visa === v}
                  onClick={() => update({ visa: ctx.visa === v ? undefined : (v as VisaCode) })}>
              {v}
            </Chip>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-sm font-medium">{t(locale, "onboarding.stay")}</legend>
        <div className="flex flex-wrap gap-2">
          {STAY_PERIODS.map((s) => (
            <Chip key={s} selected={ctx.stay === s}
                  onClick={() => update({ stay: ctx.stay === s ? undefined : (s as StayPeriod) })}>
              {t(locale, `stay.${s}`)}
            </Chip>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-sm font-medium">{t(locale, "onboarding.purpose")}</legend>
        <div className="flex flex-wrap gap-2">
          {PURPOSES.map((p) => (
            <Chip key={p} selected={ctx.purposes.includes(p)} onClick={() => togglePurpose(p)}>
              {t(locale, `purpose.${p}`)}
            </Chip>
          ))}
        </div>
      </fieldset>

      {/* 수집 범위를 화면에서 밝힌다 — 개인정보 어필 포인트를 말로만 하지 않는다 */}
      <p className="rounded-md bg-gray-50 px-3 py-2 text-xs leading-relaxed text-gray-600">
        <span aria-hidden className="mr-1">🔒</span>
        {t(locale, "onboarding.noStorage")}
      </p>

      <button
        type="button"
        onClick={() => router.push(`/${locale}/chat`)}
        className="tap w-full rounded-lg bg-blue-700 font-medium text-white hover:bg-blue-800"
      >
        {t(locale, "common.next")}
      </button>
    </div>
  );
}

function Chip({ selected, onClick, children }: {
  selected: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={`min-h-11 rounded-full border px-4 text-sm transition
        ${selected
          ? "border-blue-600 bg-blue-600 font-medium text-white"
          : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"}`}
    >
      {children}
    </button>
  );
}
