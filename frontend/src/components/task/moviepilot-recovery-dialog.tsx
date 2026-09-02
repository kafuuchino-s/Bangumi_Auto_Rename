"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, History, Loader2, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  enqueueMoviePilotRecovery,
  getMoviePilotRecovery,
} from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import type {
  MoviePilotRecoveryItem,
  MoviePilotRecoveryReport,
} from "@/lib/api/types";
import { formatNumber } from "@/lib/format";
import { useLocale } from "@/i18n/use-locale";
import { useTaskStore } from "@/store/task-store";
import { toast } from "sonner";

export function MoviePilotRecoveryDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("tasks");
  const { locale } = useLocale();
  const { fetchTasks } = useTaskStore();
  const [report, setReport] = useState<MoviePilotRecoveryReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<MoviePilotRecoveryItem | null>(null);
  const [enqueuing, setEnqueuing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await getMoviePilotRecovery());
    } catch (value) {
      setError(apiErrorMessage(value));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [load, open]);

  const recoverable = useMemo(
    () => report?.items.filter((item) => item.status === "recoverable") ?? [],
    [report],
  );

  const enqueue = async () => {
    if (!selected) return;
    setEnqueuing(true);
    try {
      await enqueueMoviePilotRecovery(selected.history_id);
      toast.success(t("moviepilotRecoveryEnqueued"));
      setSelected(null);
      await Promise.all([load(), fetchTasks()]);
    } catch (value) {
      toast.error(apiErrorMessage(value));
    } finally {
      setEnqueuing(false);
    }
  };

  const summary = report?.summary;
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="w-[96vw] max-w-6xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <div className="flex items-center justify-between gap-3 pr-8">
              <DialogTitle>{t("moviepilotRecoveryTitle")}</DialogTitle>
              <Button
                variant="outline"
                size="icon"
                aria-label={t("refresh", { ns: "common" })}
                title={t("refresh", { ns: "common" })}
                onClick={() => void load()}
                disabled={loading}
              >
                <RefreshCw className={loading ? "animate-spin" : ""} />
              </Button>
            </div>
            <DialogDescription>
              {t("moviepilotRecoveryHistory", {
                count: formatNumber(summary?.history_count ?? 0, locale),
              })}
            </DialogDescription>
          </DialogHeader>

          {summary ? (
            <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border bg-border text-sm sm:grid-cols-5">
              <SummaryCell label={t("moviepilotRecoverable")} value={summary.recoverable_count} />
              <SummaryCell label={t("moviepilotDownloading")} value={summary.downloading_count} />
              <SummaryCell label={t("moviepilotQueued")} value={summary.queued_count} />
              <SummaryCell label={t("moviepilotProcessed")} value={summary.processed_count} />
              <SummaryCell label={t("moviepilotUnavailable")} value={summary.unavailable_count} />
            </div>
          ) : null}

          {report?.warnings.download_status_lookup_failed ? (
            <div className="flex items-center gap-2 text-sm text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {t("moviepilotStatusUnavailable", {
                count: formatNumber(report.warnings.download_status_lookup_failed, locale),
              })}
            </div>
          ) : null}

          {error ? (
            <div className="text-sm text-destructive">{error}</div>
          ) : loading && !report ? (
            <div className="flex items-center justify-center gap-2 py-12 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("loading", { ns: "common" })}
            </div>
          ) : recoverable.length === 0 ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              {t("moviepilotRecoveryEmpty")}
            </div>
          ) : (
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("moviepilotRelease")}</TableHead>
                    <TableHead>{t("moviepilotMediaHint")}</TableHead>
                    <TableHead>{t("moviepilotSourcePath")}</TableHead>
                    <TableHead>{t("moviepilotCompletion")}</TableHead>
                    <TableHead className="text-right">{t("actions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {recoverable.map((item) => (
                    <TableRow key={item.history_id}>
                      <TableCell className="min-w-56 max-w-80">
                        <div className="font-medium break-words">{item.title || item.torrent_name}</div>
                        <div className="text-xs text-muted-foreground break-words">
                          {item.torrent_name}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {[item.torrent_site, item.downloaded_at].filter(Boolean).join(" · ")}
                        </div>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm">
                        <div>{[item.media_type, item.year].filter(Boolean).join(" · ") || "-"}</div>
                        <div className="text-xs text-muted-foreground">
                          {[item.seasons, item.episodes, item.tmdb_id ? `TMDB ${item.tmdb_id}` : ""]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      </TableCell>
                      <TableCell className="min-w-64 max-w-96 font-mono text-xs break-all">
                        {item.local_path}
                      </TableCell>
                      <TableCell>
                        <Badge variant={item.completion_state === "completed" ? "secondary" : "outline"}>
                          {t(`moviepilotCompletion_${item.completion_state}`)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button size="sm" onClick={() => setSelected(item)}>
                          <History className="h-4 w-4" />
                          {t("moviepilotRecoveryEnqueue")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmationDialog
        open={selected !== null}
        onOpenChange={(value) => !value && setSelected(null)}
        title={t("moviepilotRecoveryConfirmTitle")}
        description={t("moviepilotRecoveryConfirmDescription", {
          title: selected?.title || selected?.torrent_name || "-",
          path: selected?.local_path || "-",
        })}
        confirmLabel={t("moviepilotRecoveryEnqueue")}
        onConfirm={() => void enqueue()}
        loading={enqueuing}
      />
    </>
  );
}

function SummaryCell({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between gap-2 bg-background px-3 py-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-semibold tabular-nums">{value}</span>
    </div>
  );
}
