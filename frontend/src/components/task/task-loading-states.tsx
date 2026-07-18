import { useTranslation } from "react-i18next";
import { Skeleton } from "@/components/ui/skeleton";

export function TaskTableSkeleton({ rows = 8 }: { rows?: number }) {
  return <div className="border rounded-md"><div className="border-b px-4 py-3"><Skeleton className="h-4 w-32" /></div>{Array.from({ length: rows }).map((_, index) => <div key={index} className="border-b px-4 py-3 flex items-center gap-3"><Skeleton className="h-4 w-8" /><Skeleton className="h-4 w-12" /><Skeleton className="h-4 flex-1 max-w-[300px]" /><Skeleton className="h-4 w-20" /><Skeleton className="h-4 w-12" /><Skeleton className="h-5 w-16 rounded-full" /><Skeleton className="h-4 w-16" /></div>)}</div>;
}

export function EmptyState({ onAction, actionLabel }: { onAction?: () => void; actionLabel?: string }) {
  const { t } = useTranslation("tasks");
  return <div className="border rounded-md py-16 text-center"><div className="text-muted-foreground mb-2">{t("empty")}</div><div className="text-xs text-muted-foreground mb-4">{t("filterSearch")}</div>{onAction && actionLabel && <button onClick={onAction} className="text-sm text-primary hover:underline">{actionLabel}</button>}</div>;
}
