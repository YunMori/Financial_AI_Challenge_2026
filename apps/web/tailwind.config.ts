import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 응답 계층 색상 (planner §11.3). 색상만으로 구분하지 않고
        // 아이콘·문구를 함께 쓴다 — 색각 이상 대응.
        tierA: { bg: "#EFF6FF", border: "#BFDBFE", text: "#1D4ED8" },
        tierB: { bg: "#FEFCE8", border: "#FDE68A", text: "#A16207" },
        tierC: { bg: "#F3F4F6", border: "#D1D5DB", text: "#4B5563" },
      },
    },
  },
  plugins: [],
} satisfies Config;
