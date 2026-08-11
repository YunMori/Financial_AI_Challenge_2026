"use client";

import { use, useEffect, useRef, useState } from "react";

import { CitationBadge } from "@/components/chat/CitationBadge";
import { TierNotice } from "@/components/chat/TierNotice";
import { AiDisclosure } from "@/components/common/Notices";
import { t } from "@/lib/i18n";
import { loadContext, type SessionContext } from "@/lib/session";
import { streamChat, warmUp, type EvidenceRef, type Tier } from "@/lib/sse";

const EXAMPLES: Record<string, string[]> = {
  ko: ["한도제한계좌 이체 한도가 왜 100만원인가요?",
       "외국인등록증을 받으려면 어떤 서류가 필요한가요?",
       "모바일 외국인등록증으로 계좌를 열 수 있는 은행은?"],
  en: ["Why can I only transfer 1 million won?",
       "What documents do I need for alien registration?",
       "Which banks accept the mobile alien registration card?"],
  vi: ["Tại sao tôi chỉ chuyển được 1 triệu won?",
       "Cần giấy tờ gì để đăng ký người nước ngoài?",
       "Ngân hàng nào chấp nhận thẻ đăng ký điện tử?"],
};

interface Turn {
  role: "user" | "assistant";
  text: string;
  tier?: Tier;
  refs?: EvidenceRef[];
  /** 폴백으로 교체된 답변인지 — 화면에서 구분해 보여준다 */
  replaced?: boolean;
  contacts?: string[];
}

/** S4 — 대화형 상담 */
export default function ChatPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = use(params);
  const [ctx, setCtx] = useState<SessionContext>({ purposes: [] });
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setCtx(loadContext()); warmUp(); }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  async function ask(question: string) {
    if (!question.trim() || busy) return;
    setBusy(true);
    setError(false);
    setInput("");

    const history = turns.slice(-6).map((x) => ({ role: x.role, content: x.text }));
    setTurns((prev) => [...prev, { role: "user", text: question },
                                 { role: "assistant", text: "" }]);

    const patch = (fn: (last: Turn) => Turn) =>
      setTurns((prev) => prev.map((x, i) => (i === prev.length - 1 ? fn(x) : x)));

    await streamChat(
      { lang: locale, message: question, context: ctx, history },
      {
        onToken: (tok) => patch((last) => ({ ...last, text: last.text + tok })),
        onCitations: (items) => patch((last) => ({ ...last, refs: items })),
        // ★ 출력 검사에 실패했다 — 표시된 텍스트를 **교체**한다.
        // 이 처리가 없으면 서버는 차단했다는데 화면엔 환각이 남는다.
        onInvalidate: (p) =>
          patch((last) => ({ ...last, text: p.fallback_text, replaced: true,
                             contacts: p.contacts })),
        onDone: (d) => patch((last) => ({ ...last, tier: d.tier })),
        onError: () => { setError(true); patch((last) => ({ ...last, text: "" })); },
      },
    );
    setBusy(false);
  }

  return (
    <div className="space-y-5">
      {turns.length === 0 && (
        <section className="space-y-3">
          <h1 className="text-lg font-semibold">{t(locale, "chat.title")}</h1>
          <p className="text-xs font-medium text-gray-500">{t(locale, "chat.examples")}</p>
          <ul className="space-y-2">
            {(EXAMPLES[locale] ?? EXAMPLES.ko).map((ex) => (
              <li key={ex}>
                <button type="button" onClick={() => ask(ex)}
                        className="w-full rounded-lg border border-gray-200 px-3 py-2.5 text-left text-sm hover:bg-gray-50">
                  {ex}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <ol className="space-y-5">
        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <li key={i} className="flex justify-end">
              <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-blue-700 px-4 py-2.5 text-sm text-white">
                {turn.text}
              </p>
            </li>
          ) : (
            <li key={i} className="space-y-2.5">
              {turn.text ? (
                <div className={`rounded-2xl rounded-bl-sm px-4 py-3 text-sm leading-relaxed
                                 ${turn.replaced ? "bg-gray-100 text-gray-700" : "bg-gray-50"}`}>
                  <p className="whitespace-pre-wrap">{turn.text}</p>
                  {turn.contacts?.length ? (
                    <ul className="mt-3 space-y-1 border-t border-gray-200 pt-2 text-xs text-gray-600">
                      {turn.contacts.map((c) => <li key={c}>· {c}</li>)}
                    </ul>
                  ) : null}
                </div>
              ) : (
                busy && <p className="text-sm text-gray-500">{t(locale, "chat.thinking")}</p>
              )}

              {turn.tier && <TierNotice tier={turn.tier} locale={locale} />}

              {turn.refs?.length ? (
                <details open className="rounded-md bg-gray-50 px-3 py-2">
                  <summary className="cursor-pointer text-xs font-medium text-gray-700">
                    {t(locale, "chat.sources")} ({turn.refs.length})
                  </summary>
                  <ul className="mt-2 space-y-2">
                    {turn.refs.map((r) => (
                      <CitationBadge key={r.chunk_id} item={r} locale={locale} />
                    ))}
                  </ul>
                </details>
              ) : null}

              {turn.text && <AiDisclosure locale={locale} />}
            </li>
          ),
        )}
      </ol>

      {error && <p className="text-sm text-red-700">{t(locale, "chat.error")}</p>}
      <div ref={endRef} />

      <form
        onSubmit={(e) => { e.preventDefault(); ask(input); }}
        className="sticky bottom-0 flex gap-2 border-t border-gray-200 bg-white pb-4 pt-3"
      >
        <label htmlFor="q" className="sr-only">{t(locale, "chat.title")}</label>
        <input
          id="q"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={t(locale, "chat.placeholder")}
          disabled={busy}
          maxLength={2000}
          className="tap min-w-0 flex-1 rounded-lg border border-gray-300 text-sm disabled:bg-gray-50"
        />
        <button type="submit" disabled={busy || !input.trim()}
                className="tap shrink-0 rounded-lg bg-blue-700 text-sm font-medium text-white disabled:bg-gray-300">
          {t(locale, "chat.send")}
        </button>
      </form>
    </div>
  );
}
