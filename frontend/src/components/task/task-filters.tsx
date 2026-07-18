"use client";

import { Filter, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useTaskStore } from "@/store/task-store";

const STATUS_OPTIONS = ["completed", "failed", "running", "pending"] as const;
const TYPE_OPTIONS = ["anime", "movie", "other"] as const;

export function TaskFilters() {
  const { t } = useTranslation("tasks");
  const { filters, setFilters, resetFilters, tasks } = useTaskStore();
  const statusCount = (status: string) => tasks.filter((task) => task.status === status).length;
  const typeCount = (type: string) => tasks.filter((task) => (
    type === "anime" ? task.is_anime === true : type === "movie" ? task.is_movie === true : task.is_anime !== true && task.is_movie !== true
  )).length;
  const toggle = (key: "status" | "type", value: string, checked: boolean) => {
    const current = filters[key];
    setFilters({ [key]: checked ? [...current, value] : current.filter((item) => item !== value) });
  };
  const hasFilter = filters.status.length > 0 || filters.type.length > 0;
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between pb-2 border-b">
        <h3 className="text-base font-semibold flex items-center gap-2"><Filter className="h-4 w-4" />{t("filters")}</h3>
        {hasFilter && <Button variant="ghost" size="sm" onClick={resetFilters}><X className="h-3 w-3" />{t("reset", { ns: "common" })}</Button>}
      </div>
      <div className="space-y-3">
        <Label className="text-sm font-medium">{t("status")}</Label>
        {STATUS_OPTIONS.map((status) => <div key={status} className="flex items-center gap-2">
          <Checkbox checked={filters.status.includes(status)} onCheckedChange={(value) => toggle("status", status, value === true)} />
          <span className="text-sm flex-1">{t(status)}</span><span className="text-xs text-muted-foreground">{statusCount(status)}</span>
        </div>)}
      </div>
      <Separator />
      <div className="space-y-3">
        <Label className="text-sm font-medium">{t("filterType")}</Label>
        {TYPE_OPTIONS.map((type) => <div key={type} className="flex items-center gap-2">
          <Checkbox checked={filters.type.includes(type)} onCheckedChange={(value) => toggle("type", type, value === true)} />
          <span className="text-sm flex-1">{t(type)}</span><span className="text-xs text-muted-foreground">{typeCount(type)}</span>
        </div>)}
      </div>
    </div>
  );
}
