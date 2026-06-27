import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = { title: "Auto-Replies" };

export default function AutoRepliesLayout({ children }: { children: ReactNode }) {
  return children;
}
