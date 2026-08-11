/**
 * 응답 계층 표시 (planner §7.1, §11.3).
 *
 * **색상만으로 계층을 구분하지 않는다.** 아이콘과 문구를 함께 쓴다 —
 * 색각 이상 이용자에게 파랑/노랑 구분은 정보가 되지 않는다.
 */
import { t } from "@/lib/i18n";
import type { Tier } from "@/lib/sse";

const STYLES: Record<Tier, { box: string; icon: string; label: string; notice?: string }> = {
  A: { box: "bg-tierA-bg border-tierA-border text-tierA-text", icon: "📘",
       label: "tier.aLabel" },
  B: { box: "bg-tierB-bg border-tierB-border text-tierB-text", icon: "⚠️",
       label: "tier.bLabel", notice: "tier.bNotice" },
  C: { box: "bg-tierC-bg border-tierC-border text-tierC-text", icon: "ℹ️",
       label: "tier.cLabel", notice: "tier.cNotice" },
};

export function TierNotice({ tier, locale }: { tier: Tier; locale: string }) {
  const s = STYLES[tier];
  return (
    <div className={`rounded-md border px-3 py-2 text-xs ${s.box}`}>
      <p className="flex items-center gap-1.5 font-medium">
        <span aria-hidden>{s.icon}</span>
        {t(locale, s.label)}
      </p>
      {s.notice && <p className="mt-1 leading-relaxed opacity-90">{t(locale, s.notice)}</p>}
    </div>
  );
}
