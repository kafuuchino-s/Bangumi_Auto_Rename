import { i18n } from "@/i18n";
import { ApiClientError } from "./client";

export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return i18n.t(`errors:${error.code}`, { defaultValue: error.message });
  }
  return error instanceof Error ? error.message : i18n.t("errors:unknown");
}
