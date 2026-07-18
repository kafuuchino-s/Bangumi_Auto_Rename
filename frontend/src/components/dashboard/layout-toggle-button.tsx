"use client";

import { LayoutGrid, LayoutList } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useLayoutStore } from "@/store/layout-store";
import { useTranslation } from "react-i18next";

export function LayoutToggleButton() {
  const { layoutVariant, toggleLayout } = useLayoutStore();
  const { t } = useTranslation("navigation");
  const isMinimal = layoutVariant === "minimal";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleLayout}
          title={isMinimal ? t("layoutCompact") : t("layoutMinimal")}
          aria-label={isMinimal ? t("layoutCompact") : t("layoutMinimal")}
        >
          {isMinimal ? (
            <LayoutList className="h-4 w-4" />
          ) : (
            <LayoutGrid className="h-4 w-4" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {isMinimal ? t("layoutCompactHint") : t("layoutMinimalHint")}
      </TooltipContent>
    </Tooltip>
  );
}
