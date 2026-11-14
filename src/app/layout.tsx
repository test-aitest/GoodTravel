import type { Metadata } from "next";
import { Theme } from "@astryxdesign/core/theme";
import { defaultThemeSlug, themes } from "@/astryx-themes";
import "./globals.css";

export const metadata: Metadata = {
  title: "ClientOps",
  description: "AI workers that run each client's operations on their own computer.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>
        <Theme theme={themes[defaultThemeSlug]}>{children}</Theme>
      </body>
    </html>
  );
}
