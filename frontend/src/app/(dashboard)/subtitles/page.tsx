"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Upload, Trash2 } from "lucide-react";
import {
  Table,
  TableHeader,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/task/status-badge";
import {
  getSubtitleTasks,
  importSubtitle,
  deleteSubtitle,
} from "@/lib/api/client";
import type { SubtitleRow } from "@/lib/api/types";
import { toast } from "sonner";

export default function SubtitlesPage() {
  const [rows, setRows] = useState<SubtitleRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  const refresh = () => {
    getSubtitleTasks()
      .then(setRows)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await importSubtitle(file);
      toast.success("字幕导入完成");
      refresh();
    } catch (err) {
      toast.error("导入失败: " + (err as Error).message);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const handleDelete = async (uuid: string) => {
    try {
      await deleteSubtitle(uuid);
      toast.success("已删除字幕记录");
      setRows((r) => r.filter((x) => x.uuid !== uuid));
    } catch (err) {
      toast.error("删除失败: " + (err as Error).message);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">字幕导入</h1>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={refresh}>
            <RefreshCw className="h-4 w-4" />刷新
          </Button>
          <label>
            <input
              type="file"
              accept=".zip,.rar,.ass,.ssa,.srt,.sub,.vtt"
              className="hidden"
              onChange={handleUpload}
              disabled={uploading}
            />
            <Button size="sm" asChild disabled={uploading}>
              <span><Upload className="h-4 w-4" />{uploading ? "导入中…" : "上传字幕"}</span>
            </Button>
          </label>
        </div>
      </div>

      <div className="border rounded-md">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>压缩包</TableHead>
              <TableHead>匹配动漫</TableHead>
              <TableHead className="text-center">匹配数</TableHead>
              <TableHead className="text-center">对齐</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={6}>
                    <Skeleton className="h-8 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground py-12">
                  暂无字幕导入记录
                </TableCell>
              </TableRow>
            ) : (
              rows.map((r) => (
                <TableRow key={r.uuid}>
                  <TableCell className="font-mono text-xs">{r.archive}</TableCell>
                  <TableCell>{r.matched_task}</TableCell>
                  <TableCell className="text-center">{r.matched_count}</TableCell>
                  <TableCell className="text-center text-muted-foreground">{r.sync}</TableCell>
                  <TableCell><StatusBadge status={r.status} /></TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive h-8 w-8"
                      onClick={() => handleDelete(r.uuid)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
