"use client";

import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";

import { CitationBadge } from "@/components/chat/CitationBadge";
import { TierNotice } from "@/components/chat/TierNotice";
import { AiDisclosure } from "@/components/common/Notices";
import { t } from "@/lib/i18n";
import { loadContext, type SessionContext } from "@/lib/session";
import { streamGuide, warmUp, type EvidenceRef, type NextAction, type Tier } from "@/lib/sse";

/**
 * F4 — 한도제한계좌 해제 가이드.
 *
 * `/chat` 과 **같은 SSE 를 그대로 탄다.** 질문 문장은 서버가 프로필로 만든다 —
 * 검색어 규칙이 클라이언트에도 생기면 두 곳이 어긋난다.
 *
 * ★ `invalidate` 처리를 여기서도 한다. 챗봇에만 넣고 이 화면에서 빠뜨리면
 *   "차단했다"고 하면서 화면엔 환각이 남는 사고가 이쪽 경로로 난다.
 */
export default function LimitReleasePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = use(params);
  const router = useRouter();

  const [ctx, setCtx] = useState<SessionContext>({ purposes: [] });
  const [text, setText] = useState("");
  const [tier, setTier] = useState<Tier | null>(null);
  const [refs, setRefs] = useState<EvidenceRef[]>([]);
  const [replaced, setReplaced] = useState(false);
  const [contacts, setContacts] = useState<string[]>([]);
  const [nextAction, setNextAction] = useState<NextAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    setCtx(loadContext());
    warmUp();
  }, []);

  async function run() {
    if (busy) return;
    setBusy(true);
    setStarted(true);
    setError(false);
    setText("");
    setTier(null);
    setRefs([]);
    setReplaced(false);
    setContacts([]);
    setNextAction(null);

    await streamGuide(
      { lang: locale, context: ctx },
      {
        onToken: (tok) => setText((prev) => prev + tok),
        onCitations: setRefs,
        // ★ 표시된 텍스트를 폴백 문구로 **교체**한다.
        onInvalidate: (p) => {
          setText(p.fallback_text);
          setReplaced(true);
          setContacts(p.contacts);
        },
        onDone: (d) => {
          setTier(d.tier);
          setNextAction(d.next_action ?? null);
        },
        onError: () => setError(true),
      },
    );
    setBusy(false);
  }

  function goChecklist() {
    if (!nextAction) return;
    const q = new URLSearchParams(nextAction.params);
    router.push(`/${locale}/tools/checklist?${q}`);
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">{t(locale, "guide.title")}</h1>
        <p className="mt-1 text-sm text-gray-600">{t(locale, "guide.subtitle")}</p>
      </div>

      {!started && (
        <button
          type="button"
          onClick={run}
          className="tap w-full rounded-lg bg-blue-700 font-medium text-white hover:bg-blue-800"
        >
          {t(locale, "guide.start")}
        </button>
      )}

      {busy && !text && <p className="text-sm text-gray-500">{t(locale, "guide.loading")}</p>}

      {text && (
        <div className="space-y-3">
          <div
            className={`whitespace-pre-wrap rounded-lg border p-4 text-sm leading-relaxed
              ${replaced ? "border-tierC-border bg-tierC-bg" : "border-gray-200 bg-white"}`}
          >
            {text}
          </div>

          {contacts.length > 0 && (
            <ul className="space-y-1">
              {contacts.map((c) => (
                <li key={c} className="text-xs text-gray-600">
                  <span aria-hidden className="mr-1">☎</span>
                  {c}
                </li>
              ))}
            </ul>
          )}

          {tier && <TierNotice tier={tier} locale={locale} />}

          {refs.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-gray-700">
                {t(locale, "chat.sources")}
              </p>
              <ul className="space-y-1.5">
                {refs.map((r) => (
                  <CitationBadge key={r.chunk_id} item={r} locale={locale} />
                ))}
              </ul>
            </div>
          )}

          <AiDisclosure locale={locale} />

          {/* F4 → F3. 서버가 폴백일 때는 붙이지 않으므로 여기서 다시 판단하지 않는다. */}
          {nextAction && (
            <button
              type="button"
              onClick={goChecklist}
              className="tap w-full rounded-lg border border-blue-600 font-medium text-blue-700 hover:bg-blue-50"
            >
              {t(locale, nextAction.label_key)} →
            </button>
          )}
        </div>
      )}

      {error && (
        <div className="space-y-2">
          <p className="text-sm text-red-700">{t(locale, "chat.error")}</p>
          <button
            type="button"
            onClick={run}
            className="tap rounded-lg border border-gray-300 text-sm hover:bg-gray-50"
          >
            {t(locale, "guide.retry")}
          </button>
        </div>
      )}
    </div>
  );
}
