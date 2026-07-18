"use client";

import { useState, useEffect } from "react";
import { Loader2, ChevronRight, Check, FolderOpen } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createTask } from "@/lib/api/client";
import { PathBrowserDialog } from "@/components/settings/path-browser-dialog";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

type Step = "path" | "confirm";

const LAST_DIR_KEY = "bar_last_task_dir";

// 取路径的父目录（兼容 / 与 \）；取不到（已是根）则原样返回。
const parentDir = (p: string): string => {
  const idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  if (idx <= 0) return p;
  return p.slice(0, idx);
};

export function CreateTaskWizard({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation("tasks");
  const [step, setStep] = useState<Step>("path");
  const [path, setPath] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setStep("path");
      // 预填上次成功入队路径的父目录，方便连续添加同目录下的邻居。
      // 仅在用户未输入时作为起点；不强制覆盖。
      const last = typeof localStorage !== "undefined" ? localStorage.getItem(LAST_DIR_KEY) : "";
      setPath(last || "");
      setBrowserOpen(false);
    }
  }, [open]);

  const handleSubmit = async () => {
    if (!path.trim()) return;
    setSubmitting(true);
    try {
      // 媒体类型由链路自动判定（Pi Case Agent + BGM→TMDB 桥接），
      // 不再要求用户预选 is_anime/is_movie。默认当动漫落地（anime_path）。
      await createTask({ path: path.trim() });
      // 记忆上次入队路径的父目录，下次打开浏览器定位到同级，方便连续加邻居。
      try {
        localStorage.setItem(LAST_DIR_KEY, parentDir(path.trim()));
      } catch {
        /* localStorage 不可用时静默跳过 */
      }
      toast.success(t("taskEnqueued"));
      onOpenChange(false);
      onCreated();
    } catch (e) {
      toast.error(t("addFailed", { message: (e as Error).message }));
    } finally {
      setSubmitting(false);
    }
  };

  const stepIndex = step === "path" ? 0 : 1;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("add")}</DialogTitle>
          <DialogDescription>
            {t("addDescription")}
          </DialogDescription>
        </DialogHeader>

        {/* 步骤指示 */}
        <div className="flex items-center gap-2 mb-2">
          {[t("stepPath"), t("stepConfirm")].map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              <div
                className={`flex items-center gap-1.5 text-sm ${
                  i <= stepIndex ? "text-foreground" : "text-muted-foreground"
                }`}
              >
                <div
                  className={`h-5 w-5 rounded-full flex items-center justify-center text-xs ${
                    i < stepIndex
                      ? "bg-primary text-primary-foreground"
                      : i === stepIndex
                      ? "border-2 border-primary text-primary"
                      : "border border-muted-foreground/40"
                  }`}
                >
                  {i < stepIndex ? <Check className="h-3 w-3" /> : i + 1}
                </div>
                {label}
              </div>
              {i < 1 && <ChevronRight className="h-4 w-4 text-muted-foreground" />}
            </div>
          ))}
        </div>

        {/* Step: 路径 */}
        {step === "path" && (
          <div className="space-y-3">
            <div className="flex gap-2">
              <Input
                placeholder={t("pathPlaceholder")}
                aria-label={t("path")}
                value={path}
                onChange={(e) => setPath(e.target.value)}
              />
              <Button variant="outline" onClick={() => setBrowserOpen(true)}>
                <FolderOpen className="h-4 w-4" />
                {t("browse")}
              </Button>
            </div>
            <div className="text-xs text-muted-foreground">
              {t("pathHelp")}
            </div>
          </div>
        )}

        {/* Step: 确认 */}
        {step === "confirm" && (
          <div className="space-y-3">
            <div className="border rounded-md p-4 space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">{t("path")}</span>
                <span className="font-mono text-xs break-all text-right max-w-[60%]">
                  {path}
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">{t("mediaType")}</span>
                <span>{t("autoDetect")}</span>
              </div>
            </div>
            <div className="text-xs text-muted-foreground">
              {t("pipelineSummary")}
            </div>
          </div>
        )}

        <DialogFooter className="gap-2">
          {step === "path" && (
            <Button onClick={() => setStep("confirm")} disabled={!path.trim()}>
              {t("nextStep")}
            </Button>
          )}
          {step === "confirm" && (
            <>
              <Button variant="outline" onClick={() => setStep("path")}>
                {t("previousStep")}
              </Button>
              <Button onClick={handleSubmit} disabled={submitting}>
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : t("confirmEnqueue")}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>

      {/* 复用配置页的成熟文件浏览器：面包屑/盘符/搜索/分页/路径输入 */}
      <PathBrowserDialog
        open={browserOpen}
        onOpenChange={setBrowserOpen}
        onConfirm={(p) => {
          setPath(p);
          setBrowserOpen(false);
        }}
        initialPath={path}
        selectMode="directory"
        title={t("chooseMediaDirectory")}
      />
    </Dialog>
  );
}
