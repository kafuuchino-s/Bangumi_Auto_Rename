"use client";

import { LayoutGrid, LayoutList } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useLayoutStore } from "@/store/layout-store";

export function LayoutToggleButton() {
  const { layoutVariant, toggleLayout } = useLayoutStore();
  const isMinimal = layoutVariant === "minimal";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleLayout}
          title={isMinimal ? "切换为紧凑布局" : "切换为极简布局"}
        >
          {isMinimal ? (
            <LayoutList className="h-4 w-4" />
          ) : (
            <LayoutGrid className="h-4 w-4" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {isMinimal ? "紧凑布局（显示标题）" : "极简布局（仅数字）"}
      </TooltipContent>
    </Tooltip>
  );
}
