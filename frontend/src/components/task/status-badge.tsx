import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

// 状态 → {label, className} 配置表（浅底深字降噪，对齐后端 status 字段）
const statusConfig: Record<
  string,
  { label: string; className: string }
> = {
  成功: {
    label: "成功",
    className:
      "bg-green-100 text-green-700 hover:bg-green-100 dark:bg-green-900/40 dark:text-green-300",
  },
  失败: {
    label: "失败",
    className:
      "bg-red-100 text-red-700 hover:bg-red-100 dark:bg-red-900/40 dark:text-red-300",
  },
  处理中: {
    label: "处理中",
    className:
      "bg-blue-100 text-blue-700 hover:bg-blue-100 dark:bg-blue-900/40 dark:text-blue-300",
  },
  等待处理: {
    label: "等待中",
    className:
      "bg-amber-100 text-amber-700 hover:bg-amber-100 dark:bg-amber-900/40 dark:text-amber-300",
  },
};

export function StatusBadge({ status }: { status: string }) {
  const config = statusConfig[status] ?? {
    label: status,
    className:
      "bg-red-100 text-red-700 hover:bg-red-100 dark:bg-red-900/40 dark:text-red-300",
  };
  return (
    <Badge variant="secondary" className={cn("font-medium", config.className)}>
      {config.label}
    </Badge>
  );
}
