"use client";

/**
 * `/chat` SSE 클라이언트 (planner §9.2).
 *
 * `EventSource` 는 GET 만 지원하는데 우리는 POST 로 컨텍스트를 보내야 하므로
 * `fetch` + `ReadableStream` 으로 직접 파싱한다.
 *
 * **`invalidate` 처리가 핵심이다.** 이미 표시된 텍스트를 폴백 카드로 교체해야
 * 한다. 이 처리를 빠뜨리면 서버는 "차단했다"고 하는데 화면엔 환각이 남는다.
 */

import { API_BASE } from "./api";
import type { SessionContext } from "./session";

export type Tier = "A" | "B" | "C";

/** 답변 뒤에 이어지는 행동 제안 (F4 → F3). **서버가 결정한다.** */
export interface NextAction {
  type: "checklist";
  /** 문구는 클라이언트가 갖는다 — 서버가 3언어를 또 들고 있지 않는다 */
  label_key: string;
  params: Record<string, string>;
}

export interface EvidenceRef {
  ref: number;
  chunk_id: string;
  title: string;
  publisher: string;
  published_at: string | null;
  verified_at: string | null;
  url: string | null;
  stale: boolean;
}

export interface ChatCallbacks {
  onMeta?: (m: { tier: Tier; retrieval: { top1_score: number; stale: boolean } }) => void;
  onToken?: (t: string) => void;
  onCitations?: (items: EvidenceRef[]) => void;
  /** 표시된 텍스트를 이 문구로 **교체**하라 */
  onInvalidate?: (p: { reason: string; fallback_text: string; contacts: string[] }) => void;
  onDone?: (d: {
    tier: Tier;
    latency_ms: number;
    fallback_reason: string | null;
    /** 폴백일 때는 서버가 붙이지 않는다 — 답한 척이 되므로 */
    next_action?: NextAction | null;
  }) => void;
  onError?: (e: unknown) => void;
}

export async function streamChat(
  body: { lang: string; message: string; context: SessionContext; history: unknown[] },
  cb: ChatCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  return stream("/chat", body, cb, signal);
}

/**
 * F4 한도제한계좌 해제 가이드.
 *
 * `/chat` 과 **같은 이벤트 스트림**이다. 서버가 프로필로 한국어 질의를
 * 조립하므로 클라이언트는 질문 문장을 만들지 않는다 — 검색어 규칙이 두 곳에
 * 생기는 것을 막는다.
 */
export async function streamGuide(
  body: { lang: string; context: SessionContext },
  cb: ChatCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  return stream("/guide/limit-release", body, cb, signal);
}

async function stream(
  path: string,
  body: { lang: string } & Record<string, unknown>,
  cb: ChatCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-KB-Lang": body.lang },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE 는 빈 줄로 이벤트를 구분한다. 마지막 조각은 미완성일 수 있으므로 남긴다.
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) dispatch(block, cb);
    }
    if (buffer.trim()) dispatch(buffer, cb);
  } catch (e) {
    if ((e as Error)?.name !== "AbortError") cb.onError?.(e);
  }
}

function dispatch(block: string, cb: ChatCallbacks): void {
  let name = "";
  let raw = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7).trim();
    else if (line.startsWith("data: ")) raw += line.slice(6);
  }
  if (!name || !raw) return;

  let data: any;
  try {
    data = JSON.parse(raw);
  } catch {
    return; // 깨진 이벤트 하나 때문에 스트림 전체를 죽이지 않는다
  }

  switch (name) {
    case "meta": cb.onMeta?.(data); break;
    case "token": cb.onToken?.(data.t); break;
    case "citations": cb.onCitations?.(data.items); break;
    case "invalidate": cb.onInvalidate?.(data); break;
    case "done": cb.onDone?.(data); break;
  }
}

/**
 * 백엔드 웜업 (planner §15.3-3).
 *
 * 임베딩 모델 로드만으로 **4초**가 든다(실측). Render 콜드스타트와는 별개다.
 * 이용자가 언어를 고르고 프로필을 입력하는 동안 미리 깨워 둔다.
 */
export function warmUp(): void {
  const base = API_BASE.replace(/\/api\/v1$/, "");
  fetch(`${base}/healthz`, { cache: "no-store" }).catch(() => {});
}
