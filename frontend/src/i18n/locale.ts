export const LOCALE_STORAGE_KEY = "bar.locale.v1";

export const SUPPORTED_LOCALES = ["zh-CN", "en-US"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export type LocalePreference = "system" | Locale;

function defaultStorage(): Storage | undefined {
  try {
    return typeof window === "undefined" ? undefined : window.localStorage;
  } catch {
    return undefined;
  }
}

export function readLocalePreference(
  storage: Pick<Storage, "getItem"> | undefined =
    defaultStorage(),
): LocalePreference {
  try {
    const value = storage?.getItem(LOCALE_STORAGE_KEY);
    return value === "zh-CN" || value === "en-US" || value === "system"
      ? value
      : "system";
  } catch {
    return "system";
  }
}

export function writeLocalePreference(
  preference: LocalePreference,
  storage: Pick<Storage, "setItem"> | undefined =
    defaultStorage(),
): void {
  try {
    storage?.setItem(LOCALE_STORAGE_KEY, preference);
  } catch {
    // Private browsing and locked-down webviews may reject storage writes.
  }
}

export function localeFromLanguages(languages: readonly string[] | undefined): Locale {
  for (const language of languages ?? []) {
    const normalized = language.toLowerCase();
    if (normalized === "en" || normalized.startsWith("en-")) return "en-US";
    if (normalized === "zh" || normalized.startsWith("zh-")) return "zh-CN";
  }
  return "zh-CN";
}

export function resolveLocale(
  preference: LocalePreference,
  languages: readonly string[] =
    typeof navigator === "undefined"
      ? []
      : navigator.languages?.length
        ? navigator.languages
        : [navigator.language],
): Locale {
  return preference === "system" ? localeFromLanguages(languages) : preference;
}
