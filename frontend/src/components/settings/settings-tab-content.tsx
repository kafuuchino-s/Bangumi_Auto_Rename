"use client";

import { useEffect, useMemo, useState } from "react";
import { Save, FlaskConical, Cast, Send, Loader2, AlertTriangle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Info, FolderOpen } from "lucide-react";
import { PathBrowserDialog } from "./path-browser-dialog";
import {
  getConfig,
  updateConfig,
  getFieldSpec,
  testAi,
  testEmby,
  testTelegram,
} from "@/lib/api/client";
import type { ConfigDict, FieldSpecEntry } from "@/lib/api/types";
import { toast } from "sonner";

const TAB_LABEL: Record<string, string> = {
  general: "基础与路径",
  ai: "AI 识别",
  subtitle: "字幕",
  notify: "通知",
  advanced: "高级",
};

export function SettingsTabContent({ tab }: { tab: string }) {
  const [config, setConfig] = useState<ConfigDict>({});
  const [initialConfig, setInitialConfig] = useState<ConfigDict>({});
  const [spec, setSpec] = useState<FieldSpecEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([getConfig(), getFieldSpec()])
      .then(([c, s]) => {
        if (!active) return;
        setConfig(c);
        setInitialConfig(c);
        setSpec(s);
        setLoading(false);
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  // 按 tab 过滤 + group 聚合
  const grouped = useMemo(() => {
    const out: Record<string, FieldSpecEntry[]> = {};
    for (const e of spec) {
      if (e.tab !== tab) continue;
      (out[e.group] ??= []).push(e);
    }
    return out;
  }, [spec, tab]);

  const setField = (key: string, value: unknown) => {
    setConfig((c) => ({ ...c, [key]: value }));
  };

  const isDirty = useMemo(
    () => JSON.stringify(config) !== JSON.stringify(initialConfig),
    [config, initialConfig]
  );

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateConfig(config);
      setInitialConfig(config);
      toast.success("配置保存成功");
    } catch (e) {
      toast.error("保存失败: " + (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setConfig(initialConfig);
  };

  const runTest = async (name: string, fn: () => Promise<unknown>) => {
    toast.info(`正在测试${name}…`);
    try {
      const r = (await fn()) as { success?: boolean; message?: string };
      if (r.success) toast.success(`${name}: ${r.message}`);
      else toast.error(`${name}: ${r.message ?? "失败"}`);
    } catch (e) {
      toast.error(`${name}测试失败: ` + (e as Error).message);
    }
  };

  const testForGroup = (group: string) => {
    if (group === "AI 识别") return () => runTest("AI", testAi);
    if (group === "通知：Emby") return () => runTest("Emby", testEmby);
    if (group === "通知：Telegram") return () => runTest("Telegram", testTelegram);
    return undefined;
  };

  if (loading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 2 }).map((_, i) => (
          <Card key={i}>
            <CardHeader>
              <div className="h-5 w-32 bg-muted rounded animate-pulse" />
            </CardHeader>
            <CardContent className="space-y-3">
              {Array.from({ length: 3 }).map((_, j) => (
                <div key={j} className="space-y-1.5">
                  <div className="h-4 w-40 bg-muted rounded animate-pulse" />
                  <div className="h-9 w-full bg-muted rounded animate-pulse" />
                </div>
              ))}
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {tab === "advanced" && (
        <div className="flex items-start gap-2 border border-yellow-300/60 bg-yellow-50 dark:bg-yellow-950/30 rounded-md p-3">
          <AlertTriangle className="h-4 w-4 text-yellow-600 dark:text-yellow-400 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-yellow-800 dark:text-yellow-200">
            以下为开发者/运维调参项，默认值已是最佳实践，改错可能导致链路异常。
          </div>
        </div>
      )}

      {Object.entries(grouped).map(([group, entries]) => {
        const onTest = testForGroup(group);
        const allAdvanced = entries.every((e) => e.level === "advanced");
        return (
          <SectionCard
            key={group}
            group={group}
            entries={entries}
            config={config}
            setField={setField}
            onTest={onTest}
            showAdvancedBadge={allAdvanced && tab !== "advanced"}
          />
        );
      })}

      <div className="flex justify-end gap-3 sticky bottom-0 bg-background/95 backdrop-blur py-3 border-t">
        <Button
          variant="outline"
          onClick={handleReset}
          disabled={!isDirty || saving}
        >
          重置
        </Button>
        <Button onClick={handleSave} disabled={!isDirty || saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          保存
        </Button>
      </div>
    </div>
  );
}

function SectionCard({
  group,
  entries,
  config,
  setField,
  onTest,
  showAdvancedBadge,
}: {
  group: string;
  entries: FieldSpecEntry[];
  config: ConfigDict;
  setField: (k: string, v: unknown) => void;
  onTest?: () => void;
  showAdvancedBadge?: boolean;
}) {
  // 按 subgroup 聚合：无 subgroup 的平铺，有 subgroup 的分组
  const plain = entries.filter((e) => !e.subgroup);
  const subgroups = useMemo(() => {
    const out: Record<string, FieldSpecEntry[]> = {};
    for (const e of entries) {
      if (!e.subgroup) continue;
      (out[e.subgroup] ??= []).push(e);
    }
    return out;
  }, [entries]);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base flex items-center gap-2">
          {group}
          {showAdvancedBadge && (
            <span className="text-xs font-normal text-muted-foreground border rounded px-1.5 py-0.5">
              进阶
            </span>
          )}
        </CardTitle>
        {onTest && (
          <Button variant="outline" size="sm" onClick={onTest}>
            <FlaskConical className="h-3.5 w-3.5" />测试
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {plain.map((entry) => (
          <FieldRow
            key={entry.key}
            entry={entry}
            value={config[entry.key]}
            setField={setField}
          />
        ))}
        {Object.entries(subgroups).map(([sub, subEntries]) => (
          <div
            key={sub}
            className="space-y-3 pl-4 border-l-2 border-muted"
          >
            <h4 className="text-sm font-medium text-muted-foreground">{sub}</h4>
            {subEntries.map((entry) => (
              <FieldRow
                key={entry.key}
                entry={entry}
                value={config[entry.key]}
                setField={setField}
              />
            ))}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function FieldRow({
  entry,
  value,
  setField,
}: {
  entry: FieldSpecEntry;
  value: unknown;
  setField: (k: string, v: unknown) => void;
}) {
  const isSecret =
    entry.key.includes("api_key") || entry.key === "telegram_bot_token";
  const [showSecret, setShowSecret] = useState(false);

  // bool_toggle 用 Switch 横向卡片布局（对齐 seiri）
  if (entry.control === "toggle" && entry.bool_toggle) {
    return (
      <div className="flex flex-row items-center justify-between rounded-lg border p-4">
        <div className="space-y-0.5 pr-4">
          <div className="flex items-center gap-1.5">
            <Label className="text-sm font-medium">{labelOf(entry)}</Label>
            {entry.help && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Info className="h-3.5 w-3.5 text-muted-foreground cursor-help" />
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">{entry.help}</TooltipContent>
              </Tooltip>
            )}
          </div>
          {entry.default_hint && (
            <div className="text-xs text-muted-foreground">{entry.default_hint}</div>
          )}
        </div>
        <Switch
          checked={Boolean(value)}
          onCheckedChange={(v) => setField(entry.key, v)}
        />
      </div>
    );
  }

  return (
    <div className="grid gap-1.5">
      <div className="flex items-center gap-1.5">
        <Label className="text-sm">{labelOf(entry)}</Label>
        {entry.help && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Info className="h-3.5 w-3.5 text-muted-foreground cursor-help" />
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{entry.help}</TooltipContent>
          </Tooltip>
        )}
        {entry.default_hint && (
          <span className="text-xs text-muted-foreground ml-auto">
            {entry.default_hint}
          </span>
        )}
      </div>
      <Control
        entry={entry}
        value={value}
        setField={setField}
        isSecret={isSecret}
        showSecret={showSecret}
        setShowSecret={setShowSecret}
      />
    </div>
  );
}

function Control({
  entry,
  value,
  setField,
  isSecret,
  showSecret,
  setShowSecret,
}: {
  entry: FieldSpecEntry;
  value: unknown;
  setField: (k: string, v: unknown) => void;
  isSecret: boolean;
  showSecret: boolean;
  setShowSecret: (v: boolean) => void;
}) {
  const { control, key: k, options } = entry;

  if (control === "toggle" || control === "select") {
    const opts = options ?? [];
    const cur = typeof value === "string" ? value : opts[0];
    return (
      <Select value={cur} onValueChange={(v) => setField(k, v)}>
        <SelectTrigger className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {opts.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  if (control === "number") {
    return (
      <Input
        type="number"
        value={typeof value === "number" ? value : ""}
        min={entry.min}
        max={entry.max}
        step={entry.step}
        onChange={(e) =>
          setField(k, e.target.value === "" ? null : Number(e.target.value))
        }
      />
    );
  }

  // secret 单独处理：GET /config 返回脱敏星号，前端永远拿不到明文。
  // 脱敏值（全星号）时显示占位 + 清空按钮（方便输入新值）；用户输入新明文后正常 password 框。
  if (isSecret) {
    const strVal = typeof value === "string" ? value : "";
    const isMasked = strVal.length > 0 && [...strVal].every((c) => c === "*");
    if (isMasked) {
      return (
        <div className="flex gap-2">
          <Input
            type="text"
            value={strVal}
            readOnly
            placeholder=""
            className="text-muted-foreground"
            title="已保存（出于安全，明文不下发到前端）"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setField(k, "")}
            title="清空后输入新密钥覆盖"
          >
            修改
          </Button>
        </div>
      );
    }
    return (
      <div className="flex gap-2">
        <Input
          type={showSecret ? "text" : "password"}
          value={strVal}
          onChange={(e) => setField(k, e.target.value)}
          placeholder={strVal === "" ? "输入新密钥" : ""}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setShowSecret(!showSecret)}
        >
          {showSecret ? "隐藏" : "显示"}
        </Button>
      </div>
    );
  }

  // path：input + 文件选择按钮（复用 PathBrowserDialog）
  if (control === "path") {
    return (
      <PathField
        entry={entry}
        value={value}
        setField={setField}
      />
    );
  }

  // input
  return (
    <Input
      type="text"
      value={typeof value === "string" ? value : ""}
      onChange={(e) => setField(k, e.target.value)}
    />
  );
}

function PathField({
  entry,
  value,
  setField,
}: {
  entry: FieldSpecEntry;
  value: unknown;
  setField: (k: string, v: unknown) => void;
}) {
  const [open, setOpen] = useState(false);
  const selectMode = entry.select_mode === "file" ? "file" : "directory";
  const strVal = typeof value === "string" ? value : "";
  return (
    <div className="flex gap-2">
      <Input
        type="text"
        value={strVal}
        onChange={(e) => setField(entry.key, e.target.value)}
        placeholder={selectMode === "file" ? "可执行文件路径或文件名" : "目录路径"}
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        title={selectMode === "file" ? "选择文件" : "选择目录"}
      >
        <FolderOpen className="h-3.5 w-3.5" />
        选择
      </Button>
      <PathBrowserDialog
        open={open}
        onOpenChange={setOpen}
        initialPath={strVal}
        selectMode={selectMode}
        title={selectMode === "file" ? "选择可执行文件" : "选择目录"}
        onConfirm={(p) => setField(entry.key, p)}
      />
    </div>
  );
}

// 配置 key → 中文标签
// 注意：label 的单一来源在后端 field-spec 的 `label` 字段（由 CN_MAP 注入）。
// 此字典仅作过渡回退（后端漏 label 时兜底），新字段应直接在后端 CN_MAP 维护，无需改这里。
const CN_LABELS: Record<string, string> = {
  api_key: "TMDB API 密钥",
  tv_path: "电视剧路径",
  movie_path: "电影路径",
  anime_path: "动漫路径",
  anime_movie_path: "动漫电影路径",
  mode: "重命名模式",
  overwrite_existing: "目标已存在策略",
  hardlink_fallback_to_symlink: "硬链接失败降级软链接",
  docker_mnt: "Docker 挂载路径",
  host_path_prefix: "宿主机路径前缀",
  ai_api_key: "OpenAI API 密钥",
  ai_base_url: "OpenAI API 地址",
  ai_model: "OpenAI 模型",
  ai_temperature: "OpenAI 温度",
  ai_auto_save: "自动保存 AI 分析",
  ai_confidence_threshold: "AI 置信度阈值",
  openai_output_format: "OpenAI 输出格式",
  openai_api_interface: "OpenAI 接口类型",
  subtitle_auto_fetch_enabled: "启用字幕自动抓取",
  subtitle_auto_fetch_preferred_language: "优先字幕语言",
  subtitle_auto_fetch_provider: "字幕抓取源",
  subtitle_auto_fetch_candidate_limit: "抓取候选上限",
  subtitle_auto_fetch_timeout_seconds: "抓取超时秒数",
  subtitle_auto_fetch_browser_enabled: "启用动态浏览器抓取",
  subtitle_auto_fetch_acgrip_base_url: "ACGRIP 地址",
  subtitle_auto_fetch_use_ai_rerank: "启用 AI 重排",
  subtitle_auto_fetch_search_mode: "字幕搜索模式",
  subtitle_auto_fetch_save_reason: "保存重排原因",
  subtitle_sync_enabled: "启用字幕自动对齐",
  subtitle_sync_mode: "字幕对齐模式",
  subtitle_sync_executable: "ffsubsync 可执行文件",
  subtitle_sync_extra_args: "ffsubsync 额外参数",
  subtitle_sync_timeout_seconds: "对齐超时秒数",
  subtitle_sync_overwrite_policy: "字幕覆盖策略",
  subtitle_case_agent_primary_enabled: "启用字幕 Case Agent",
  emby_enabled: "启用 Emby 通知",
  emby_host: "Emby 服务器地址",
  emby_api_key: "Emby API 密钥",
  telegram_enabled: "启用 Telegram 通知",
  telegram_bot_token: "Telegram Bot Token",
  telegram_chat_id: "Telegram Chat ID",
  telegram_notify_on_success: "成功时通知",
  telegram_notify_on_failure: "失败时通知",
  telegram_base_url: "Telegram API 地址",
  skip_tags: "跳过标签（逗号分隔）",
  rename_bgm_to_tmdb_product_pipeline_enabled: "启用 BGM→TMDB 产品链路",
  rename_bgm_to_tmdb_execute_enabled: "执行 BGM→TMDB 迁移",
  log_level: "日志等级",
  queue_max_workers: "队列并行数",
};

function labelOf(entry: FieldSpecEntry): string {
  // 优先后端下发的 label；回退本地字典（过渡）；再回退 key
  return entry.label ?? CN_LABELS[entry.key] ?? entry.key;
}
