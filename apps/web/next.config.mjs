/** @type {import('next').NextConfig} */
export default {
  reactStrictMode: true,
  // 백엔드 URL 은 빌드 타임에 주입된다. 값이 없으면 로컬 기본값.
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1",
    NEXT_PUBLIC_OFFICIAL_DOMAIN: process.env.NEXT_PUBLIC_OFFICIAL_DOMAIN ?? "",
  },
};
