import { t } from "@/lib/i18n";

/**
 * 이용약관 및 안내 (planner §8.4).
 *
 * "오안내 손해" 대응 3종 중 마지막 조각이다 — 나머지 둘(계층 B 강제 문구,
 * 화면 상단 상시 배너)은 이미 있다.
 *
 * ★ **"하지 않는 일"을 "하는 일"만큼 크게 쓴다.** 이 서비스의 신뢰는 무엇을
 * 답하느냐가 아니라 **무엇을 답하지 않느냐**에서 나온다. 개별 심사 예측,
 * 상품 권유, 사기 판정은 하지 않으며 그것이 설계다.
 *
 * ★ **서버 컴포넌트다.** 상태도 조회도 없는 정적 문서이며, 클라이언트 번들에
 * 실을 이유가 없다.
 */
export default async function TermsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;

  const sections: { title: string; bodies: string[] }[] = [
    { title: "terms.purposeTitle", bodies: ["terms.purposeBody"] },
    {
      title: "terms.notTitle",
      bodies: ["terms.notAdvice", "terms.notApproval", "terms.notRecord"],
    },
    { title: "terms.authorityTitle", bodies: ["terms.authorityBody"] },
    { title: "terms.aiTitle", bodies: ["terms.aiBody"] },
    { title: "terms.privacyTitle", bodies: ["terms.privacyBody"] },
    { title: "terms.contactTitle", bodies: ["terms.contactBody"] },
  ];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">{t(locale, "terms.title")}</h1>
        <p className="mt-1 text-xs text-gray-500">
          {t(locale, "terms.updated")} 2026-08-21
        </p>
      </div>

      {sections.map((s) => (
        <section key={s.title}>
          <h2 className="text-sm font-semibold">{t(locale, s.title)}</h2>
          <div className="mt-1.5 space-y-2">
            {s.bodies.map((b) => (
              <p key={b} className="text-sm leading-relaxed text-gray-700">
                {t(locale, b)}
              </p>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
