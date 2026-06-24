"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  Table,
  TableHeader,
  TableHead,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { RefreshCw, Plus, AlertCircle } from "lucide-react";
import { useTaskStore } from "@/store/task-store";
import { TasksToolbar } from "@/components/task/tasks-toolbar";
import { TaskTableBody } from "@/components/task/task-table-body";
import { TaskCards } from "@/components/task/task-cards";
import { Pagination } from "@/components/task/pagination";
import { TaskDetailDialog } from "@/components/task/task-detail-dialog";
import { CreateTaskWizard } from "@/components/task/create-task-wizard";
import {
  TaskTableSkeleton,
  EmptyState,
} from "@/components/task/task-loading-states";
import { getTasksStream } from "@/lib/api/client";
import type { TaskRow } from "@/lib/api/types";

export default function TaskListPage() {
  return (
    <Suspense
      fallback={<div className="text-muted-foreground">加载中…</div>}
    >
      <TaskListContent />
    </Suspense>
  );
}

function TaskListContent() {
  const {
    tasks,
    loading,
    error,
    filters,
    fetchTasks,
    viewMode,
    selected,
    toggleSelected,
    retry,
    remove,
    refetchSub,
  } = useTaskStore();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [showWizard, setShowWizard] = useState(false);
  const [detailUuid, setDetailUuid] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // ?detail=uuid 同步（可分享 URL）
  useEffect(() => {
    setDetailUuid(searchParams.get("detail"));
  }, [searchParams]);

  const onViewDetail = (uuid: string) => {
    router.push(`/?detail=${uuid}`);
  };
  const onEdit = (uuid: string) => {
    // 编辑复用详情对话框（详情内可重试/删除；字段级编辑待后续增强）
    router.push(`/?detail=${uuid}`);
  };

  // 初次拉取 + 轮询兜底
  useEffect(() => {
    fetchTasks();
    const t = setInterval(fetchTasks, 8000);
    return () => clearInterval(t);
  }, [fetchTasks]);

  // SSE 实时推送（有连接时由浏览器维持，失败静默回退到轮询）
  useEffect(() => {
    let es: EventSource | null = null;
    try {
      es = getTasksStream();
      es.onmessage = (ev) => {
        try {
          const parsed = JSON.parse(ev.data);
          const rows = (parsed?.tasks ?? []) as TaskRow[];
          useTaskStore.setState({ tasks: rows, loading: false, error: null });
        } catch {
          /* ignore */
        }
      };
      es.onerror = () => {
        /* 静默，轮询兜底 */
      };
    } catch {
      /* SSE 不可用 */
    }
    return () => es?.close();
  }, []);

  // 筛选：search + status + type（统计+筛选器在全局左栏）
  const filtered = useMemo(() => {
    const q = filters.search.trim().toLowerCase();
    return tasks.filter((t) => {
      if (q) {
        const hit = [t.path, t.name, t.uuid, t.status].some((s) =>
          String(s).toLowerCase().includes(q)
        );
        if (!hit) return false;
      }
      if (filters.status.length > 0 && !filters.status.includes(t.status))
        return false;
      if (filters.type.length > 0) {
        const a = String(t.is_anime);
        const m = String(t.is_movie);
        if (!filters.type.includes(a) && !filters.type.includes(m))
          return false;
      }
      return true;
    });
  }, [tasks, filters]);

  // 筛选变化重置页码
  useEffect(() => {
    setPage(1);
  }, [filters]);

  const total = filtered.length;
  const totalPages = Math.ceil(total / pageSize) || 1;
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  const allPageSelected =
    paged.length > 0 && paged.every((t) => selected.has(t.uuid));
  const somePageSelected = paged.some((t) => selected.has(t.uuid));

  const toggleSelectAll = () => {
    if (allPageSelected) {
      const next = new Set(selected);
      paged.forEach((t) => next.delete(t.uuid));
      useTaskStore.setState({ selected: next });
    } else {
      const next = new Set(selected);
      paged.forEach((t) => next.add(t.uuid));
      useTaskStore.setState({ selected: next });
    }
  };

  // 卡片视图操作回调
  const handleRetry = async (uuid: string) => {
    try {
      await retry(uuid);
    } catch {
      /* toast 在 store 外层已处理 */
    }
  };
  const handleRemove = async (uuid: string) => {
    try {
      await remove(uuid);
    } catch {
      /* ignore */
    }
  };
  const handleRefetch = async (uuid: string) => {
    try {
      await refetchSub(uuid);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="space-y-4">
      {/* 标题行 + 刷新/添加（统计+筛选在全局左栏） */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">任务列表</h1>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={fetchTasks}>
            <RefreshCw className="h-4 w-4" />
            刷新
          </Button>
          <Button size="sm" onClick={() => setShowWizard(true)}>
            <Plus className="h-4 w-4" />
            添加任务
          </Button>
        </div>
      </div>

      <TasksToolbar pageSize={pageSize} setPageSize={setPageSize} />

      {error && (
        <div className="flex items-center gap-2 text-destructive text-sm border border-destructive/30 bg-destructive/5 rounded-md p-2">
          <AlertCircle className="h-4 w-4" />
          加载失败: {error}
        </div>
      )}

      {loading && tasks.length === 0 ? (
        <TaskTableSkeleton />
      ) : total === 0 ? (
        <EmptyState
          onAction={() => setShowWizard(true)}
          actionLabel="添加第一个任务"
        />
      ) : viewMode === "table" ? (
        <div className="border rounded-md">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={
                      allPageSelected
                        ? true
                        : somePageSelected
                        ? "indeterminate"
                        : false
                    }
                    onCheckedChange={toggleSelectAll}
                  />
                </TableHead>
                <TableHead className="w-12">ID</TableHead>
                <TableHead>传入路径</TableHead>
                <TableHead>识别剧集</TableHead>
                <TableHead className="text-center">季度</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="text-center">队列状态</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TaskTableBody
              rows={paged}
              selected={selected}
              onToggle={toggleSelected}
              onViewDetail={onViewDetail}
              onEdit={onEdit}
            />
          </Table>
        </div>
      ) : (
        <TaskCards
          tasks={paged}
          selected={selected}
          onToggle={toggleSelected}
          onViewDetail={onViewDetail}
          onRetry={handleRetry}
          onEdit={onEdit}
          onRefetch={handleRefetch}
          onRemove={handleRemove}
        />
      )}

      <Pagination
        page={page}
        totalPages={totalPages}
        total={total}
        pageSize={pageSize}
        onPageChange={setPage}
      />

      <CreateTaskWizard
        open={showWizard}
        onOpenChange={setShowWizard}
        onCreated={fetchTasks}
      />

      <TaskDetailDialog
        uuid={detailUuid}
        open={detailUuid !== null}
        onOpenChange={(v) => {
          if (!v) {
            setDetailUuid(null);
            router.push("/");
          }
        }}
      />
    </div>
  );
}
