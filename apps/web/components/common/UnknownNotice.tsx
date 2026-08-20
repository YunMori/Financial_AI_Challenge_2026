/**
 * "확인 필요" 섹션 (planner §4.2, §10-F2).
 *
 * 서버가 `unknown_institutions` 를 카드 배열과 **따로** 돌려주는 이유가 이
 * 컴포넌트다. 카드에 섞어 회색으로 칠하면 "정보가 있는데 좀 흐린 것"으로
 * 읽힌다. 확인하지 못했다는 사실은 별도의 문단이 되어야 전달된다.
 */
import { t } from "@/lib/i18n";

export function UnknownNotice({
  names,
  locale,
}: {
  names: string[];
  locale: string;
}) {
  if (names.length === 0) return null;

  return (
    <section className="rounded-lg border border-tierC-border bg-tierC-bg p-4">
      <h2 className="flex items-center gap-1.5 text-sm font-semibold text-tierC-text">
        <span aria-hidden>❔</span>
        {t(locale, "institution.unknownTitle")}
      </h2>
      <p className="mt-1.5 text-xs leading-relaxed text-gray-600">
        {t(locale, "institution.unknownBody")}
      </p>
      <ul className="mt-2 flex flex-wrap gap-1.5">
        {names.map((n) => (
          <li key={n} className="rounded border border-gray-300 bg-white px-2 py-0.5 text-xs text-gray-700">
            {n}
          </li>
        ))}
      </ul>
    </section>
  );
}
