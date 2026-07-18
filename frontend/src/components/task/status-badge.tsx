import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

const statusClass: Record<string, string> = {
  completed: "bg-green-100 text-green-700 hover:bg-green-100 dark:bg-green-900/40 dark:text-green-300",
  failed: "bg-red-100 text-red-700 hover:bg-red-100 dark:bg-red-900/40 dark:text-red-300",
  running: "bg-blue-100 text-blue-700 hover:bg-blue-100 dark:bg-blue-900/40 dark:text-blue-300",
  pending: "bg-amber-100 text-amber-700 hover:bg-amber-100 dark:bg-amber-900/40 dark:text-amber-300",
};

export function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation("tasks");
  const key = status in statusClass ? status : "failed";
  return <Badge variant="secondary" className={cn("font-medium", statusClass[key])}>{t(key, { defaultValue: status })}</Badge>;
}
