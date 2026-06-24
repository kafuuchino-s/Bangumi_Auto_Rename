import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";

// 注：不使用 next/font/google（Geist），避免离线/受限网络拉取 fonts.gstatic.com 失败。
// 改用 CSS 变量 + 系统字体栈（在 globals.css @theme 中定义 --font-sans/--font-mono 回退）。

export const metadata: Metadata = {
  title: "番剧自动重命名",
  description: "AI-first 媒体整理流水线控制台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <ThemeProvider
          attribute="class"
          defaultTheme="light"
          enableSystem
          disableTransitionOnChange
        >
          <TooltipProvider>{children}</TooltipProvider>
          <Toaster richColors position="top-center" />
        </ThemeProvider>
      </body>
    </html>
  );
}
