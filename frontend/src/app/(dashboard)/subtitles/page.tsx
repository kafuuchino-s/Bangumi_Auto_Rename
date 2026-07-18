"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, Upload, Trash2 } from "lucide-react";
import { Table, TableHeader, TableHead, TableBody, TableRow, TableCell } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/task/status-badge";
import { getSubtitleTasks, importSubtitle, deleteSubtitle } from "@/lib/api/client";
import type { SubtitleRow } from "@/lib/api/types";
import { toast } from "sonner";
import { apiErrorMessage } from "@/lib/api/errors";

export default function SubtitlesPage() {
  const { t } = useTranslation("subtitles");
  const [rows, setRows] = useState<SubtitleRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const refresh = () => { setLoading(true); void getSubtitleTasks().then(setRows).finally(() => setLoading(false)); };
  useEffect(() => refresh(), []);
  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try { await importSubtitle(file); toast.success(t("imported")); refresh(); }
    catch (error) { toast.error(apiErrorMessage(error)); }
    finally { setUploading(false); event.target.value = ""; }
  };
  const handleDelete = async (uuid: string) => {
    try { await deleteSubtitle(uuid); toast.success(t("deleted")); setRows((items) => items.filter((item) => item.uuid !== uuid)); }
    catch (error) { toast.error(apiErrorMessage(error)); }
  };
  return <div className="space-y-4">
    <div className="flex items-center justify-between"><h1 className="text-xl font-bold">{t("title")}</h1><div className="flex gap-2">
      <Button variant="outline" size="sm" onClick={refresh}><RefreshCw className="h-4 w-4" />{t("refresh", { ns: "common" })}</Button>
      <label><input type="file" accept=".zip,.rar,.ass,.ssa,.srt,.sub,.vtt" className="hidden" onChange={handleUpload} disabled={uploading} /><Button size="sm" asChild disabled={uploading}><span><Upload className="h-4 w-4" />{uploading ? t("uploading") : t("upload")}</span></Button></label>
    </div></div>
    <div className="border rounded-md"><Table><TableHeader><TableRow><TableHead>{t("archive")}</TableHead><TableHead>{t("matchedTask")}</TableHead><TableHead className="text-center">{t("matchedCount")}</TableHead><TableHead className="text-center">{t("sync")}</TableHead><TableHead>{t("status")}</TableHead><TableHead className="text-right">{t("actions", { ns: "tasks" })}</TableHead></TableRow></TableHeader><TableBody>
      {loading ? Array.from({ length: 4 }).map((_, index) => <TableRow key={index}><TableCell colSpan={6}><Skeleton className="h-8 w-full" /></TableCell></TableRow>) : rows.length === 0 ? <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-12">{t("empty")}</TableCell></TableRow> : rows.map((row) => <TableRow key={row.uuid}><TableCell className="font-mono text-xs">{row.archive}</TableCell><TableCell>{row.matched_task ?? "-"}</TableCell><TableCell className="text-center">{row.matched_count}/{row.total_count}</TableCell><TableCell className="text-center text-muted-foreground">{row.sync.enabled ? `${row.sync.success}/${row.sync.attempted}` : "-"}</TableCell><TableCell><StatusBadge status={row.status} /></TableCell><TableCell className="text-right"><Button variant="ghost" size="icon" className="text-destructive h-8 w-8" onClick={() => void handleDelete(row.uuid)} aria-label={t("delete", { ns: "common" })}><Trash2 className="h-4 w-4" /></Button></TableCell></TableRow>)}
    </TableBody></Table></div>
  </div>;
}
