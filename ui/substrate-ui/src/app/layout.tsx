import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { AuthProvider } from "@/contexts/AuthContext";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AI Chat Assistant",
  description: "Chat with AI assistant",
  other: {
    // Tell Dark Reader not to reprocess this page — we handle dark mode ourselves.
    // Prevents hydration mismatches caused by data-darkreader-* attributes injected into SVGs.
    "darkreader-lock": "",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("raavan-theme");if(t){document.documentElement.setAttribute("data-theme",t);document.documentElement.classList.toggle("dark",t==="dark")}else if(window.matchMedia("(prefers-color-scheme: dark)").matches){document.documentElement.setAttribute("data-theme","dark");document.documentElement.classList.add("dark")}else{document.documentElement.setAttribute("data-theme","light");document.documentElement.classList.remove("dark")}}catch(e){}})()`,
          }}
        />
      </head>
      <body
        suppressHydrationWarning
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <ThemeProvider>
          <AuthProvider>
            {children}
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
