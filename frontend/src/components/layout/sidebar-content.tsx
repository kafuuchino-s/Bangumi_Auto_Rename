"use client";

import { usePathname } from "next/navigation";
import { DashboardStatsCompact } from "@/components/dashboard/dashboard-stats-compact";
import { TaskFilters } from "@/components/task/task-filters";

// 侧栏内容：所有页统一放统计卡；任务页额外加筛选器。
// 路由 group (dashboard) 让此组件跨页常驻，usePathname 决定是否加筛选。
export function SidebarContent() {
  const pathname = usePathname();
  const isTaskPage = pathname === "/";

  return (
    <div className="space-y-6">
      <DashboardStatsCompact />
      {isTaskPage && (
        <div className="border-t pt-4">
          <TaskFilters />
        </div>
      )}
    </div>
  );
}
