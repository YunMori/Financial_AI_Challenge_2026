"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { LOCALES, LOCALE_FLAGS, LOCALE_NAMES, isLocale } from "@/lib/i18n";

/**
 * 언어 전환 (planner §3.1).
 *
 * ★ **이게 없으면 다국어 서비스라는 주장이 화면에서 증명되지 않는다.** 그동안
 * 언어를 바꾸려면 URL 을 직접 쳐야 했고, 대상 이용자는 그럴 수 없다.
 *
 * ★ **현재 경로를 유지한다.** `/vi/tools/scam` 에서 언어를 바꾸면
 * `/en/tools/scam` 으로 간다. 첫 화면으로 되돌리면 이용자가 보던 것을 잃는다.
 *
 * ★ **`Link` 를 쓴다** (router.push 가 아니라). 언어 전환은 공유 가능한 주소여야
 * 하고, 크롤러·스크린리더가 언어 선택지를 목록으로 읽을 수 있어야 한다.
 * 표시 이름은 **그 언어 자체로** 쓴다 — 한국어를 못 읽는 이용자가 찾아야 한다.
 */
export function LangSwitch({ locale }: { locale: string }) {
  const pathname = usePathname() || `/${locale}`;

  // `/ko/tools/scam` → `/{next}/tools/scam`. 첫 세그먼트가 로케일이 아니면
  // (미들웨어가 이미 막지만) 통째로 붙인다.
  const segments = pathname.split("/");
  const rest = isLocale(segments[1] ?? "") ? segments.slice(2).join("/") : segments.join("/");
  const suffix = rest ? `/${rest}` : "";

  return (
    <nav aria-label="Language" className="flex shrink-0 items-center gap-1">
      {LOCALES.map((l) => {
        const current = l === locale;
        return (
          <Link
            key={l}
            href={`/${l}${suffix}`}
            hrefLang={l}
            lang={l}
            aria-current={current ? "true" : undefined}
            className={
              "rounded px-1.5 py-0.5 text-xs " +
              (current
                ? "bg-blue-50 font-medium text-blue-800"
                : "text-gray-600 hover:bg-gray-100")
            }
          >
            <span aria-hidden className="mr-0.5">
              {LOCALE_FLAGS[l]}
            </span>
            {LOCALE_NAMES[l]}
          </Link>
        );
      })}
    </nav>
  );
}
