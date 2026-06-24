"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Settings, Sparkles, Subtitles, Bell, Server } from "lucide-react";
import { cn } from "@/lib/utils";

const tabs = [
  { href: "/settings/general", label: "基础与路径", icon: Settings },
  { href: "/settings/ai", label: "AI 识别", icon: Sparkles },
  { href: "/settings/subtitle", label: "字幕", icon: Subtitles },
  { href: "/settings/notify", label: "通知", icon: Bell },
  { href: "/settings/advanced", label: "高级", icon: Server },
];

export function SettingsTabs() {
  const pathname = usePathname();
  return (
    <div className="border-b overflow-x-auto">
      <div className="flex space-x-8 min-w-max px-1">
        {tabs.map((tab) => {
          const isActive = pathname === tab.href;
          const Icon = tab.icon;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              prefetch={false}
              className={cn(
                "flex items-center gap-2 pb-3 text-sm font-medium transition-colors border-b-2",
                isActive
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted"
              )}
            >
              <Icon className="h-4 w-4" />
              {tab.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
