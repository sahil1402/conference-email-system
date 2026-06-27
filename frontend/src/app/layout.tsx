import type { Metadata } from "next";
import "./globals.css";

import { Providers } from "@/lib/providers";
import { AppShell } from "@/components/layout";

export const metadata: Metadata = {
  title: {
    default: "ConfMail — Conference Email System",
    template: "%s · ConfMail",
  },
  description:
    "Automated conference email reply & routing for program committees.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
