"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ListTodo,
  Subtitles,
  Settings,
  FileText,
  Menu,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const navItems = [
  { href: "/", label: "任务", icon: ListTodo },
  { href: "/subtitles", label: "字幕", icon: Subtitles },
  { href: "/logs", label: "日志", icon: FileText },
  { href: "/settings", label: "配置", icon: Settings },
];

export function TopNavbar({
  onToggleSidebar,
}: {
  onToggleSidebar?: () => void;
}) {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-14 items-center px-4 gap-3">
        {/* 菜单切换（小屏抽屉用；桌面端侧栏常驻不显示） */}
        {onToggleSidebar && (
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9 lg:hidden"
            onClick={onToggleSidebar}
            title="切换侧栏"
          >
            <Menu className="h-5 w-5" />
          </Button>
        )}

        {/* Logo（红色品牌方块 + 标题） */}
        <Link href="/" prefetch={false} className="flex items-center gap-2 flex-shrink-0">
          <span className="h-8 w-8 rounded-lg bg-primary text-primary-foreground flex items-center justify-center font-bold text-sm">
            番
          </span>
          <span className="text-base font-bold hidden sm:inline-block">
            番剧自动重命名
          </span>
        </Link>

        {/* 导航 */}
        <nav className="flex items-center gap-1 ml-2">
          {navItems.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                prefetch={false}
                className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-sm font-medium transition-colors",
                  active
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted"
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="hidden md:inline-block">{item.label}</span>
              </Link>
            );
          })}
        </nav>

        {/* 右侧留空（主题切换在侧栏底部） */}
        <div className="ml-auto" />
      </div>
    </header>
  );
}
