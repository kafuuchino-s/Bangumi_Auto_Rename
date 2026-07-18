"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { LayoutList, LayoutGrid, Search, X, RotateCw, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useTaskStore } from "@/store/task-store";
import { apiErrorMessage } from "@/lib/api/errors";
import { toast } from "sonner";

export function TasksToolbar({ pageSize, setPageSize }: { pageSize: number; setPageSize: (n: number) => void }) {
  const { t } = useTranslation("tasks");
  const { filters, setFilters, viewMode, setViewMode, selected, clearSelected, batchRetry, batchRemove } = useTaskStore();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const count = selected.size;
  const batch = async (action: () => Promise<void>, success: string) => { setLoading(true); try { await action(); toast.success(success); setConfirmOpen(false); } catch (error) { toast.error(apiErrorMessage(error)); } finally { setLoading(false); } };
  return <div className="flex flex-wrap items-center gap-2 mb-3"><div className="relative flex-1 min-w-[200px] max-w-sm"><Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" /><Input placeholder={t("filterSearch")} value={filters.search} onChange={(event) => setFilters({ search: event.target.value })} className="pl-8" />{filters.search && <button onClick={() => setFilters({ search: "" })} className="absolute right-2.5 top-2.5 text-muted-foreground" aria-label={t("close", { ns: "common" })}><X className="h-4 w-4" /></button>}</div><Separator orientation="vertical" className="h-8" />
    <div className="flex items-center gap-1 border rounded-md p-0.5"><Button variant={viewMode === "table" ? "secondary" : "ghost"} size="icon" className="h-7 w-7" onClick={() => setViewMode("table")} title={t("tableView", { ns: "common" })} aria-label={t("tableView", { ns: "common" })}><LayoutList className="h-4 w-4" /></Button><Button variant={viewMode === "card" ? "secondary" : "ghost"} size="icon" className="h-7 w-7" onClick={() => setViewMode("card")} title={t("cardView", { ns: "common" })} aria-label={t("cardView", { ns: "common" })}><LayoutGrid className="h-4 w-4" /></Button></div>
    <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))} className="h-9 border rounded-md px-2 text-sm bg-background" aria-label={t("itemsPerPage", { ns: "common", count: pageSize })}>{[10, 20, 50, 100].map((size) => <option key={size} value={size}>{t("itemsPerPage", { ns: "common", count: size })}</option>)}</select>
    {count > 0 && <div className="flex items-center gap-2 ml-auto"><span className="text-sm text-muted-foreground">{t("selected", { ns: "common", count })}</span><Button variant="outline" size="sm" onClick={() => void batch(() => batchRetry([...selected]), t("taskEnqueued"))} disabled={loading}><RotateCw className="h-3.5 w-3.5" />{t("retry", { ns: "common" })}</Button><Button variant="destructive" size="sm" onClick={() => setConfirmOpen(true)} disabled={loading}><Trash2 className="h-3.5 w-3.5" />{t("delete", { ns: "common" })}</Button><Button variant="ghost" size="sm" onClick={clearSelected}>{t("cancel", { ns: "common" })}</Button></div>}
    <ConfirmationDialog open={confirmOpen} onOpenChange={setConfirmOpen} title={t("deleteConfirmTitle")} description={t("deleteConfirmDescription")} confirmLabel={t("delete", { ns: "common" })} confirmVariant="destructive" onConfirm={() => void batch(() => batchRemove([...selected]), t("taskDeleted"))} loading={loading} />
  </div>;
}
