/**
 * 정착 여정 (planner §11.1 S3).
 *
 * 계좌개설 → 한도해제 → 송금. **송금은 M1 범위 밖이라 "준비 중"으로 둔다** —
 * 반쪽 기능을 열어 두지 않는 것이 §1.1 의 원칙이고, 단계를 지워 버리면
 * 이용자가 전체 절차를 이해하지 못한다.
 */
import Link from "next/link";

import { t } from "@/lib/i18n";

interface Step {
  key: string;
  labelKey: string;
  href?: string;
}

export function JourneyStepper({ locale }: { locale: string }) {
  const steps: Step[] = [
    { key: "account", labelKey: "dashboard.stepAccount", href: `/${locale}/institutions` },
    { key: "limit", labelKey: "dashboard.stepLimit", href: `/${locale}/guide/limit-release` },
    { key: "remit", labelKey: "dashboard.stepRemit" },
  ];

  return (
    <ol className="flex items-stretch gap-2">
      {steps.map((s, i) => {
        const body = (
          <div
            className={`flex h-full flex-col justify-center rounded-lg border px-3 py-2.5 text-center
              ${s.href
                ? "border-blue-200 bg-blue-50 text-blue-900"
                : "border-gray-200 bg-gray-50 text-gray-400"}`}
          >
            <span className="text-[11px] opacity-70">{i + 1}</span>
            <span className="text-xs font-medium">{t(locale, s.labelKey)}</span>
            {!s.href && (
              <span className="mt-0.5 text-[10px]">{t(locale, "dashboard.stepRemitSoon")}</span>
            )}
          </div>
        );
        return (
          <li key={s.key} className="flex-1">
            {s.href ? (
              <Link href={s.href} className="block h-full">
                {body}
              </Link>
            ) : (
              body
            )}
          </li>
        );
      })}
    </ol>
  );
}
