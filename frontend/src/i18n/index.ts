import i18next from "i18next";
import { initReactI18next } from "react-i18next";
import { readLocalePreference, resolveLocale, type LocalePreference, type Locale } from "./locale";
import { resources } from "./resources";

export { i18next as i18n };
export * from "./locale";

export interface I18nBootstrap {
  preference: LocalePreference;
  locale: Locale;
}

let initialized: Promise<I18nBootstrap> | undefined;

export function initI18n(): Promise<I18nBootstrap> {
  if (initialized) return initialized;
  initialized = (async () => {
    const preference = readLocalePreference();
    const locale = resolveLocale(preference);
    await i18next.use(initReactI18next).init({
      resources,
      lng: locale,
      fallbackLng: "zh-CN",
      supportedLngs: ["zh-CN", "en-US"],
      ns: ["common", "navigation", "tasks", "subtitles", "settings", "logs", "errors"],
      defaultNS: "common",
      interpolation: { escapeValue: false },
      returnNull: false,
    });
    return { preference, locale };
  })().catch(async (error) => {
    // A broken browser storage/resource must never block the console.
    await i18next.init({ resources, lng: "zh-CN", fallbackLng: "zh-CN" });
    return { preference: "zh-CN" as const, locale: "zh-CN" as const, error };
  });
  return initialized;
}
