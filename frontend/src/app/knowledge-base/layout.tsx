import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = { title: "Knowledge Base" };

export default function KnowledgeBaseLayout({ children }: { children: ReactNode }) {
  return children;
}
