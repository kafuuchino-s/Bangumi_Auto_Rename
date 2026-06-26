// 后端 API 类型定义（对齐 src/api/serializers.py 与路由响应）

export interface TaskRow {
  id: number;
  path: string;
  name: string | null;
  uuid: string;
  season: string | number | null;
  status: string;
  failure_reason_label: string;
  queue_status: string;
  is_anime: boolean | string;
  is_movie: boolean | string;
  ai_used: string;
}

export interface TaskDetail {
  found: boolean;
  uuid: string;
  basic?: {
    path: string;
    name: string;
    season_id: string | number | null;
    tmdb_media_type: string;
    tmdb_media_type_label: string;
    tmdb_name: string;
    tmdb_year: string | number | null;
    tmdb_id: string | number | null;
  };
  failure?: {
    reason: string;
    reason_label: string;
    error: string;
  };
  ai?: {
    ai_used: boolean;
    ai_attempted: boolean;
    pipeline_mode: string;
    pipeline_mode_label: string;
  };
  case_agent?: {
    status: string;
    status_label: string;
    product_result_kind: string;
  };
  landing?: {
    target_dir: string;
    mapping_count: number;
    mappings: unknown[];
  };
  subtitle_fetch?: {
    status: string;
    status_label: string;
    case_agent_status: string;
    case_agent_status_label: string;
    provider: string;
    failure_reason: string;
    missing_video_count: number;
    matched_count: number;
    unmatched_count: number;
    no_target_count: number;
    selections_count: number;
  };
  bangumi_subjects?: {
    id: number | string;
    name: string;
    name_cn: string;
    media_kind: string;
    assignment_count: number | string;
    episode_ranges: string;
  }[];
  tmdb_subjects?: {
    tmdb_ref: string;
    tmdb_id: number | string;
    media_type: string;
    name: string;
    year: number | string;
    episode_ranges: string;
  }[];
  mapping_details?: {
    source_name: string;
    source_path: string;
    bgm: string;
    tmdb: string;
    confidence: string;
    disposition: string;
  }[];
  total_size?: string;
}

export interface SubtitleRow {
  id: number;
  archive: string;
  archive_path: string;
  matched_task: string;
  matched_count: string;
  sync: string;
  status: string;
  uuid: string;
}

export interface DashboardStats {
  running: number;
  pending: number;
  today_success: number;
  today_failed: number;
  today_total: number;
  success_rate: number | null;
}

export interface ConfigDict {
  [key: string]: unknown;
}

export interface FieldSpecEntry {
  key: string;
  control: string;
  level: "basic" | "advanced";
  group: string;
  tab?: string;
  subgroup?: string;
  label?: string;
  select_mode?: "file" | "directory";
  options?: string[];
  min?: number;
  max?: number;
  step?: number;
  help?: string;
  default_hint?: string;
  bool_toggle?: boolean;
}

export interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface BrowseResult {
  current: string;
  parent: string | null;
  items: FileItem[];
  total?: number;
  page?: number;
  limit?: number;
  total_pages?: number;
}

export interface DrivesResult {
  drives: string[];
  system: string;
}

export interface LogTailResult {
  lines: string[];
  count: number;
  file: string;
}
