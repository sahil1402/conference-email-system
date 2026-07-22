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
      <head>
        {/*
          Anti-flash theme guard: runs synchronously before React hydrates and
          before first paint, so a stored light-mode choice is applied without a
          dark flash. Dark is the unstyled default (bare :root), so we only ever
          SET the attribute for light; anything else is left untouched. Wrapped
          in try/catch since localStorage can throw in private-browsing modes.
        */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{if(localStorage.getItem("confmail-theme")==="light"){document.documentElement.setAttribute("data-theme","light")}}catch(e){}`,
          }}
        />
      </head>
      <body className="min-h-screen">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
