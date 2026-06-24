"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { RotateCw, Trash2 } from "lucide-react";
import { getTaskDetail, retryTask, deleteTask } from "@/lib/api/client";
import type { TaskDetail } from "@/lib/api/types";
import { toast } from "sonner";
import { useTaskStore } from "@/store/task-store";

export function TaskDetailDialog({
  uuid,
  open,
  onOpenChange,
}: {
  uuid: string | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const { fetchTasks } = useTaskStore();

  useEffect(() => {
    if (!open || !uuid) return;
    setLoading(true);
    getTaskDetail(uuid)
      .then(setDetail)
      .catch((e) => toast.error("加载详情失败: " + e.message))
      .finally(() => setLoading(false));
  }, [open, uuid]);

  const handleRetry = async () => {
    if (!uuid) return;
    try {
      await retryTask(uuid);
      toast.success("任务已重新入队");
      onOpenChange(false);
      fetchTasks();
    } catch (e) {
      toast.error("重试失败: " + (e as Error).message);
    }
  };

  const handleDelete = async () => {
    if (!uuid) return;
    try {
      await deleteTask(uuid);
      toast.success("已删除任务记录");
      onOpenChange(false);
      fetchTasks();
    } catch (e) {
      toast.error("删除失败: " + (e as Error).message);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{detail?.basic?.name || "任务详情"}</DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {uuid}
          </DialogDescription>
        </DialogHeader>

        {loading && <div className="text-muted-foreground py-8 text-center">加载中…</div>}

        {detail && !loading && (
          <div className="space-y-4">
            <div className="flex gap-2 justify-end">
              <Button variant="outline" size="sm" onClick={handleRetry}>
                <RotateCw className="h-3.5 w-3.5" />重试
              </Button>
              <Button variant="destructive" size="sm" onClick={handleDelete}>
                <Trash2 className="h-3.5 w-3.5" />删除
              </Button>
            </div>

            <Section title="基本信息">
              <KV k="传入路径" v={detail.basic?.path} />
              <KV k="识别剧集" v={detail.basic?.name} />
              <KV k="季度" v={detail.basic?.season_id} />
              <KV k="是否动漫" v={detail.basic?.is_anime} />
              <KV k="是否电影" v={detail.basic?.is_movie} />
            </Section>

            <Section title="失败原因">
              <KV k="失败类" v={detail.failure?.reason} />
              <KV k="说明" v={detail.failure?.reason_label} />
              <KV k="原始错误" v={detail.failure?.error} />
            </Section>

            <Section title="AI 识别">
              <KV k="是否使用 AI" v={detail.ai?.ai_used ? "是" : "否"} />
              <KV k="是否尝试 AI" v={detail.ai?.ai_attempted ? "是" : "否"} />
              <KV k="AI 置信度" v={detail.ai?.ai_confidence} />
              <KV k="处理链路" v={detail.ai?.pipeline_mode_label} />
            </Section>

            <Section title="Case Agent / 落地">
              <KV k="状态" v={detail.case_agent?.status_label} />
              <KV k="产品结果类型" v={detail.case_agent?.product_result_kind} />
              <Separator />
              <KV k="目标目录" v={detail.landing?.target_dir} />
              <KV k="映射条目数" v={detail.landing?.mapping_count} />
            </Section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      <div className="space-y-1.5 text-sm">{children}</div>
    </div>
  );
}

function KV({ k, v }: { k: string; v?: unknown }) {
  return (
    <div className="flex gap-3">
      <span className="text-muted-foreground w-24 shrink-0">{k}</span>
      <span className="break-all">
        {v === undefined || v === null || v === "" ? "-" : String(v)}
      </span>
    </div>
  );
}
