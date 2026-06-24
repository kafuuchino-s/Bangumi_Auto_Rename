"use client";

import { Filter, X } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useTaskStore } from "@/store/task-store";

// 状态/类型选项（对齐后端 status 与 is_anime/is_movie 字段）
const STATUS_OPTIONS = ["成功", "失败", "处理中", "等待处理"];
const TYPE_OPTIONS = ["是", "否", "自动"];

export function TaskFilters() {
  const { filters, setFilters, resetFilters, tasks } = useTaskStore();

  // 聚合计数：每个状态/类型在当前任务里有多少
  const statusCount = (s: string) =>
    tasks.filter((t) => t.status === s).length;
  const typeCount = (tp: string) =>
    tasks.filter((t) => {
      const a = String(t.is_anime);
      const m = String(t.is_movie);
      return a === tp || m === tp;
    }).length;

  const toggleStatus = (s: string, checked: boolean) => {
    const cur = filters.status;
    setFilters({
      status: checked ? [...cur, s] : cur.filter((x) => x !== s),
    });
  };
  const toggleType = (tp: string, checked: boolean) => {
    const cur = filters.type;
    setFilters({
      type: checked ? [...cur, tp] : cur.filter((x) => x !== tp),
    });
  };

  const hasFilter =
    filters.status.length > 0 || filters.type.length > 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between pb-2 border-b">
        <h3 className="text-base font-semibold flex items-center gap-2">
          <Filter className="h-4 w-4" />筛选条件
        </h3>
        {hasFilter && (
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            <X className="h-3 w-3" />清除
          </Button>
        )}
      </div>

      <div className="space-y-3">
        <Label className="text-sm font-medium">状态</Label>
        {STATUS_OPTIONS.map((s) => (
          <div key={s} className="flex items-center gap-2">
            <Checkbox
              checked={filters.status.includes(s)}
              onCheckedChange={(v) => toggleStatus(s, v === true)}
            />
            <span className="text-sm flex-1">{s}</span>
            <span className="text-xs text-muted-foreground">
              {statusCount(s)}
            </span>
          </div>
        ))}
      </div>

      <Separator />

      <div className="space-y-3">
        <Label className="text-sm font-medium">类型（动漫/电影）</Label>
        {TYPE_OPTIONS.map((tp) => (
          <div key={tp} className="flex items-center gap-2">
            <Checkbox
              checked={filters.type.includes(tp)}
              onCheckedChange={(v) => toggleType(tp, v === true)}
            />
            <span className="text-sm flex-1">{tp}</span>
            <span className="text-xs text-muted-foreground">
              {typeCount(tp)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
