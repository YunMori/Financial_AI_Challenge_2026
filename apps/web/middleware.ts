import { NextResponse, type NextRequest } from "next/server";

import { DEFAULT_LOCALE, LOCALES } from "@/lib/i18n";

/**
 * 로케일 라우팅. `/` 로 들어오면 Accept-Language 를 보고 보낸다.
 *
 * 대상 이용자는 한국어 URL 을 직접 칠 일이 없다 — 브라우저 언어를 존중하는
 * 것이 첫 화면 이탈을 줄이는 가장 싼 방법이다.
 */
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (LOCALES.some((l) => pathname === `/${l}` || pathname.startsWith(`/${l}/`))) {
    return NextResponse.next();
  }

  // **이용자의 선호 순서**를 따라야 한다. LOCALES 를 바깥 루프에 두면
  // 우리 배열 순서(ko, en, vi)가 이기고, "vi,en" 을 보낸 베트남어 이용자가
  // 영어 화면을 받는다. q 값이 높은 것부터 훑는다.
  const preferred = (req.headers.get("accept-language") ?? "")
    .split(",")
    .map((part) => {
      const [tag, ...rest] = part.trim().split(";");
      const q = Number(rest.find((r) => r.startsWith("q="))?.slice(2) ?? 1);
      return { lang: tag.trim().split("-")[0].toLowerCase(), q };
    })
    .sort((a, b) => b.q - a.q);

  const locale =
    preferred.map((p) => p.lang).find((lang) => LOCALES.some((l) => l === lang)) ??
    DEFAULT_LOCALE;

  return NextResponse.redirect(new URL(`/${locale}${pathname === "/" ? "" : pathname}`, req.url));
}

export const config = {
  matcher: ["/((?!api|_next|.*\\..*).*)"],
};
