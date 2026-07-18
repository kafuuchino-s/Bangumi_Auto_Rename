"use client";

import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Settings, Sparkles, Subtitles, Bell, Server } from "lucide-react";
import { cn } from "@/lib/utils";

const tabs = [
  { href: "/settings/general", key: "general", icon: Settings },
  { href: "/settings/ai", key: "ai", icon: Sparkles },
  { href: "/settings/subtitle", key: "subtitle", icon: Subtitles },
  { href: "/settings/notify", key: "notify", icon: Bell },
  { href: "/settings/advanced", key: "advanced", icon: Server },
] as const;

export function SettingsTabs() {
  const { t } = useTranslation("settings");
  return (
    <div className="border-b overflow-x-auto">
      <div className="flex space-x-8 min-w-max px-1">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <NavLink
              key={tab.href}
              to={tab.href}
              className={({ isActive }) => cn(
                "flex items-center gap-2 pb-3 text-sm font-medium transition-colors border-b-2",
                isActive ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted",
              )}
            >
              <Icon className="h-4 w-4" />
              {t(tab.key)}
            </NavLink>
          );
        })}
      </div>
    </div>
  );
}
