"use client";

import { create } from "zustand";

export type DashboardLayoutVariant = "compact" | "minimal";

interface LayoutState {
  layoutVariant: DashboardLayoutVariant;
  setLayoutVariant: (v: DashboardLayoutVariant) => void;
  toggleLayout: () => void;
}

export const useLayoutStore = create<LayoutState>((set) => ({
  layoutVariant: "compact",
  setLayoutVariant: (v) => set({ layoutVariant: v }),
  toggleLayout: () =>
    set((s) => ({
      layoutVariant: s.layoutVariant === "compact" ? "minimal" : "compact",
    })),
}));
