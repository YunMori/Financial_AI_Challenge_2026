import { notFound } from "next/navigation";
import Link from "next/link";

import { LangSwitch } from "@/components/common/LangSwitch";
import { AntiPhishingNotice, FinalAuthorityBanner } from "@/components/common/Notices";
import { UnreviewedLanguageNotice } from "@/components/common/UnreviewedLanguageNotice";
import { LOCALES, isLocale, t } from "@/lib/i18n";

export function generateStaticParams() {
  return LOCALES.map((locale) => ({ locale }));
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  return (
    <html lang={locale}>
      <body className="flex min-h-screen flex-col bg-white text-gray-900 antialiased">
        {/* 고지는 레이아웃에 박는다 — 화면마다 조건부로 넣으면 반드시 빠진다 */}
        <FinalAuthorityBanner locale={locale} />
        {/* 미검수 언어 고지. 검수된 언어에서는 아무것도 렌더하지 않는다. */}
        <UnreviewedLanguageNotice locale={locale} />

        <header className="border-b border-gray-200 px-4 py-3">
          <div className="mx-auto flex max-w-3xl flex-wrap items-baseline gap-x-2 gap-y-1">
            <Link href={`/${locale}`} className="text-base font-bold">
              {t(locale, "common.appName")}
            </Link>
            <span className="text-xs text-gray-500">{t(locale, "common.tagline")}</span>
            {/* 언어 전환은 **모든 화면**에 있어야 한다 — 레이아웃에 박는다 */}
            <div className="ml-auto">
              <LangSwitch locale={locale} />
            </div>
          </div>
        </header>

        <main className="flex-1 px-4 py-6">
          <div className="mx-auto max-w-3xl">{children}</div>
        </main>

        <AntiPhishingNotice locale={locale} />
      </body>
    </html>
  );
}
