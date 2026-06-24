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
      <DialogContent className="sm:max-w-5xl w-[94vw] max-h-[85vh] overflow-y-auto">
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
            </Section>

            {detail.tmdb_subjects && detail.tmdb_subjects.length > 0 && (
              <Section title="TMDB 条目">
                {detail.tmdb_subjects.map((t) => (
                  <div key={t.tmdb_ref} className="space-y-1.5 border-l-2 border-primary/30 pl-3">
                    <LinkKV
                      k="条目 ID"
                      href={tmdbUrl(t.tmdb_ref, t.tmdb_id)}
                      text={`${t.tmdb_ref}${t.name ? ` · ${t.name}` : ""}${t.year && t.year !== "-" ? ` (${t.year})` : ""}`}
                    />
                    <KV k="媒体类型" v={t.media_type === "movie" ? "电影" : t.media_type === "tv" ? "剧集 (TV)" : t.media_type} />
                    <KV k="集数范围" v={t.episode_ranges} />
                  </div>
                ))}
              </Section>
            )}

            {detail.bangumi_subjects && detail.bangumi_subjects.length > 0 && (
              <Section title="Bangumi 条目">
                {detail.bangumi_subjects.map((s) => (
                  <div key={String(s.id)} className="space-y-1.5 border-l-2 border-primary/30 pl-3">
                    <LinkKV
                      k="条目 ID"
                      href={`https://bgm.tv/subject/${s.id}`}
                      text={`${s.id}${s.name_cn ? ` · ${s.name_cn}` : s.name ? ` · ${s.name}` : ""}`}
                    />
                    <KV k="日文名" v={s.name} />
                    <KV k="集数范围" v={s.episode_ranges} />
                  </div>
                ))}
              </Section>
            )}

            <Section title="失败原因">
              <KV k="失败类" v={detail.failure?.reason} />
              <KV k="说明" v={detail.failure?.reason_label} />
              <KV k="原始错误" v={detail.failure?.error} />
            </Section>

            <Section title="AI 识别">
              <KV k="是否使用 AI" v={detail.ai?.ai_used ? "是" : "否"} />
              <KV k="是否尝试 AI" v={detail.ai?.ai_attempted ? "是" : "否"} />
              <KV k="处理链路" v={detail.ai?.pipeline_mode_label} />
            </Section>

            <Section title="Case Agent / 落地">
              <KV k="状态" v={detail.case_agent?.status_label} />
              <KV k="产品结果类型" v={detail.case_agent?.product_result_kind} />
              <Separator />
              <KV k="目标目录" v={detail.landing?.target_dir} />
            </Section>

            {detail.mapping_details && detail.mapping_details.length > 0 && (
              <Section
                title={`映射明细（${detail.mapping_details.length} 条${detail.total_size && detail.total_size !== "-" ? `，总计 ${detail.total_size}` : ""}）`}
              >
                <div className="border rounded-md max-h-72 overflow-auto">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-muted/60 backdrop-blur">
                      <tr className="text-left text-muted-foreground">
                        <th className="px-2 py-1.5 font-medium">源文件</th>
                        <th className="px-2 py-1.5 font-medium whitespace-nowrap">BGM</th>
                        <th className="px-2 py-1.5 font-medium whitespace-nowrap">TMDB</th>
                        <th className="px-2 py-1.5 font-medium whitespace-nowrap">置信度</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.mapping_details.map((r, i) => (
                        <tr
                          key={i}
                          className="border-t hover:bg-muted/30"
                          title={r.source_path}
                        >
                          <td className="px-2 py-1 truncate max-w-[420px]">
                            {r.source_name}
                          </td>
                          <td className="px-2 py-1 whitespace-nowrap">{r.bgm}</td>
                          <td className="px-2 py-1 whitespace-nowrap">{r.tmdb}</td>
                          <td className="px-2 py-1 whitespace-nowrap text-muted-foreground">
                            {r.confidence}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            )}

            {detail.subtitle_fetch && (
              <Section title="字幕自动抓取">
                <KV k="抓取状态" v={detail.subtitle_fetch.status_label} />
                <KV
                  k="Case Agent"
                  v={detail.subtitle_fetch.case_agent_status_label}
                />
                <KV k="来源" v={detail.subtitle_fetch.provider} />
                {detail.subtitle_fetch.failure_reason && (
                  <KV k="失败原因" v={detail.subtitle_fetch.failure_reason} />
                )}
                <Separator />
                <KV
                  k="已配对 / 缺字幕"
                  v={`${detail.subtitle_fetch.matched_count} / ${detail.subtitle_fetch.missing_video_count}`}
                />
                <KV k="选中包数" v={detail.subtitle_fetch.selections_count} />
                <KV k="未配对字幕" v={detail.subtitle_fetch.unmatched_count} />
                <KV
                  k="无落点字幕"
                  v={detail.subtitle_fetch.no_target_count}
                />
              </Section>
            )}
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

// 带跳转链接的 KV：值显示为可点击外链（新标签打开）。
function LinkKV({ k, href, text }: { k: string; href: string; text: string }) {
  return (
    <div className="flex gap-3">
      <span className="text-muted-foreground w-24 shrink-0">{k}</span>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="break-all text-primary underline underline-offset-2 hover:opacity-80"
      >
        {text || "-"}
      </a>
    </div>
  );
}

// TMDB ref → themoviedb.org URL（tv:{id} → /tv/{id}，movie:{id} → /movie/{id}）。
function tmdbUrl(ref: string, id: number | string | null | undefined): string {
  const kind = ref.startsWith("movie:") ? "movie" : "tv";
  return `https://www.themoviedb.org/${kind}/${id ?? ""}`;
}
