"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useSidebarStore } from "@/store/sidebar-store";
import { useMobile } from "@/hooks/use-mobile";
import { TopNavbar } from "@/components/layout/top-navbar";
import { SidebarContent } from "@/components/layout/sidebar-content";
import { SidebarBottomControls } from "@/components/dashboard/sidebar-bottom-controls";

// 侧栏只在任务页常驻展开；其他页（配置/日志/字幕）侧栏收起、主区全宽，
// 避免无关统计卡干扰。用户仍可手动切换（菜单钮），同页内手动操作不被覆盖。
function _sidebarDefaultOpen(pathname: string, isLgUp: boolean) {
  if (!isLgUp) return false; // 小屏一律收起（抽屉式）
  return pathname === "/"; // 桌面端仅任务页展开
}

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { isOpen, toggle, setOpen, setMobile } = useSidebarStore();
  const isMobile = useMobile();
  const pathname = usePathname();

  // 同步 isMobile 到 store
  useEffect(() => {
    setMobile(isMobile);
  }, [isMobile, setMobile]);

  // 按当前页 + 断点设置侧栏默认开合：
  // - 任务页(/) 且桌面端 → 展开
  // - 其他页 或 小屏 → 收起
  // pathname 变化时重置（跨页自动收起/展开）；同页内用户手动 toggle 不受影响。
  useEffect(() => {
    if (typeof window === "undefined") return;
    const isLgUp = window.matchMedia("(min-width: 1024px)").matches;
    setOpen(_sidebarDefaultOpen(pathname, isLgUp));
  }, [pathname, setOpen]);

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* 左侧栏：小屏抽屉（fixed + 平移），大屏静态列（宽度过渡） */}
      <aside
        className={`
          overflow-hidden flex flex-col border-r bg-background
          fixed left-0 top-0 bottom-0 z-[60] shadow-lg
          transition-transform duration-300 ease-in-out
          ${
            isOpen
              ? "translate-x-0"
              : "-translate-x-full lg:translate-x-0"
          }
          lg:static lg:top-auto lg:left-auto lg:bottom-auto lg:shadow-none
          lg:transition-[width] lg:duration-300 lg:ease-in-out
          ${
            isOpen
              ? "lg:w-80"
              : "lg:w-0 lg:border-r-0"
          }
        `}
      >
        {/* 滚动内容区 */}
        <div className="flex-1 p-6 overflow-y-auto">
          <SidebarContent />
        </div>

        {/* 固定底部按钮栏 */}
        <div className="border-t bg-background p-4 flex-shrink-0">
          <SidebarBottomControls />
        </div>
      </aside>

      {/* 移动端遮罩（覆盖全屏含顶栏） */}
      {isOpen && isMobile && (
        <div
          className="fixed inset-0 bg-black/20 z-[55] lg:hidden"
          onClick={toggle}
        />
      )}

      {/* 右侧：顶栏 + 主内容 */}
      <div className="flex flex-col flex-1 overflow-hidden min-w-0">
        <TopNavbar onToggleSidebar={toggle} />

        <main className="flex-1 overflow-hidden relative">
          <div className="h-full p-6 overflow-y-auto">{children}</div>
        </main>
      </div>
    </div>
  );
}
