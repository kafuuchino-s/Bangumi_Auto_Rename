import { createContext } from "react";
import type { Locale, LocalePreference } from "./locale";

export interface LocaleContextValue {
  preference: LocalePreference;
  locale: Locale;
  setPreference: (preference: LocalePreference) => void;
}

export const LocaleContext = createContext<LocaleContextValue | null>(null);
