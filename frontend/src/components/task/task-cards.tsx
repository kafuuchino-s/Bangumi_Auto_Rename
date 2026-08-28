"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Film, Tv, MoreHorizontal, RotateCw, Pencil, RefreshCw, Trash2 } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { StatusBadge } from "./status-badge";
import { getTaskMediaType } from "@/lib/task-media-type";
import type { TaskRow } from "@/lib/api/types";

export function TaskCards({ tasks, selected, onToggle, onViewDetail, onRetry, onEdit, onRefetch, onRemove }: {
  tasks: TaskRow[]; selected: Set<string>; onToggle: (uuid: string) => void; onViewDetail: (uuid: string) => void;
  onRetry: (uuid: string) => void; onEdit: (uuid: string) => void; onRefetch: (uuid: string) => void; onRemove: (uuid: string) => void;
}) {
  const { t } = useTranslation("tasks");
  const [deleteUuid, setDeleteUuid] = useState<string | null>(null);
  return <><div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">{tasks.map((task) => {
    const type = getTaskMediaType(task);
    const Icon = task.is_movie === true ? Film : Tv;
    return <div key={task.uuid} className="border rounded-lg p-3 bg-card hover:shadow-sm transition-shadow cursor-pointer" onClick={() => onViewDetail(task.uuid)}>
      <div className="flex items-start gap-2 mb-2"><Checkbox checked={selected.has(task.uuid)} onCheckedChange={() => onToggle(task.uuid)} onClick={(event) => event.stopPropagation()} /><div className="flex-1 min-w-0"><div className="flex items-center gap-1.5 flex-wrap"><Icon className="h-3.5 w-3.5 text-muted-foreground" /><span className="text-xs text-muted-foreground">{t(type)}</span><StatusBadge status={task.status} /></div><div className="mt-1 font-medium text-sm truncate" title={task.name || task.path}>{task.name || task.path}</div></div>
        <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7 flex-shrink-0" onClick={(event) => event.stopPropagation()} aria-label={t("actions")}><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger><DropdownMenuContent align="end">
          <DropdownMenuItem onClick={(event) => { event.stopPropagation(); onRetry(task.uuid); }}><RotateCw className="h-3.5 w-3.5" />{t("retry", { ns: "common" })}</DropdownMenuItem>
          <DropdownMenuItem onClick={(event) => { event.stopPropagation(); onRefetch(task.uuid); }}><RefreshCw className="h-3.5 w-3.5" />{t("subtitles", { ns: "navigation" })}</DropdownMenuItem>
          <DropdownMenuItem onClick={(event) => { event.stopPropagation(); onEdit(task.uuid); }}><Pencil className="h-3.5 w-3.5" />{t("edit", { ns: "common" })}</DropdownMenuItem>
          <DropdownMenuItem className="text-destructive" onClick={(event) => { event.stopPropagation(); setDeleteUuid(task.uuid); }}><Trash2 className="h-3.5 w-3.5" />{t("delete", { ns: "common" })}</DropdownMenuItem>
        </DropdownMenuContent></DropdownMenu>
      </div><div className="text-xs text-muted-foreground truncate" title={task.path}>{task.path}</div>{task.season != null && <div className="text-xs text-muted-foreground mt-1">{t("season")}: {task.season}</div>}
    </div>;
  })}</div><ConfirmationDialog open={deleteUuid !== null} onOpenChange={(open) => !open && setDeleteUuid(null)} title={t("deleteConfirmTitle")} description={t("deleteConfirmDescription")} confirmLabel={t("delete", { ns: "common" })} confirmVariant="destructive" onConfirm={() => { if (deleteUuid) onRemove(deleteUuid); setDeleteUuid(null); }} /></>;
}
