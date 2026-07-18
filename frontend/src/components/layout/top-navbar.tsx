"use client";

import { NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ListTodo, Subtitles, Settings, FileText, Menu } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { LocaleSwitcher } from "@/components/locale-switcher";

const navItems = [
  { href: "/", key: "tasks", icon: ListTodo },
  { href: "/subtitles", key: "subtitles", icon: Subtitles },
  { href: "/logs", key: "logs", icon: FileText },
  { href: "/settings", key: "settings", icon: Settings },
] as const;

export function TopNavbar({ onToggleSidebar }: { onToggleSidebar?: () => void }) {
  const { pathname } = useLocation();
  const { t } = useTranslation("navigation");
  return (
    <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-14 items-center px-4 gap-3">
        {onToggleSidebar && (
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9 lg:hidden"
            onClick={onToggleSidebar}
            title={t("toggleSidebar")}
            aria-label={t("toggleSidebar")}
          >
            <Menu className="h-5 w-5" />
          </Button>
        )}
        <NavLink to="/" className="flex items-center gap-2 flex-shrink-0" aria-label={t("brand")}>
          <span className="h-8 w-8 rounded-lg bg-primary text-primary-foreground flex items-center justify-center font-bold text-sm">{t("brandMark")}</span>
          <span className="text-base font-bold hidden sm:inline-block">{t("brand")}</span>
        </NavLink>
        <nav className="flex items-center gap-1 ml-2" aria-label={t("brand")}>
          {navItems.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <NavLink
                key={item.href}
                to={item.href}
                end={item.href === "/"}
                className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-sm font-medium transition-colors",
                  active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground hover:bg-muted",
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="hidden md:inline-block">{t(item.key)}</span>
              </NavLink>
            );
          })}
        </nav>
        <div className="ml-auto"><LocaleSwitcher /></div>
      </div>
    </header>
  );
}
