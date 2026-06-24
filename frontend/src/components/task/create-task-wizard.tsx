"use client";

import { useState, useEffect } from "react";
import { FolderOpen, Loader2, ChevronRight, Check } from "lucide-react";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";
import { browseFiles, listDrives, createTask } from "@/lib/api/client";
import type { BrowseResult, FileItem } from "@/lib/api/types";
import { toast } from "sonner";

type Step = "path" | "type" | "confirm";

export function CreateTaskWizard({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
  const [step, setStep] = useState<Step>("path");
  const [path, setPath] = useState("");
  const [browse, setBrowse] = useState<BrowseResult | null>(null);
  const [browseRoot, setBrowseRoot] = useState("");
  const [loadingBrowse, setLoadingBrowse] = useState(false);
  const [mediaType, setMediaType] = useState<"auto" | "anime" | "movie">("auto");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setStep("path");
      setPath("");
      setBrowse(null);
      setBrowseRoot("");
      setMediaType("auto");
    }
  }, [open]);

  const doBrowse = async (p: string) => {
    setLoadingBrowse(true);
    try {
      const res = await browseFiles(p);
      setBrowse(res);
      setBrowseRoot(res.current);
      setPath(res.current);
    } catch (e) {
      toast.error("浏览失败: " + (e as Error).message);
    } finally {
      setLoadingBrowse(false);
    }
  };

  const handlePickDir = (item: FileItem) => {
    if (item.is_dir) doBrowse(item.path);
  };

  const handleUseCurrent = () => {
    if (browseRoot) setPath(browseRoot);
    setStep("type");
  };

  const handleSubmit = async () => {
    if (!path.trim()) return;
    setSubmitting(true);
    try {
      const body: {
        path: string;
        is_anime?: boolean | null;
        is_movie?: boolean | null;
      } = { path: path.trim() };
      if (mediaType === "anime") body.is_anime = true;
      else if (mediaType === "movie") body.is_movie = true;
      await createTask(body);
      toast.success("任务已加入队列");
      onOpenChange(false);
      onCreated();
    } catch (e) {
      toast.error("添加失败: " + (e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const stepIndex = step === "path" ? 0 : step === "type" ? 1 : 2;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>添加任务</DialogTitle>
          <DialogDescription>
            选择本地媒体目录，程序将自动整理并重命名。
          </DialogDescription>
        </DialogHeader>

        {/* 步骤指示 */}
        <div className="flex items-center gap-2 mb-2">
          {["选择路径", "媒体类型", "确认入队"].map((label, i) => (
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
              {i < 2 && <ChevronRight className="h-4 w-4 text-muted-foreground" />}
            </div>
          ))}
        </div>

        {/* Step: 路径 */}
        {step === "path" && (
          <div className="space-y-3">
            <div className="flex gap-2">
              <Input
                placeholder="粘贴路径，或从下方浏览选择…"
                value={path}
                onChange={(e) => setPath(e.target.value)}
              />
              <Button
                variant="outline"
                onClick={() => doBrowse(browseRoot || "")}
              >
                <FolderOpen className="h-4 w-4" />
                浏览
              </Button>
            </div>

            {browse && (
              <div className="border rounded-md max-h-64 overflow-auto">
                <div className="flex items-center justify-between px-3 py-2 border-b bg-muted/30 sticky top-0">
                  <span className="text-xs text-muted-foreground truncate" title={browse.current}>
                    {browse.current}
                  </span>
                  {browse.parent !== null && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-xs"
                      onClick={() => doBrowse(browse.parent as string)}
                    >
                      返回上级
                    </Button>
                  )}
                </div>
                {browse.items.length === 0 ? (
                  <div className="px-3 py-6 text-center text-sm text-muted-foreground">
                    空目录
                  </div>
                ) : (
                  browse.items.map((item) => (
                    <button
                      key={item.path}
                      onClick={() => handlePickDir(item)}
                      className={`w-full text-left px-3 py-1.5 text-sm hover:bg-muted/40 flex items-center gap-2 ${
                        !item.is_dir ? "opacity-50" : ""
                      }`}
                    >
                      {item.is_dir ? (
                        <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
                      ) : (
                        <span className="h-3.5 w-3.5 inline-block" />
                      )}
                      <span className="truncate">{item.name}</span>
                    </button>
                  ))
                )}
              </div>
            )}
            {loadingBrowse && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> 正在浏览…
              </div>
            )}
          </div>
        )}

        {/* Step: 类型 */}
        {step === "type" && (
          <div className="space-y-4">
            <div className="text-sm text-muted-foreground">
              路径：<span className="text-foreground font-mono text-xs break-all">{path}</span>
            </div>
            <RadioGroup
              value={mediaType}
              onValueChange={(v) => setMediaType(v as typeof mediaType)}
              className="space-y-3"
            >
              <div className="flex items-start gap-3 p-3 border rounded-md hover:bg-muted/30">
                <RadioGroupItem value="auto" id="mt-auto" className="mt-1" />
                <div className="flex-1">
                  <Label htmlFor="mt-auto" className="cursor-pointer">自动判断（推荐）</Label>
                  <div className="text-xs text-muted-foreground">由 AI 根据内容判断动漫/电影/剧集</div>
                </div>
              </div>
              <div className="flex items-start gap-3 p-3 border rounded-md hover:bg-muted/30">
                <RadioGroupItem value="anime" id="mt-anime" className="mt-1" />
                <div className="flex-1">
                  <Label htmlFor="mt-anime" className="cursor-pointer">动漫</Label>
                  <div className="text-xs text-muted-foreground">强制按动漫整理</div>
                </div>
              </div>
              <div className="flex items-start gap-3 p-3 border rounded-md hover:bg-muted/30">
                <RadioGroupItem value="movie" id="mt-movie" className="mt-1" />
                <div className="flex-1">
                  <Label htmlFor="mt-movie" className="cursor-pointer">电影</Label>
                  <div className="text-xs text-muted-foreground">强制按电影整理</div>
                </div>
              </div>
            </RadioGroup>
          </div>
        )}

        {/* Step: 确认 */}
        {step === "confirm" && (
          <div className="space-y-3">
            <div className="border rounded-md p-4 space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">路径</span>
                <span className="font-mono text-xs break-all text-right max-w-[60%]">{path}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">媒体类型</span>
                <span>
                  {mediaType === "auto" ? "自动判断" : mediaType === "anime" ? "动漫" : "电影"}
                </span>
              </div>
            </div>
            <div className="text-xs text-muted-foreground">
              入队后将自动执行：标题提取 → TMDB/Bangumi 映射 → 重命名落盘 → 字幕/通知。
            </div>
          </div>
        )}

        <DialogFooter className="gap-2">
          {step === "path" && (
            <>
              <Button variant="outline" onClick={() => listDrives().then((d) => doBrowse(d.system)).catch(() => doBrowse(""))}>
                从根目录
              </Button>
              <Button onClick={handleUseCurrent} disabled={!path.trim()}>
                下一步
              </Button>
            </>
          )}
          {step === "type" && (
            <>
              <Button variant="outline" onClick={() => setStep("path")}>上一步</Button>
              <Button onClick={() => setStep("confirm")}>下一步</Button>
            </>
          )}
          {step === "confirm" && (
            <>
              <Button variant="outline" onClick={() => setStep("type")}>上一步</Button>
              <Button onClick={handleSubmit} disabled={submitting}>
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "确认入队"}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
