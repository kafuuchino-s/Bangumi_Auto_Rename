"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, FlaskConical, Loader2, RefreshCw, Save } from "lucide-react";
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
import { FolderOpen } from "lucide-react";
import { PathBrowserDialog } from "./path-browser-dialog";
import {
  getConfig,
  updateConfig,
  discoverModels,
  getFieldSpec,
  testAi,
  testEmby,
  testTelegram,
} from "@/lib/api/client";
import type { ConfigDict, FieldSpecEntry } from "@/lib/api/types";
import { toast } from "sonner";
import { apiErrorMessage } from "@/lib/api/errors";

export function SettingsTabContent({ tab }: { tab: string }) {
  const { t } = useTranslation("settings");
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
      toast.success(t("saveSuccess"));
    } catch (e) {
      toast.error(t("saveFailed", { message: apiErrorMessage(e) }));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setConfig(initialConfig);
  };

  const runTest = async (name: string, fn: () => Promise<unknown>) => {
    toast.info(t("testing", { name }));
    try {
      const r = (await fn()) as { success?: boolean; message?: string };
      if (r.success) toast.success(t("testSuccess", { name, message: r.message ?? t("test") }));
      else toast.error(t("testFailed", { name, message: r.message ?? t("test") }));
    } catch (e) {
      toast.error(t("testFailed", { name, message: apiErrorMessage(e) }));
    }
  };

  const testForGroup = (group: string) => {
    if (group === "ai_recognition") return () => runTest("AI", testAi);
    if (group === "notify_emby") return () => runTest("Emby", testEmby);
    if (group === "notify_telegram") return () => runTest("Telegram", testTelegram);
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
            {t("advancedWarning")}
          </div>
        </div>
      )}

      {Object.entries(grouped).map(([group, entries]) => {
        const onTest = testForGroup(group);
        const allAdvanced = entries.every((e) => e.level === "advanced");
        return (
          <SectionCard
            key={group}
            groupLabel={t(`group.${group}`, { defaultValue: group })}
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
          {t("reset", { ns: "common" })}
        </Button>
        <Button onClick={handleSave} disabled={!isDirty || saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {t("save", { ns: "common" })}
        </Button>
      </div>
    </div>
  );
}

function SectionCard({
  groupLabel,
  entries,
  config,
  setField,
  onTest,
  showAdvancedBadge,
}: {
  groupLabel: string;
  entries: FieldSpecEntry[];
  config: ConfigDict;
  setField: (k: string, v: unknown) => void;
  onTest?: () => void;
  showAdvancedBadge?: boolean;
}) {
  const { t } = useTranslation("settings");
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
          {groupLabel}
          {showAdvancedBadge && (
            <span className="text-xs font-normal text-muted-foreground border rounded px-1.5 py-0.5">
              {t("advancedBadge")}
            </span>
          )}
        </CardTitle>
        {onTest && (
            <Button variant="outline" size="sm" onClick={onTest}>
            <FlaskConical className="h-3.5 w-3.5" />{t("test")}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {plain.map((entry) => (
          <FieldRow
            key={entry.key}
            entry={entry}
            value={config[entry.key]}
            config={config}
            setField={setField}
          />
        ))}
        {Object.entries(subgroups).map(([sub, subEntries]) => (
          <div
            key={sub}
            className="space-y-3 pl-4 border-l-2 border-muted"
          >
            <h4 className="text-sm font-medium text-muted-foreground">
              {t(`subgroup.${sub}`, { defaultValue: sub })}
            </h4>
            {subEntries.map((entry) => (
              <FieldRow
                key={entry.key}
                entry={entry}
                value={config[entry.key]}
                config={config}
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
  config,
  setField,
}: {
  entry: FieldSpecEntry;
  value: unknown;
  config: ConfigDict;
  setField: (k: string, v: unknown) => void;
}) {
  const { t } = useTranslation("settings");
  const isSecret =
    entry.key.includes("api_key") || entry.key === "telegram_bot_token";
  const [showSecret, setShowSecret] = useState(false);

  // bool_toggle 用 Switch 横向卡片布局（对齐 seiri）
  if (entry.control === "toggle" && entry.bool_toggle) {
    return (
      <div className="flex flex-row items-center justify-between rounded-lg border p-4">
        <div className="space-y-0.5 pr-4">
          <div className="flex items-center gap-1.5">
            <Label className="text-sm font-medium">
              {t(`field.${entry.key}.label`, { defaultValue: entry.key })}
            </Label>
          </div>
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
        <Label className="text-sm">
          {t(`field.${entry.key}.label`, { defaultValue: entry.key })}
        </Label>
      </div>
      <Control
        entry={entry}
        value={value}
        config={config}
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
  config,
  setField,
  isSecret,
  showSecret,
  setShowSecret,
}: {
  entry: FieldSpecEntry;
  value: unknown;
  config: ConfigDict;
  setField: (k: string, v: unknown) => void;
  isSecret: boolean;
  showSecret: boolean;
  setShowSecret: (v: boolean) => void;
}) {
  const { t } = useTranslation("settings");
  const { control, key: k, options } = entry;

  if (k === "ai_model") {
    return (
      <ModelField
        value={value}
        config={config}
        setField={setField}
      />
    );
  }

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
              {t(`option.${o}`, { defaultValue: o })}
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
            title={t("savedSecretTitle")}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setField(k, "")}
            title={t("clearSecretTitle")}
          >
            {t("changeSecret")}
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
          placeholder={strVal === "" ? t("newSecretPlaceholder") : ""}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setShowSecret(!showSecret)}
        >
          {showSecret ? t("hide") : t("show")}
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

function ModelField({
  value,
  config,
  setField,
}: {
  value: unknown;
  config: ConfigDict;
  setField: (k: string, v: unknown) => void;
}) {
  const { t } = useTranslation("settings");
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const autoFetchStarted = useRef(false);
  const currentValue = typeof value === "string" ? value : "";
  const baseUrl = typeof config.ai_base_url === "string" ? config.ai_base_url : "";
  const apiKey = typeof config.ai_api_key === "string" ? config.ai_api_key : "";
  const apiInterface =
    typeof config.openai_api_interface === "string"
      ? config.openai_api_interface
      : "responses_api";

  const fetchModels = useCallback(
    async (silent = false) => {
      if (!baseUrl.trim() || !apiKey.trim()) {
        const message = t("modelFetchMissingConfig");
        setError(message);
        if (!silent) toast.error(message);
        return;
      }

      setLoading(true);
      setError("");
      try {
        const result = await discoverModels({
          base_url: baseUrl,
          api_key: apiKey,
          api_interface: apiInterface,
        });
        setModels(result.models);
        if (!silent) {
          toast.success(t("modelFetchSuccess", { count: result.models.length }));
        }
        if (result.models.length === 0) {
          setError(t("modelFetchEmpty"));
        }
      } catch (err) {
        const message = apiErrorMessage(err);
        setError(message);
        if (!silent) {
          toast.error(t("modelFetchFailed", { message }));
        }
      } finally {
        setLoading(false);
      }
    },
    [apiInterface, apiKey, baseUrl, t],
  );

  useEffect(() => {
    if (autoFetchStarted.current) return;
    autoFetchStarted.current = true;
    if (!baseUrl.trim() || !apiKey.trim()) return;
    void fetchModels(true);
  }, [baseUrl, apiKey, fetchModels]);

  return (
    <div className="space-y-1.5">
      <div className="flex gap-2">
        <Input
          list="ai-model-options"
          type="text"
          value={currentValue}
          onChange={(e) => setField("ai_model", e.target.value)}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void fetchModels()}
          disabled={loading}
          title={t("fetchModels")}
        >
          <RefreshCw className={loading ? "animate-spin" : undefined} />
          {loading ? t("fetchingModels") : t("fetchModels")}
        </Button>
      </div>
      <datalist id="ai-model-options">
        {models.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>
      {models.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {t("modelFetchCount", { count: models.length })}
        </p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
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
  const { t } = useTranslation("settings");
  const [open, setOpen] = useState(false);
  const selectMode = entry.select_mode === "file" ? "file" : "directory";
  const strVal = typeof value === "string" ? value : "";
  return (
    <div className="flex gap-2">
      <Input
        type="text"
        value={strVal}
        onChange={(e) => setField(entry.key, e.target.value)}
        placeholder={selectMode === "file" ? t("filePathPlaceholder") : t("directoryPathPlaceholder")}
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        title={selectMode === "file" ? t("chooseFile") : t("chooseDirectory")}
      >
        <FolderOpen className="h-3.5 w-3.5" />
        {t("select", { ns: "common" })}
      </Button>
      <PathBrowserDialog
        open={open}
        onOpenChange={setOpen}
        initialPath={strVal}
        selectMode={selectMode}
        title={selectMode === "file" ? t("chooseFile") : t("chooseDirectory")}
        onConfirm={(p) => setField(entry.key, p)}
      />
    </div>
  );
}
