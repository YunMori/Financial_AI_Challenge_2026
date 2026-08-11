import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "K-Buddy",
  description: "체류자격 기반 외국인 금융정착 안내",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
