import { notFound } from "next/navigation";
import Link from "next/link";

import { AntiPhishingNotice, FinalAuthorityBanner } from "@/components/common/Notices";
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
        {/* 고지 3종은 레이아웃에 박는다 — 화면마다 조건부로 넣으면 반드시 빠진다 */}
        <FinalAuthorityBanner locale={locale} />

        <header className="border-b border-gray-200 px-4 py-3">
          <div className="mx-auto flex max-w-3xl items-baseline gap-2">
            <Link href={`/${locale}`} className="text-base font-bold">
              {t(locale, "common.appName")}
            </Link>
            <span className="text-xs text-gray-500">{t(locale, "common.tagline")}</span>
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
