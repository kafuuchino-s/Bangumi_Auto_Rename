"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { RotateCw, Trash2 } from "lucide-react";
import { getTaskDetail, retryTask, deleteTask } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import type { TaskDetail } from "@/lib/api/types";
import { toast } from "sonner";
import { useTaskStore } from "@/store/task-store";
import { useLocale } from "@/i18n/use-locale";
import { formatBytes as intlBytes, formatNumber } from "@/lib/format";

export function TaskDetailDialog({
  uuid,
  open,
  onOpenChange,
}: {
  uuid: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("tasks");
  const { locale } = useLocale();
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const { fetchTasks } = useTaskStore();

  useEffect(() => {
    if (!open || !uuid) return;
    setLoading(true);
    setDetail(null);
    void getTaskDetail(uuid)
      .then(setDetail)
      .catch((error) => toast.error(apiErrorMessage(error)))
      .finally(() => setLoading(false));
  }, [open, uuid]);

  const retry = async () => {
    if (!uuid) return;
    try {
      await retryTask(uuid);
      toast.success(t("taskEnqueued"));
      onOpenChange(false);
      void fetchTasks();
    } catch (error) {
      toast.error(apiErrorMessage(error));
    }
  };

  const remove = async () => {
    if (!uuid) return;
    try {
      await deleteTask(uuid);
      toast.success(t("taskDeleted"));
      onOpenChange(false);
      void fetchTasks();
    } catch (error) {
      toast.error(apiErrorMessage(error));
    }
  };

  const basic = detail?.basic;
  const sourceEvidence = detail?.source_evidence;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-5xl w-[94vw] max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{basic?.name || t("detail", { ns: "common" })}</DialogTitle>
          <DialogDescription className="font-mono text-xs">{uuid}</DialogDescription>
        </DialogHeader>
        {loading && <div className="text-muted-foreground py-8 text-center">{t("loading", { ns: "common" })}</div>}
        {detail && !loading && (
          <div className="space-y-4">
            <div className="flex gap-2 justify-end">
              <Button variant="outline" size="sm" onClick={() => void retry()}>
                <RotateCw className="h-3.5 w-3.5" />{t("retry", { ns: "common" })}
              </Button>
              <Button variant="destructive" size="sm" onClick={() => void remove()}>
                <Trash2 className="h-3.5 w-3.5" />{t("delete", { ns: "common" })}
              </Button>
            </div>

            <Section title={t("detail", { ns: "common" })}>
              <KV k={t("path")} v={basic?.path} />
              <KV k={t("recognizedTitle")} v={basic?.name} />
              <KV k={t("season")} v={basic?.season_id} />
            </Section>

            {sourceEvidence?.provider ? (
              <Section title={t("detailSourceEvidence")}>
                <KV k={t("detailProvider")} v={sourceEvidence.provider} />
                <KV k={t("moviepilotHistoryId")} v={sourceEvidence.history_id} />
                <KV k={t("moviepilotRelease")} v={sourceEvidence.torrent_name} />
                <KV k={t("moviepilotSite")} v={sourceEvidence.torrent_site} />
                <KV k={t("moviepilotHash")} v={sourceEvidence.download_hash} />
                <KV k={t("moviepilotSourcePath")} v={sourceEvidence.source_path} />
                <KV
                  k={t("moviepilotMediaHint")}
                  v={[
                    sourceEvidence.title,
                    sourceEvidence.media_type,
                    sourceEvidence.year,
                    sourceEvidence.seasons,
                    sourceEvidence.episodes,
                  ].filter(Boolean).join(" · ")}
                />
                <KV k={t("detailTmdb")} v={sourceEvidence.tmdb_id} />
                <KV k={t("moviepilotDownloadedAt")} v={sourceEvidence.downloaded_at} />
                <KV
                  k={t("moviepilotCompletion")}
                  v={sourceEvidence.completion_evidence
                    ? t(`moviepilotCompletion_${sourceEvidence.completion_evidence}`)
                    : undefined}
                />
              </Section>
            ) : null}

            {detail.tmdb_subjects?.length ? (
              <Section title={t("detailTmdb")}>
                {detail.tmdb_subjects.map((subject) => (
                  <div key={subject.tmdb_ref} className="space-y-1.5 border-l-2 border-primary/30 pl-3">
                    <LinkKV
                      k={t("detailId")}
                      href={tmdbUrl(subject.tmdb_ref, subject.tmdb_id)}
                      text={`${subject.tmdb_ref}${subject.name ? ` · ${subject.name}` : ""}${subject.year ? ` (${subject.year})` : ""}`}
                    />
                    <KV k={t("detailType")} v={subject.media_type} />
                    <KV k={t("detailEpisodes")} v={formatRanges(subject.episode_ranges, locale, t("special"))} />
                  </div>
                ))}
              </Section>
            ) : null}

            {detail.bangumi_subjects?.length ? (
              <Section title={t("detailBangumi")}>
                {detail.bangumi_subjects.map((subject) => (
                  <div key={String(subject.id)} className="space-y-1.5 border-l-2 border-primary/30 pl-3">
                    <LinkKV
                      k={t("detailId")}
                      href={`https://bgm.tv/subject/${subject.id}`}
                      text={`${subject.id}${subject.name_cn ? ` · ${subject.name_cn}` : subject.name ? ` · ${subject.name}` : ""}`}
                    />
                    <KV k={t("detailName")} v={subject.name} />
                    <KV k={t("detailEpisodes")} v={formatRanges(subject.episode_ranges, locale, t("special"))} />
                  </div>
                ))}
              </Section>
            ) : null}

            <Section title={t("failed")}>
              <KV k={t("detailCode")} v={detail.failure?.reason} />
              <KV k={t("detailDiagnostic")} v={detail.failure?.error} />
            </Section>

            <Section title={t("detailAi")}>
              <KV k={t("detailUsed")} v={booleanText(detail.ai?.ai_used, t)} />
              <KV k={t("detailAttempted")} v={booleanText(detail.ai?.ai_attempted, t)} />
              <KV k={t("detailPipeline")} v={detail.ai?.pipeline_mode} />
            </Section>

            <Section title={t("detailCaseAgent")}>
              <KV k={t("status")} v={detail.case_agent?.status} />
              <KV k={t("detailResult")} v={detail.case_agent?.product_result_kind} />
              <Separator />
              <KV k={t("detailTarget")} v={detail.landing?.target_dir} />
            </Section>

            {detail.mapping_details?.length ? (
              <Section title={`${t("detailMapping", { count: formatNumber(detail.mapping_details.length, locale) })}${detail.total_size_bytes ? ` · ${intlBytes(detail.total_size_bytes, locale)}` : ""}`}>
                <div className="border rounded-md max-h-72 overflow-auto">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-muted/60">
                      <tr className="text-left text-muted-foreground">
                        <th className="px-2 py-1.5">{t("detailSource")}</th>
                        <th className="px-2 py-1.5">{t("detailBangumi")}</th>
                        <th className="px-2 py-1.5">{t("detailTmdb")}</th>
                        <th className="px-2 py-1.5">{t("detailConfidence")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.mapping_details.map((row, index) => (
                        <tr key={index} className="border-t">
                          <td className="px-2 py-1 truncate max-w-[420px]" title={row.source_path}>{row.source_name}</td>
                          <td className="px-2 py-1">{formatTarget(row.bangumi_target ?? row.bgm)}</td>
                          <td className="px-2 py-1">{formatTarget(row.tmdb_target ?? row.tmdb)}</td>
                          <td className="px-2 py-1 text-muted-foreground">{row.confidence ?? t("notAvailable", { ns: "common" })}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            ) : null}

            {detail.subtitle_fetch && (
              <Section title={t("detailSubtitleFetch")}>
                <KV k={t("status")} v={detail.subtitle_fetch.status} />
                <KV k={t("detailCaseAgent")} v={detail.subtitle_fetch.case_agent_status} />
                <KV k={t("detailProvider")} v={detail.subtitle_fetch.provider} />
                <KV k={t("detailFailure")} v={detail.subtitle_fetch.failure_reason} />
                <Separator />
                <KV k={t("detailMatched")} v={formatNumber(detail.subtitle_fetch.matched_count, locale)} />
                <KV k={t("detailSelections")} v={formatNumber(detail.subtitle_fetch.selections_count, locale)} />
                <KV k={t("detailUnmatched")} v={formatNumber(detail.subtitle_fetch.unmatched_count, locale)} />
              </Section>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="space-y-2"><h3 className="text-sm font-semibold">{title}</h3><div className="space-y-1.5 text-sm">{children}</div></div>;
}

function KV({ k, v }: { k: string; v?: unknown }) {
  return <div className="flex gap-3"><span className="text-muted-foreground w-28 shrink-0">{k}</span><span className="break-all">{v === undefined || v === null || v === "" ? "-" : String(v)}</span></div>;
}

function LinkKV({ k, href, text }: { k: string; href: string; text: string }) {
  return <div className="flex gap-3"><span className="text-muted-foreground w-28 shrink-0">{k}</span><a href={href} target="_blank" rel="noopener noreferrer" className="break-all text-primary underline">{text || "-"}</a></div>;
}

function booleanText(value: boolean | null | undefined, t: (key: string, options?: Record<string, unknown>) => string) {
  if (value == null) return "-";
  return value ? t("yes", { ns: "common" }) : t("no", { ns: "common" });
}

function formatRanges(value: unknown, locale: "zh-CN" | "en-US", specialLabel: string): string {
  if (!Array.isArray(value)) return value == null ? "-" : String(value);
  return value.map((range) => {
    if (!range || typeof range !== "object") return String(range);
    const item = range as Record<string, unknown>;
    if (item.kind === "special") return `${specialLabel} · ${formatNumber(Number(item.count ?? 0), locale)}`;
    if (item.season !== undefined) {
      const start = formatNumber(Number(item.start), locale);
      const end = formatNumber(Number(item.end ?? item.start), locale);
      return `S${String(item.season).padStart(2, "0")}E${start}–E${end}`;
    }
    return `${formatNumber(Number(item.start), locale)}–${formatNumber(Number(item.end ?? item.start), locale)}`;
  }).join(" + ");
}

function formatTarget(value: unknown): string {
  if (value == null) return "-";
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);
  const item = value as Record<string, unknown>;
  return String(item.episode_token ?? item.ref ?? item.sort ?? item.id ?? "-");
}

function tmdbUrl(ref: string, id: number | string | null | undefined): string {
  return `https://www.themoviedb.org/${ref.startsWith("movie:") ? "movie" : "tv"}/${id ?? ""}`;
}
