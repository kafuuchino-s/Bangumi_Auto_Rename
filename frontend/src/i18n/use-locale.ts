import { useContext } from "react";
import { LocaleContext } from "./locale-context";
import type { LocaleContextValue } from "./locale-context";

export function useLocale(): LocaleContextValue {
  const value = useContext(LocaleContext);
  if (!value) throw new Error("useLocale must be used inside I18nProvider");
  return value;
}
