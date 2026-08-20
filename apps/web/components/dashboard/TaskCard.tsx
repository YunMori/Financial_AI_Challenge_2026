/** 대시보드 진입 카드 (planner §11.1 S3). 터치 타깃 44px 이상. */
import Link from "next/link";

export function TaskCard({
  href,
  icon,
  title,
  description,
}: {
  href: string;
  icon: string;
  title: string;
  description: string;
}) {
  return (
    <li>
      <Link
        href={href}
        className="flex min-h-16 items-start gap-3 rounded-lg border border-gray-200 bg-white p-4 transition hover:bg-gray-50"
      >
        <span aria-hidden className="text-xl leading-none">{icon}</span>
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-gray-900">{title}</span>
          <span className="mt-0.5 block text-xs leading-relaxed text-gray-600">{description}</span>
        </span>
      </Link>
    </li>
  );
}
