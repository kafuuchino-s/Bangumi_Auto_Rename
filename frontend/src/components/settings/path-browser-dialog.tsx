"use client";

import { useEffect, useRef, useState } from "react";
import {
  FolderOpen,
  File as FileIcon,
  Loader2,
  Check,
  Search,
  ChevronLeft,
  ChevronRight,
  HardDrive,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { browseFiles, listDrives } from "@/lib/api/client";
import type { BrowseResult, FileItem } from "@/lib/api/types";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

// 成熟文件选择器：面包屑导航 + 路径输入框 + 左侧盘符栏 + 搜索 + 分页 +
// 单击选中 / 双击进入。selectMode: directory(选目录) | file(选文件)。
export function PathBrowserDialog({
  open,
  onOpenChange,
  onConfirm,
  initialPath = "",
  selectMode = "directory",
  title = "选择路径",
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onConfirm: (path: string) => void;
  initialPath?: string;
  selectMode?: "directory" | "file";
  title?: string;
}) {
  const [browse, setBrowse] = useState<BrowseResult | null>(null);
  const [pathInput, setPathInput] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(false);
  const [drives, setDrives] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [search, setSearch] = useState("");

  const LIMIT = 100;

  const doBrowse = async (
    p: string,
    opts?: { search?: string; page?: number; isFallback?: boolean }
  ) => {
    setLoading(true);
    try {
      const res = await browseFiles(p, {
        search: opts?.search ?? search,
        page: opts?.page ?? page,
        limit: LIMIT,
      });
      setBrowse(res);
      setPathInput(res.current);
      setPage(res.page ?? 1);
      if (opts?.search !== undefined) setSearch(opts.search);
      if (opts?.page !== undefined) setPage(opts.page);
    } catch (e) {
      if (opts?.isFallback) {
        toast.error("浏览失败: " + (e as Error).message);
      } else {
        // 首次失败（路径无效/无权限）→ 兜底到系统默认根
        doBrowse("", { search: "", page: 1, isFallback: true });
      }
    } finally {
      setLoading(false);
    }
  };

  // 把 initialPath 转成起始浏览目录
  const startBrowseDir = (p: string): string => {
    if (!p) return "";
    if (selectMode === "directory") return p;
    const idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
    if (idx <= 0) return "";
    return p.slice(0, idx);
  };

  // Dialog 打开时触发浏览 + 加载盘符
  useEffect(() => {
    if (!open) return;
    setSelected(initialPath);
    setSearchInput("");
    setSearch("");
    setPage(1);
    doBrowse(startBrowseDir(initialPath), { search: "", page: 1 });
    listDrives()
      .then((d) => setDrives(d.drives ?? []))
      .catch(() => setDrives([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 搜索防抖 300ms
  const onSearchChange = (v: string) => {
    setSearchInput(v);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setPage(1);
      doBrowse(browse?.current ?? "", { search: v, page: 1 });
    }, 300);
  };

  // 面包屑：按 \/ 拆分当前路径，累每段路径
  const crumbs = (() => {
    const cur = browse?.current ?? "";
    if (!cur) return [] as { label: string; path: string }[];
    // Windows: H:\Anime\2024 → ["H:", "Anime", "2024"]；Unix: /a/b → ["", "a", "b"]
    const parts = cur.split(/[\\/]/).filter((s) => s !== "");
    const result: { label: string; path: string }[] = [];
    let acc = "";
    parts.forEach((part, i) => {
      if (i === 0 && part.endsWith(":")) {
        // Windows 盘符
        acc = part + "\\";
        result.push({ label: part, path: acc });
      } else {
        acc = acc ? acc + "\\" + part : part;
        result.push({ label: part, path: acc });
      }
    });
    // Unix 根路径
    if (cur.startsWith("/") && parts.length > 0 && !cur[1]?.match(/[a-z]/i)) {
      // 简单处理：Unix 路径用 / 拼接
      return cur
        .split("/")
        .filter(Boolean)
        .map((part, i, arr) => ({
          label: part,
          path: "/" + arr.slice(0, i + 1).join("/"),
        }));
    }
    return result;
  })();

  // 盘符标识：Windows 取首段盘符（"C:"），Linux/Docker 路径用完整路径本身匹配。
  const driveKey = (drv: string) =>
    /^[a-z]:/i.test(drv) ? drv.slice(0, 2).toUpperCase() : drv;
  // 盘符侧栏显示名：Windows 盘符（"C:"），Linux 路径取末段名（/media → "media"）。
  const driveLabel = (drv: string) =>
    /^[a-z]:/i.test(drv) ? drv.slice(0, 2).toUpperCase() : drv.split("/").filter(Boolean).pop() || drv;
  const currentDriveKey = driveKey(browse?.current ?? "");

  const handleItemClick = (item: FileItem) => {
    if (item.name === "..") {
      doBrowse(item.path);
      return;
    }
    if (item.is_dir) {
      if (selectMode === "directory") {
        setSelected(item.path); // 目录模式：点目录选中
      } else {
        doBrowse(item.path); // file 模式：点目录进入
      }
    } else {
      // 文件
      if (selectMode === "file") {
        setSelected(item.path); // file 模式：点文件选中
      }
      // directory 模式：文件不可选，忽略
    }
  };

  const handleItemDouble = (item: FileItem) => {
    if (item.name === "..") {
      doBrowse(item.path);
      return;
    }
    if (item.is_dir) {
      doBrowse(item.path);
    } else if (selectMode === "file") {
      // file 模式双击文件 = 选中并确认
      setSelected(item.path);
      onConfirm(item.path);
      onOpenChange(false);
    }
  };

  const handlePathInputEnter = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && pathInput.trim()) {
      setPage(1);
      doBrowse(pathInput.trim(), { search: "", page: 1 });
    }
  };

  const handleConfirm = () => {
    if (selected) {
      onConfirm(selected);
      onOpenChange(false);
    }
  };

  const totalPages = browse?.total_pages ?? 1;

  return (
    <Dialog open={open} onOpenChange={(v) => onOpenChange(v)}>
      <DialogContent className="sm:max-w-5xl w-[94vw]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <div className="flex gap-3 h-[60vh] min-w-0 overflow-hidden">
          {/* 左侧盘符栏 */}
          {drives.length > 0 && (
            <div className="w-28 flex-shrink-0 border-r pr-2 overflow-y-auto">
              <div className="text-xs text-muted-foreground px-2 py-1 sticky top-0 bg-background">
                位置
              </div>
              {drives.map((drv) => {
                const active =
                  currentDriveKey === driveKey(drv) ||
                  (browse?.current ?? "").startsWith(drv);
                return (
                  <button
                    key={drv}
                    onClick={() => {
                      setPage(1);
                      doBrowse(drv, { search: "", page: 1 });
                    }}
                    className={cn(
                      "w-full flex items-center gap-1.5 px-2 py-1.5 text-sm rounded-md transition-colors text-left",
                      active
                        ? "bg-primary/10 text-primary"
                        : "hover:bg-muted text-foreground"
                    )}
                    title={drv}
                  >
                    <HardDrive className="h-3.5 w-3.5 flex-shrink-0" />
                    <span className="truncate">{driveLabel(drv)}</span>
                  </button>
                );
              })}
            </div>
          )}

          {/* 右侧主区 */}
          <div className="flex-1 flex flex-col min-w-0 gap-2">
            {/* 面包屑 */}
            <div className="flex items-center gap-1 flex-wrap text-sm min-h-6">
              {crumbs.length === 0 ? (
                <span className="text-muted-foreground text-xs">未选择</span>
              ) : (
                crumbs.map((c, i) => (
                  <span key={c.path} className="flex items-center gap-1 min-w-0">
                    <button
                      onClick={() => {
                        setPage(1);
                        doBrowse(c.path, { search: "", page: 1 });
                      }}
                      title={c.path}
                      className={cn(
                        "hover:text-primary hover:underline max-w-[200px] truncate",
                        i === crumbs.length - 1
                          ? "text-foreground font-medium"
                          : "text-muted-foreground"
                      )}
                    >
                      {c.label}
                    </button>
                    {i < crumbs.length - 1 && (
                      <span className="text-muted-foreground text-xs flex-shrink-0">›</span>
                    )}
                  </span>
                ))
              )}
            </div>

            {/* 路径输入框 */}
            <Input
              value={pathInput}
              onChange={(e) => setPathInput(e.target.value)}
              onKeyDown={handlePathInputEnter}
              placeholder="输入路径回车跳转"
              className="text-sm font-mono"
            />

            {/* 搜索框 */}
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={searchInput}
                onChange={(e) => onSearchChange(e.target.value)}
                placeholder="按名称过滤…"
                className="pl-8 text-sm"
              />
            </div>

            {/* 文件列表 */}
            <div className="flex-1 min-h-0 min-w-0 border rounded-md overflow-y-auto overflow-x-hidden bg-background">
              {loading ? (
                <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> 正在浏览…
                </div>
              ) : !browse || browse.items.length === 0 ? (
                <div className="py-12 text-center text-sm text-muted-foreground">
                  空目录
                </div>
              ) : (
                browse.items.map((item) => {
                  const isSel = selected === item.path;
                  const isFileDisabled = !item.is_dir && selectMode === "directory";
                  return (
                    <button
                      key={item.path}
                      onClick={() => handleItemClick(item)}
                      onDoubleClick={() => handleItemDouble(item)}
                      className={cn(
                        "w-full text-left px-3 py-1.5 text-sm flex items-center gap-2 transition-colors overflow-hidden",
                        isSel
                          ? "bg-primary/10 text-primary"
                          : isFileDisabled
                          ? "opacity-40 cursor-default"
                          : "hover:bg-muted/50",
                        item.name === ".." && "text-muted-foreground"
                      )}
                    >
                      {item.is_dir ? (
                        <FolderOpen className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
                      ) : (
                        <FileIcon className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
                      )}
                      <span className="truncate flex-1 min-w-0" title={item.name}>
                        {item.name}
                      </span>
                      {isSel && (
                        <Check className="h-3.5 w-3.5 text-primary flex-shrink-0" />
                      )}
                    </button>
                  );
                })
              )}
            </div>

            {/* 分页栏 */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>共 {browse?.total ?? 0} 项</span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7"
                    disabled={page <= 1}
                    onClick={() => doBrowse(browse?.current ?? "", { page: page - 1 })}
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </Button>
                  <span className="px-2">
                    {page} / {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7"
                    disabled={page >= totalPages}
                    onClick={() => doBrowse(browse?.current ?? "", { page: page + 1 })}
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="gap-2 sm:gap-2 items-center">
          <span className="text-sm text-muted-foreground mr-auto truncate flex-1 min-w-0">
            已选：{selected || "无"}
          </span>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleConfirm} disabled={!selected}>
            确认选择
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
