import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agent Builder — Visual Pipeline Editor",
  description: "Drag-and-drop visual builder for agent framework pipelines",
};

// Anti-FOUC: apply saved theme before first paint
const themeScript = `(function(){try{var t=localStorage.getItem('builder-theme')||'dark';document.documentElement.setAttribute('data-theme',t);}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-theme="dark">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
