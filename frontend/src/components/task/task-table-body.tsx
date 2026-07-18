"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { MoreHorizontal, RotateCw, Trash2, Eye, RefreshCw, Edit } from "lucide-react";
import { TableBody, TableCell, TableRow } from "@/components/ui/table";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { StatusBadge } from "./status-badge";
import { useTaskStore } from "@/store/task-store";
import { apiErrorMessage } from "@/lib/api/errors";
import { toast } from "sonner";
import type { TaskRow } from "@/lib/api/types";

export function TaskTableBody({ rows, selected, onToggle, onViewDetail, onEdit }: {
  rows: TaskRow[]; selected: Set<string>; onToggle: (uuid: string) => void;
  onViewDetail: (uuid: string) => void; onEdit: (uuid: string) => void;
}) {
  const { t } = useTranslation("tasks");
  const { retry, remove, refetchSub } = useTaskStore();
  const [deleteUuid, setDeleteUuid] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const run = async (action: () => Promise<void>, success: string) => {
    try { await action(); toast.success(success); }
    catch (error) { toast.error(apiErrorMessage(error)); }
  };
  const confirmDelete = async () => {
    if (!deleteUuid) return;
    setDeleting(true);
    try { await remove(deleteUuid); toast.success(t("taskDeleted")); setDeleteUuid(null); }
    catch (error) { toast.error(apiErrorMessage(error)); }
    finally { setDeleting(false); }
  };
  const dialog = <ConfirmationDialog open={deleteUuid !== null} onOpenChange={(open) => !open && setDeleteUuid(null)} title={t("deleteConfirmTitle")} description={t("deleteConfirmDescription")} confirmLabel={t("delete", { ns: "common" })} confirmVariant="destructive" onConfirm={confirmDelete} loading={deleting} />;
  if (rows.length === 0) return <><TableBody><TableRow><TableCell colSpan={8} className="text-center text-muted-foreground py-12">{t("empty")}</TableCell></TableRow></TableBody>{dialog}</>;
  return <><TableBody>{rows.map((task) => <TableRow key={task.uuid} className="cursor-pointer hover:bg-muted/30" onDoubleClick={() => onViewDetail(task.uuid)}>
    <TableCell className="w-10"><Checkbox checked={selected.has(task.uuid)} onCheckedChange={() => onToggle(task.uuid)} onClick={(event) => event.stopPropagation()} /></TableCell>
    <TableCell className="font-mono text-xs text-muted-foreground">{task.id}</TableCell>
    <TableCell className="max-w-[300px] truncate" title={task.path}>{task.path}</TableCell>
    <TableCell>{task.name || t("unknown", { ns: "common" })}</TableCell>
    <TableCell className="text-center">{task.season ?? "-"}</TableCell>
    <TableCell><StatusBadge status={task.status} /></TableCell>
    <TableCell className="text-center text-muted-foreground text-sm">{task.queue_position ?? "-"}</TableCell>
    <TableCell className="text-center"><div className="flex items-center gap-1 justify-end">
      <Button variant="outline" size="sm" onClick={() => onViewDetail(task.uuid)}><Eye className="h-3.5 w-3.5" />{t("detail", { ns: "common" })}</Button>
      <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-8 w-8" aria-label={t("actions")}><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger><DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => void run(() => retry(task.uuid), t("taskEnqueued"))}><RotateCw className="h-4 w-4" />{t("retry", { ns: "common" })}</DropdownMenuItem>
        <DropdownMenuItem onClick={() => void run(() => refetchSub(task.uuid), t("taskEnqueued"))}><RefreshCw className="h-4 w-4" />{t("subtitles", { ns: "navigation" })}</DropdownMenuItem>
        <DropdownMenuItem onClick={() => onEdit(task.uuid)}><Edit className="h-4 w-4" />{t("edit", { ns: "common" })}</DropdownMenuItem>
        <DropdownMenuItem className="text-destructive" onClick={() => setDeleteUuid(task.uuid)}><Trash2 className="h-4 w-4" />{t("delete", { ns: "common" })}</DropdownMenuItem>
      </DropdownMenuContent></DropdownMenu>
    </div></TableCell>
  </TableRow>)}</TableBody>{dialog}</>;
}
