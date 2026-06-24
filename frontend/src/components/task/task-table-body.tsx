"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { MoreHorizontal, RotateCw, Trash2, Eye, RefreshCw, Edit } from "lucide-react";
import {
  TableBody,
  TableCell,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { StatusBadge } from "./status-badge";
import { useTaskStore } from "@/store/task-store";
import { toast } from "sonner";
import type { TaskRow } from "@/lib/api/types";

export function TaskTableBody({
  rows,
  selected,
  onToggle,
  onViewDetail,
  onEdit,
}: {
  rows: TaskRow[];
  selected: Set<string>;
  onToggle: (uuid: string) => void;
  onViewDetail: (uuid: string) => void;
  onEdit: (uuid: string) => void;
}) {
  const router = useRouter();
  const { retry, remove, refetchSub } = useTaskStore();
  const [deleteUuid, setDeleteUuid] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const handleRetry = async (uuid: string) => {
    try {
      await retry(uuid);
      toast.success("任务已重新入队");
    } catch (e) {
      toast.error("重试失败: " + (e as Error).message);
    }
  };

  const confirmDelete = async () => {
    if (!deleteUuid) return;
    setDeleting(true);
    try {
      await remove(deleteUuid);
      toast.success("已删除任务记录");
      setDeleteUuid(null);
    } catch (e) {
      toast.error("删除失败: " + (e as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  const handleRefetch = async (uuid: string) => {
    try {
      await refetchSub(uuid);
      toast.success("字幕抓取已触发");
    } catch (e) {
      toast.error("重跑字幕失败: " + (e as Error).message);
    }
  };

  if (rows.length === 0) {
    return (
      <>
        <TableBody>
          <TableRow>
            <TableCell colSpan={8} className="text-center text-muted-foreground py-12">
              暂无任务记录
            </TableCell>
          </TableRow>
        </TableBody>
        <ConfirmationDialog
          open={deleteUuid !== null}
          onOpenChange={(v) => !v && setDeleteUuid(null)}
          title="确认删除任务"
          description="确认删除此任务记录？此操作不可撤销。"
          confirmLabel="删除"
          confirmVariant="destructive"
          onConfirm={confirmDelete}
          loading={deleting}
        />
      </>
    );
  }

  return (
    <>
      <TableBody>
        {rows.map((task) => (
        <TableRow
          key={task.uuid}
          className="cursor-pointer hover:bg-muted/30"
          onDoubleClick={() => onViewDetail(task.uuid)}
        >
          <TableCell className="w-10">
            <Checkbox
              checked={selected.has(task.uuid)}
              onCheckedChange={() => onToggle(task.uuid)}
              onClick={(e) => e.stopPropagation()}
            />
          </TableCell>
          <TableCell className="font-mono text-xs text-muted-foreground">
            {task.id}
          </TableCell>
          <TableCell className="max-w-[300px] truncate" title={task.path}>
            {task.path}
          </TableCell>
          <TableCell>{task.name || "未知"}</TableCell>
          <TableCell className="text-center">{task.season ?? "-"}</TableCell>
          <TableCell>
            <StatusBadge status={task.status} />
          </TableCell>
          <TableCell className="text-center text-muted-foreground text-sm">
            {task.queue_status}
          </TableCell>
          <TableCell className="text-center">
            <div className="flex items-center gap-1 justify-end">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onViewDetail(task.uuid)}
              >
                <Eye className="h-3.5 w-3.5" />
                详情
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-8 w-8">
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => handleRetry(task.uuid)}>
                    <RotateCw className="h-4 w-4" />
                    重试
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleRefetch(task.uuid)}>
                    <RefreshCw className="h-4 w-4" />
                    重跑字幕
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => onEdit(task.uuid)}>
                    <Edit className="h-4 w-4" />
                    编辑
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="text-destructive"
                    onClick={() => setDeleteUuid(task.uuid)}
                  >
                    <Trash2 className="h-4 w-4" />
                    删除
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </TableCell>
        </TableRow>
      ))}
      </TableBody>
      <ConfirmationDialog
        open={deleteUuid !== null}
        onOpenChange={(v) => !v && setDeleteUuid(null)}
        title="确认删除任务"
        description="确认删除此任务记录？此操作不可撤销。"
        confirmLabel="删除"
        confirmVariant="destructive"
        onConfirm={confirmDelete}
        loading={deleting}
      />
    </>
  );
}
