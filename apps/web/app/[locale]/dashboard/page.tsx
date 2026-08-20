"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { JourneyStepper } from "@/components/dashboard/JourneyStepper";
import { TaskCard } from "@/components/dashboard/TaskCard";
import { t } from "@/lib/i18n";
import { loadContext, type SessionContext } from "@/lib/session";
import { warmUp } from "@/lib/sse";

/** S3 — 정착 대시보드. F2·F4·F3·F5 의 공통 진입점이다. */
export default function DashboardPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = use(params);
  const [ctx, setCtx] = useState<SessionContext>({ purposes: [] });

  useEffect(() => {
    setCtx(loadContext());
    warmUp();
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">{t(locale, "dashboard.title")}</h1>
        <p className="mt-1 text-sm text-gray-600">{t(locale, "dashboard.subtitle")}</p>
      </div>

      <JourneyStepper locale={locale} />

      {/* 프로필이 비면 안내가 넓어진다는 것을 밝힌다. 강제하지는 않는다 —
          입력을 강제하면 첫 화면에서 이탈한다(F1 의 설계). */}
      <div className="flex items-center justify-between gap-3 rounded-md bg-gray-50 px-3 py-2">
        <p className="text-xs text-gray-600">
          {ctx.visa
            ? `${t(locale, "institution.forVisa")}: ${ctx.visa}`
            : t(locale, "dashboard.profileHint")}
        </p>
        <Link
          href={`/${locale}/profile`}
          className="shrink-0 text-xs text-blue-700 underline underline-offset-2"
        >
          {t(locale, "dashboard.editProfile")}
        </Link>
      </div>

      <ul className="space-y-3">
        <TaskCard
          href={`/${locale}/institutions`}
          icon="🏦"
          title={t(locale, "dashboard.cardInstitutions")}
          description={t(locale, "dashboard.cardInstitutionsDesc")}
        />
        <TaskCard
          href={`/${locale}/guide/limit-release`}
          icon="🔓"
          title={t(locale, "dashboard.cardGuide")}
          description={t(locale, "dashboard.cardGuideDesc")}
        />
        <TaskCard
          href={`/${locale}/tools/checklist`}
          icon="📋"
          title={t(locale, "dashboard.cardChecklist")}
          description={t(locale, "dashboard.cardChecklistDesc")}
        />
        <TaskCard
          href={`/${locale}/chat`}
          icon="💬"
          title={t(locale, "dashboard.cardChat")}
          description={t(locale, "dashboard.cardChatDesc")}
        />
      </ul>
    </div>
  );
}
