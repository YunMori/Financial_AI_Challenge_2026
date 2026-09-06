/** @type {import('next').NextConfig} */
export default {
  reactStrictMode: true,
  // Docker(EC2) 배포에서만 standalone 으로 낸다. Vercel 은 이 변수가 없어
  // 기존 빌드 그대로다 — 배포 경로를 둘로 두더라도 설정은 하나로 둔다.
  output: process.env.NEXT_OUTPUT_STANDALONE ? "standalone" : undefined,
  // 백엔드 URL 은 빌드 타임에 주입된다. 값이 없으면 로컬 기본값.
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1",
    NEXT_PUBLIC_OFFICIAL_DOMAIN: process.env.NEXT_PUBLIC_OFFICIAL_DOMAIN ?? "",
  },
};
