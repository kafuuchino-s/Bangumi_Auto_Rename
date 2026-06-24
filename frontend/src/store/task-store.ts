"use client";

import { create } from "zustand";
import {
  getTasks,
  retryTask,
  deleteTask,
  refetchSubtitle,
} from "@/lib/api/client";
import type { TaskRow } from "@/lib/api/types";

export type ViewMode = "table" | "card";

export interface TaskFilters {
  search: string;
  status: string[]; // 选中的状态值（成功/失败/处理中/等待处理）
  type: string[]; // 选中的类型（是/否/自动 对应 is_anime/is_movie）
}

interface TaskState {
  tasks: TaskRow[];
  loading: boolean;
  error: string | null;
  filters: TaskFilters;
  viewMode: ViewMode;
  selected: Set<string>;
  setFilters: (f: Partial<TaskFilters>) => void;
  resetFilters: () => void;
  setViewMode: (v: ViewMode) => void;
  toggleSelected: (uuid: string) => void;
  selectAll: (uuids: string[]) => void;
  clearSelected: () => void;
  fetchTasks: () => Promise<void>;
  retry: (uuid: string) => Promise<void>;
  remove: (uuid: string) => Promise<void>;
  refetchSub: (uuid: string) => Promise<void>;
  batchRetry: (uuids: string[]) => Promise<void>;
  batchRemove: (uuids: string[]) => Promise<void>;
}

const EMPTY_FILTERS: TaskFilters = { search: "", status: [], type: [] };

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: [],
  loading: false,
  error: null,
  filters: { ...EMPTY_FILTERS },
  viewMode: "table",
  selected: new Set<string>(),

  setFilters: (f) =>
    set((s) => ({ filters: { ...s.filters, ...f } })),
  resetFilters: () => set({ filters: { ...EMPTY_FILTERS } }),
  setViewMode: (v) => set({ viewMode: v }),
  toggleSelected: (uuid) =>
    set((s) => {
      const next = new Set(s.selected);
      if (next.has(uuid)) next.delete(uuid);
      else next.add(uuid);
      return { selected: next };
    }),
  selectAll: (uuids) => set({ selected: new Set(uuids) }),
  clearSelected: () => set({ selected: new Set<string>() }),

  fetchTasks: async () => {
    set({ loading: true, error: null });
    try {
      const tasks = await getTasks();
      set({ tasks, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },
  retry: async (uuid) => {
    await retryTask(uuid);
    await getTasks().then((tasks) => set({ tasks }));
  },
  remove: async (uuid) => {
    await deleteTask(uuid);
    set((s) => ({ tasks: s.tasks.filter((t) => t.uuid !== uuid) }));
  },
  refetchSub: async (uuid) => {
    await refetchSubtitle(uuid);
  },
  batchRetry: async (uuids) => {
    await Promise.all(uuids.map((u) => retryTask(u)));
    await getTasks().then((tasks) => set({ tasks }));
    get().clearSelected();
  },
  batchRemove: async (uuids) => {
    await Promise.all(uuids.map((u) => deleteTask(u)));
    set((s) => ({
      tasks: s.tasks.filter((t) => !uuids.includes(t.uuid)),
      selected: new Set<string>(),
    }));
  },
}));
