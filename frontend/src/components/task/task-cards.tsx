"use client";

import { useState } from "react";
import { Film, Tv, MoreHorizontal, RotateCw, Pencil, RefreshCw, Trash2 } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { StatusBadge } from "./status-badge";
import type { TaskRow } from "@/lib/api/types";

export function TaskCards({
  tasks,
  selected,
  onToggle,
  onViewDetail,
  onRetry,
  onEdit,
  onRefetch,
  onRemove,
}: {
  tasks: TaskRow[];
  selected: Set<string>;
  onToggle: (uuid: string) => void;
  onViewDetail: (uuid: string) => void;
  onRetry: (uuid: string) => void;
  onEdit: (uuid: string) => void;
  onRefetch: (uuid: string) => void;
  onRemove: (uuid: string) => void;
}) {
  const [deleteUuid, setDeleteUuid] = useState<string | null>(null);

  return (
    <>
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {tasks.map((t) => {
        const isMovie = t.is_movie === true || t.is_movie === "是";
        const isAnime = t.is_anime === true || t.is_anime === "是";
        return (
          <div
            key={t.uuid}
            className="border rounded-lg p-3 bg-card hover:shadow-sm transition-shadow cursor-pointer"
            onClick={() => onViewDetail(t.uuid)}
          >
            <div className="flex items-start gap-2 mb-2">
              <Checkbox
                checked={selected.has(t.uuid)}
                onCheckedChange={() => onToggle(t.uuid)}
                onClick={(e) => e.stopPropagation()}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 flex-wrap">
                  {isMovie ? (
                    <Film className="h-3.5 w-3.5 text-muted-foreground" />
                  ) : (
                    <Tv className="h-3.5 w-3.5 text-muted-foreground" />
                  )}
                  <span className="text-xs text-muted-foreground">
                    {isMovie ? "电影" : isAnime ? "动漫" : "剧集"}
                  </span>
                  <StatusBadge status={t.status} />
                </div>
                <div className="mt-1 font-medium text-sm truncate" title={t.name || t.path}>
                  {t.name || t.path}
                </div>
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 flex-shrink-0"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onRetry(t.uuid); }}>
                    <RotateCw className="h-3.5 w-3.5" />重试
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onRefetch(t.uuid); }}>
                    <RefreshCw className="h-3.5 w-3.5" />重新抓字幕
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onEdit(t.uuid); }}>
                    <Pencil className="h-3.5 w-3.5" />编辑
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="text-destructive"
                    onClick={(e) => { e.stopPropagation(); setDeleteUuid(t.uuid); }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />删除
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <div className="text-xs text-muted-foreground truncate" title={t.path}>
              {t.path}
            </div>
            {t.season != null && (
              <div className="text-xs text-muted-foreground mt-1">
                季 {t.season}
              </div>
            )}
          </div>
        );
      })}
    </div>
    <ConfirmationDialog
      open={deleteUuid !== null}
      onOpenChange={(v) => !v && setDeleteUuid(null)}
      title="确认删除任务"
      description="确认删除此任务记录？此操作不可撤销。"
      confirmLabel="删除"
      confirmVariant="destructive"
      onConfirm={() => {
        if (deleteUuid) onRemove(deleteUuid);
        setDeleteUuid(null);
      }}
    />
    </>
  );
}
