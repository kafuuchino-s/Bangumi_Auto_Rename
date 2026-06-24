"use client";

import { LayoutToggleButton } from "./layout-toggle-button";
import { ThemeToggle } from "@/components/theme-toggle";

// 侧栏底部：布局切换 + 主题切换
export function SidebarBottomControls() {
  return (
    <div className="flex items-center gap-2">
      <LayoutToggleButton />
      <ThemeToggle />
    </div>
  );
}
