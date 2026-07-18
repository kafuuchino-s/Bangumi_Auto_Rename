"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Plus, RefreshCw } from "lucide-react";
import { Table, TableHeader, TableHead, TableRow } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useTaskStore } from "@/store/task-store";
import { TasksToolbar } from "@/components/task/tasks-toolbar";
import { TaskTableBody } from "@/components/task/task-table-body";
import { TaskCards } from "@/components/task/task-cards";
import { Pagination } from "@/components/task/pagination";
import { TaskDetailDialog } from "@/components/task/task-detail-dialog";
import { CreateTaskWizard } from "@/components/task/create-task-wizard";
import { TaskTableSkeleton, EmptyState } from "@/components/task/task-loading-states";
import { getTasksStream } from "@/lib/api/client";
import type { TaskRow } from "@/lib/api/types";

export default function TaskListPage() {
  const { t } = useTranslation("common");
  return <Suspense fallback={<div className="text-muted-foreground">{t("loading")}</div>}><TaskListContent /></Suspense>;
}

function TaskListContent() {
  const { t } = useTranslation("tasks");
  const { tasks, loading, error, filters, fetchTasks, viewMode, selected, toggleSelected, retry, remove, refetchSub } = useTaskStore();
  const [showWizard, setShowWizard] = useState(false);
  const [detailUuid, setDetailUuid] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  useEffect(() => {
    void fetchTasks();
    const timer = window.setInterval(() => void fetchTasks(), 8000);
    return () => window.clearInterval(timer);
  }, [fetchTasks]);

  useEffect(() => {
    let stream: EventSource | null = null;
    try {
      stream = getTasksStream();
      stream.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          const rows = (payload?.data?.tasks ?? payload?.tasks ?? []) as TaskRow[];
          useTaskStore.setState({ tasks: rows, loading: false, error: null });
        } catch { /* polling remains the fallback */ }
      };
    } catch { /* EventSource is optional */ }
    return () => stream?.close();
  }, []);

  const filtered = useMemo(() => tasks.filter((task) => {
    const query = filters.search.trim().toLowerCase();
    if (query && ![task.path, task.name, task.uuid, task.status].some((value) => String(value ?? "").toLowerCase().includes(query))) return false;
    if (filters.status.length && !filters.status.includes(task.status)) return false;
    if (filters.type.length) {
      const type = task.is_anime === true ? "anime" : task.is_movie === true ? "movie" : "other";
      if (!filters.type.includes(type)) return false;
    }
    return true;
  }), [tasks, filters]);

  useEffect(() => setPage(1), [filters]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);
  const allSelected = paged.length > 0 && paged.every((task) => selected.has(task.uuid));
  const someSelected = paged.some((task) => selected.has(task.uuid));
  const toggleSelectAll = () => {
    const next = new Set(selected);
    if (allSelected) paged.forEach((task) => next.delete(task.uuid));
    else paged.forEach((task) => next.add(task.uuid));
    useTaskStore.setState({ selected: next });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">{t("title")}</h1>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void fetchTasks()}><RefreshCw className="h-4 w-4" />{t("refresh", { ns: "common" })}</Button>
          <Button size="sm" onClick={() => setShowWizard(true)}><Plus className="h-4 w-4" />{t("add")}</Button>
        </div>
      </div>
      <TasksToolbar pageSize={pageSize} setPageSize={setPageSize} />
      {error && <div className="flex items-center gap-2 text-destructive text-sm border border-destructive/30 bg-destructive/5 rounded-md p-2"><AlertCircle className="h-4 w-4" />{t("error", { ns: "common" })}: {error}</div>}
      {loading && tasks.length === 0 ? <TaskTableSkeleton /> : filtered.length === 0 ? <EmptyState onAction={() => setShowWizard(true)} actionLabel={t("addFirst")} /> : viewMode === "table" ? (
        <div className="border rounded-md"><Table><TableHeader><TableRow>
          <TableHead className="w-10"><Checkbox checked={allSelected ? true : someSelected ? "indeterminate" : false} onCheckedChange={toggleSelectAll} /></TableHead>
          <TableHead className="w-12">ID</TableHead><TableHead>{t("path")}</TableHead><TableHead>{t("recognizedTitle")}</TableHead>
          <TableHead className="text-center">{t("season")}</TableHead><TableHead>{t("status")}</TableHead><TableHead className="text-center">{t("queuePosition")}</TableHead><TableHead className="text-right">{t("actions")}</TableHead>
        </TableRow></TableHeader><TaskTableBody rows={paged} selected={selected} onToggle={toggleSelected} onViewDetail={setDetailUuid} onEdit={setDetailUuid} /></Table></div>
      ) : <TaskCards tasks={paged} selected={selected} onToggle={toggleSelected} onViewDetail={setDetailUuid} onRetry={(uuid) => void retry(uuid)} onEdit={setDetailUuid} onRefetch={(uuid) => void refetchSub(uuid)} onRemove={(uuid) => void remove(uuid)} />}
      <Pagination page={page} totalPages={totalPages} total={filtered.length} pageSize={pageSize} onPageChange={setPage} />
      <CreateTaskWizard open={showWizard} onOpenChange={setShowWizard} onCreated={() => void fetchTasks()} />
      <TaskDetailDialog uuid={detailUuid} open={detailUuid !== null} onOpenChange={(open) => !open && setDetailUuid(null)} />
    </div>
  );
}
