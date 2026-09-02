// API client for the FastAPI /api/* endpoints. Vite proxies these paths in development.

import type {
  BrowseResult,
  ConfigDict,
  DashboardStats,
  DrivesResult,
  FieldSpecEntry,
  LogTailResult,
  ModelDiscoveryResult,
  MoviePilotRecoveryReport,
  SubtitleRow,
  TaskDetail,
  TaskRow,
  ApiErrorBody,
} from "./types";

const BASE = "/api";

export class ApiClientError extends Error {
  readonly code: string;
  readonly params: Record<string, unknown>;
  readonly status: number;

  constructor(status: number, body: Partial<ApiErrorBody> | null, fallback: string) {
    const error = body?.error;
    super(error?.message || fallback);
    this.name = "ApiClientError";
    this.status = status;
    this.code = error?.code || "unknown_error";
    this.params = error?.params || {};
  }
}

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => null) as Record<string, unknown> | null;
  if (!res.ok) {
    throw new ApiClientError(res.status, body as Partial<ApiErrorBody> | null, res.statusText);
  }
  return (body && "data" in body ? body.data : body) as T;
}

// ----------------------- 任务 ----------------------- //
export async function getTasks(): Promise<TaskRow[]> {
  const data = await json<{ tasks: TaskRow[] }>(await fetch(`${BASE}/tasks`));
  return data.tasks;
}

export async function getTaskDetail(uuid: string): Promise<TaskDetail> {
  return json<TaskDetail>(await fetch(`${BASE}/tasks/${uuid}`));
}

export async function createTask(body: {
  path: string;
  is_anime?: boolean | null;
  is_movie?: boolean | null;
}): Promise<{ task_id: string | null }> {
  return json(
    await fetch(`${BASE}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  );
}

export async function retryTask(uuid: string) {
  return json(await fetch(`${BASE}/tasks/${uuid}/retry`, { method: "POST" }));
}

export async function editTask(
  uuid: string,
  body: {
    is_anime?: boolean | null;
    name?: string | null;
    season_id?: number | null;
    is_movie?: boolean | null;
  }
) {
  return json(
    await fetch(`${BASE}/tasks/${uuid}/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  );
}

export async function deleteTask(uuid: string) {
  return json(await fetch(`${BASE}/tasks/${uuid}`, { method: "DELETE" }));
}

export async function refetchSubtitle(uuid: string) {
  return json(
    await fetch(`${BASE}/tasks/${uuid}/refetch-subtitle`, { method: "POST" })
  );
}

// ----------------------- 字幕 ----------------------- //
export async function getSubtitleTasks(): Promise<SubtitleRow[]> {
  const data = await json<{ tasks: SubtitleRow[] }>(
    await fetch(`${BASE}/subtitle/tasks`)
  );
  return data.tasks;
}

export async function importSubtitle(file: File) {
  const form = new FormData();
  form.append("file", file);
  return json(
    await fetch(`${BASE}/subtitle/import`, { method: "POST", body: form })
  );
}

export async function deleteSubtitle(uuid: string) {
  return json(await fetch(`${BASE}/subtitle/${uuid}`, { method: "DELETE" }));
}

export async function retrySubtitle(uuid: string) {
  return json(
    await fetch(`${BASE}/subtitle/${uuid}/retry`, { method: "POST" })
  );
}

// ----------------------- 配置 ----------------------- //
export async function getConfig(): Promise<ConfigDict> {
  const data = await json<{ config: ConfigDict }>(
    await fetch(`${BASE}/config`)
  );
  return data.config;
}

export async function updateConfig(config: ConfigDict) {
  return json(
    await fetch(`${BASE}/config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    })
  );
}

export async function getFieldSpec(): Promise<FieldSpecEntry[]> {
  const data = await json<{ field_spec: FieldSpecEntry[] }>(
    await fetch(`${BASE}/config/field-spec`)
  );
  return data.field_spec;
}

export async function testAi() {
  return json(await fetch(`${BASE}/config/test-ai`, { method: "POST" }));
}

export async function discoverModels(config: {
  base_url?: string;
  api_key?: string;
  api_interface?: string;
}): Promise<ModelDiscoveryResult> {
  return json<ModelDiscoveryResult>(
    await fetch(`${BASE}/config/discover-models`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    })
  );
}

export async function testEmby() {
  return json(await fetch(`${BASE}/config/test-emby`, { method: "POST" }));
}

export async function testTelegram() {
  return json(
    await fetch(`${BASE}/config/test-telegram`, { method: "POST" })
  );
}

export async function testMoviePilot() {
  return json(
    await fetch(`${BASE}/config/test-moviepilot`, { method: "POST" })
  );
}

// ----------------------- MoviePilot ----------------------- //
export async function getMoviePilotRecovery(): Promise<MoviePilotRecoveryReport> {
  return json<MoviePilotRecoveryReport>(
    await fetch(`${BASE}/moviepilot/recovery`)
  );
}

export async function enqueueMoviePilotRecovery(historyId: number) {
  return json<{ task_id: string; history_id: number }>(
    await fetch(`${BASE}/moviepilot/recovery/${historyId}/enqueue`, {
      method: "POST",
    })
  );
}

// ----------------------- 仪表盘 ----------------------- //
export async function getDashboard(): Promise<DashboardStats> {
  return json<DashboardStats>(await fetch(`${BASE}/dashboard`));
}

// SSE：仪表盘实时推送（/api/dashboard/stream）
export function getDashboardStream(): EventSource {
  return new EventSource(`${BASE}/dashboard/stream`);
}

// SSE：任务列表实时推送（/api/tasks/stream）
export function getTasksStream(): EventSource {
  return new EventSource(`${BASE}/tasks/stream`);
}

// SSE：日志实时推送（/api/logs/stream）
export function getLogsStream(): EventSource {
  return new EventSource(`${BASE}/logs/stream`);
}

// ----------------------- 日志 ----------------------- //
export async function getLogTail(n = 200): Promise<LogTailResult> {
  return json<LogTailResult>(
    await fetch(`${BASE}/logs/tail?n=${n}`)
  );
}

// ----------------------- 文件 ----------------------- //
export async function browseFiles(
  path: string,
  opts?: { search?: string; page?: number; limit?: number }
): Promise<BrowseResult> {
  const params = new URLSearchParams({ path });
  if (opts?.search) params.set("search", opts.search);
  if (opts?.page) params.set("page", String(opts.page));
  if (opts?.limit) params.set("limit", String(opts.limit));
  return json<BrowseResult>(
    await fetch(`${BASE}/files/browse?${params.toString()}`)
  );
}

export async function listDrives(): Promise<DrivesResult> {
  return json<DrivesResult>(await fetch(`${BASE}/files/drives`));
}
