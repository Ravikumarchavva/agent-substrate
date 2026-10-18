import { ThemeProvider } from "../components/theme";
import "./globals.css";
import "./workspace.css";
export const metadata = {
  title: "Substrate Data",
  description: "Data workspace",
  icons: {
    icon: "/icon.svg",
  },
};
const bootstrapTheme = `try{var p=localStorage.getItem('substrate-data-theme');var t=p==='light'||p==='dark'?p:matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t}catch{}`;
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="icon" type="image/svg+xml" href="/icon.svg" />
        <script dangerouslySetInnerHTML={{ __html: bootstrapTheme }} />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
