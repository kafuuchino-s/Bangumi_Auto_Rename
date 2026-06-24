"use client";

import { useState } from "react";
import { LayoutList, LayoutGrid, Search, X, RotateCw, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useTaskStore } from "@/store/task-store";
import { toast } from "sonner";

export function TasksToolbar({
  pageSize,
  setPageSize,
}: {
  pageSize: number;
  setPageSize: (n: number) => void;
}) {
  const {
    filters,
    setFilters,
    resetFilters,
    viewMode,
    setViewMode,
    selected,
    clearSelected,
    batchRetry,
    batchRemove,
  } = useTaskStore();

  const selectedCount = selected.size;
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [batchLoading, setBatchLoading] = useState(false);

  const handleBatchRetry = async () => {
    const uuids = Array.from(selected);
    setBatchLoading(true);
    try {
      await batchRetry(uuids);
      toast.success(`已重试 ${uuids.length} 个任务`);
    } catch (e) {
      toast.error("批量重试失败: " + (e as Error).message);
    } finally {
      setBatchLoading(false);
    }
  };

  const handleBatchDelete = async () => {
    const uuids = Array.from(selected);
    setBatchLoading(true);
    try {
      await batchRemove(uuids);
      toast.success(`已删除 ${uuids.length} 个任务`);
      setConfirmOpen(false);
    } catch (e) {
      toast.error("批量删除失败: " + (e as Error).message);
    } finally {
      setBatchLoading(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 mb-3">
      {/* 搜索 */}
      <div className="relative flex-1 min-w-[200px] max-w-sm">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="搜索路径/名称/状态…"
          value={filters.search}
          onChange={(e) => setFilters({ search: e.target.value })}
          className="pl-8"
        />
        {filters.search && (
          <button
            onClick={() => setFilters({ search: "" })}
            className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <Separator orientation="vertical" className="h-8" />

      {/* 视图切换 */}
      <div className="flex items-center gap-1 border rounded-md p-0.5">
        <Button
          variant={viewMode === "table" ? "secondary" : "ghost"}
          size="icon"
          className="h-7 w-7"
          onClick={() => setViewMode("table")}
          title="表格视图"
        >
          <LayoutList className="h-4 w-4" />
        </Button>
        <Button
          variant={viewMode === "card" ? "secondary" : "ghost"}
          size="icon"
          className="h-7 w-7"
          onClick={() => setViewMode("card")}
          title="卡片视图"
        >
          <LayoutGrid className="h-4 w-4" />
        </Button>
      </div>

      {/* 每页条数 */}
      <select
        value={pageSize}
        onChange={(e) => setPageSize(Number(e.target.value))}
        className="h-9 border rounded-md px-2 text-sm bg-background"
        title="每页条数"
      >
        {[10, 20, 50, 100].map((n) => (
          <option key={n} value={n}>{n} / 页</option>
        ))}
      </select>

      {/* 批量操作（选中时显示） */}
      {selectedCount > 0 && (
        <div className="flex items-center gap-2 ml-auto">
          <span className="text-sm text-muted-foreground">
            已选 {selectedCount} 项
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={handleBatchRetry}
            disabled={batchLoading}
          >
            <RotateCw className="h-3.5 w-3.5" />批量重试
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setConfirmOpen(true)}
            disabled={batchLoading}
          >
            <Trash2 className="h-3.5 w-3.5" />批量删除
          </Button>
          <Button variant="ghost" size="sm" onClick={clearSelected}>
            取消选择
          </Button>
        </div>
      )}

      <ConfirmationDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="确认批量删除"
        description={`确认删除选中的 ${selectedCount} 个任务记录？\n此操作不可撤销。`}
        confirmLabel="删除"
        confirmVariant="destructive"
        onConfirm={handleBatchDelete}
        loading={batchLoading}
      />
    </div>
  );
}
