# K-Buddy Web — Next.js 15
#
# 빌드 컨텍스트는 **apps/web** 이다 (API 와 달리 리포 루트가 아니다):
#   docker build -f apps/web/Dockerfile -t kbuddy-web:latest apps/web
#
# ★ `NEXT_PUBLIC_*` 는 **빌드 타임에 번들에 박힌다.** 런타임 환경변수로는
#   바뀌지 않는다 (`next.config.mjs` 의 `env:` 가 그렇다). 그래서 ARG 로 받는다 —
#   값을 바꾸려면 이미지를 다시 빌드해야 한다.
FROM node:22-slim AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# 같은 오리진(nginx)으로 서비스하므로 상대 경로가 기본값이다.
# `lib/sse.ts` 의 warmUp() 이 `/api/v1` 을 떼어 `/healthz` 를 부르는데,
# 상대 경로면 그 결과가 빈 문자열이 되어 정상 동작한다.
ARG NEXT_PUBLIC_API_BASE=/api/v1
ARG NEXT_PUBLIC_OFFICIAL_DOMAIN=""
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
ENV NEXT_PUBLIC_OFFICIAL_DOMAIN=${NEXT_PUBLIC_OFFICIAL_DOMAIN}

# standalone 출력은 이 이미지에서만 켠다 — Vercel 경로는 건드리지 않는다
# (`next.config.mjs` 가 이 변수로 갈린다).
ENV NEXT_OUTPUT_STANDALONE=1

# vercel.json 의 buildCommand 와 **같은 순서**로 돈다. 다국어 키 누락은
# 빌드에서 걸러야지 화면에서 발견하면 늦다.
RUN npm run check-i18n && npm run build

# ★ `public/` 이 **없을 수 있다.** 지금 비어 있고, git 은 빈 디렉터리를
#   추적하지 않아 새 체크아웃에는 존재하지 않는다. 아래 runner 의 COPY 가
#   그대로 실패한다("/app/public": not found) — 로컬 작업 트리에서는 폴더가
#   보이므로 **배포에서만 터지는** 형태다(실측 2026-09-07, EC2 첫 빌드).
#   정적 자산이 생기면 이 줄은 아무 일도 하지 않는다.
RUN mkdir -p /app/public


FROM node:22-slim AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# standalone 은 필요한 node_modules 만 추려 담는다.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
