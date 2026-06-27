import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = { title: "Audit Log" };

export default function AuditLayout({ children }: { children: ReactNode }) {
  return children;
}
