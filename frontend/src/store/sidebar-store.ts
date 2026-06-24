"use client";

import { create } from "zustand";

interface SidebarState {
  isOpen: boolean;
  isMobile: boolean;
  toggle: () => void;
  close: () => void;
  setOpen: (v: boolean) => void;
  setMobile: (v: boolean) => void;
}

export const useSidebarStore = create<SidebarState>((set) => ({
  // 初始 false，由 layout 的 useEffect 按 matchMedia(min-width:1024px) 校正
  isOpen: false,
  isMobile: false,
  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
  close: () => set({ isOpen: false }),
  setOpen: (v) => set({ isOpen: v }),
  setMobile: (v) => set({ isMobile: v }),
}));
