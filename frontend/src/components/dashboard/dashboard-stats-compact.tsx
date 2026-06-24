"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, XCircle, Loader2, Clock, Activity } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { getDashboard, getDashboardStream } from "@/lib/api/client";
import { useLayoutStore } from "@/store/layout-store";
import type { DashboardStats } from "@/lib/api/types";

export function DashboardStatsCompact() {
  const { layoutVariant } = useLayoutStore();
  const minimal = layoutVariant === "minimal";
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    getDashboard()
      .then((s) => {
        if (active) {
          setStats(s);
          setLoading(false);
        }
      })
      .catch(() => active && setLoading(false));

    // SSE 实时更新
    let es: EventSource | null = null;
    try {
      es = getDashboardStream();
      es.onmessage = (ev) => {
        try {
          const parsed = JSON.parse(ev.data);
          const s = (parsed?.stats ?? parsed) as DashboardStats;
          if (active) setStats(s);
        } catch {
          /* ignore */
        }
      };
      es.onerror = () => {
        /* 断线静默，下次 fetch 兜底 */
      };
    } catch {
      /* SSE 不可用则仅靠定时拉取 */
    }
    return () => {
      active = false;
      es?.close();
    };
  }, []);

  const items = [
    {
      icon: Loader2,
      label: "处理中",
      value: stats?.running ?? 0,
      color: "text-blue-600 dark:text-blue-400",
    },
    {
      icon: Clock,
      label: "等待中",
      value: stats?.pending ?? 0,
      color: "text-amber-600 dark:text-amber-400",
    },
    {
      icon: CheckCircle2,
      label: "今日成功",
      value: stats?.today_success ?? 0,
      color: "text-green-600 dark:text-green-400",
    },
    {
      icon: XCircle,
      label: "今日失败",
      value: stats?.today_failed ?? 0,
      color: "text-red-600 dark:text-red-400",
    },
  ];

  if (loading) {
    return (
      <div
        className={
          minimal
            ? "grid grid-cols-4 gap-2"
            : "grid grid-cols-2 gap-2"
        }
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className={minimal ? "h-14 rounded-md" : "h-16 rounded-md"} />
        ))}
      </div>
    );
  }

  if (minimal) {
    // 极简：4 列居中，仅图标+数字，标题走 Tooltip
    return (
      <div className="grid grid-cols-4 gap-2">
        {items.map((it) => {
          const Icon = it.icon;
          return (
            <Tooltip key={it.label}>
              <TooltipTrigger asChild>
                <div className="border rounded-md p-2 flex flex-col items-center gap-1">
                  <Icon className={`h-4 w-4 ${it.color}`} />
                  <div className="text-lg font-bold leading-none">
                    {it.value}
                  </div>
                </div>
              </TooltipTrigger>
              <TooltipContent>{it.label}</TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    );
  }

  // 紧凑：2 列，图标+数字+标题
  return (
    <div className="grid grid-cols-2 gap-2">
      {items.map((it) => {
        const Icon = it.icon;
        return (
          <div
            key={it.label}
            className="border rounded-md p-3 flex items-center gap-2"
          >
            <Icon className={`h-5 w-5 flex-shrink-0 ${it.color}`} />
            <div className="min-w-0">
              <div className="text-lg font-semibold leading-none">
                {it.value}
              </div>
              <div className="text-xs text-muted-foreground mt-1 truncate">
                {it.label}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// 进度条 + 当前阶段徽章（任务页头部，显示批次整体进度）
export function ScanProgress({
  running,
  pending,
}: {
  running: number;
  pending: number;
}) {
  const total = running + pending;
  if (total === 0) return null;
  const pct = running > 0 ? 30 : 10;
  return (
    <div className="flex items-center gap-2 mb-4">
      <Activity className="h-4 w-4 text-primary animate-pulse" />
      <span className="text-sm text-muted-foreground">
        {running > 0 ? `正在处理 ${running} 个任务` : `${pending} 个任务排队中`}
      </span>
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden max-w-xs">
        <div
          className="h-full bg-primary transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
