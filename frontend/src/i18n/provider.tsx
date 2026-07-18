import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { I18nextProvider, useTranslation } from "react-i18next";
import { i18n } from ".";
import {
  resolveLocale,
  writeLocalePreference,
  type Locale,
  type LocalePreference,
} from "./locale";

interface LocaleContextValue {
  preference: LocalePreference;
  locale: Locale;
  setPreference: (preference: LocalePreference) => void;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function I18nProvider({
  children,
  initialPreference,
}: {
  children: React.ReactNode;
  initialPreference: LocalePreference;
}) {
  const [preference, setPreferenceState] = useState(initialPreference);
  const [locale, setLocale] = useState<Locale>(
    (i18n.language as Locale) || resolveLocale(initialPreference),
  );
  const { t } = useTranslation("common");

  const applySystemLocale = () => {
    if (preference !== "system") return;
    const next = resolveLocale("system");
    if (next !== locale) {
      void i18n.changeLanguage(next).then(() => setLocale(next));
    }
  };

  useEffect(() => {
    const onLanguageChange = () => applySystemLocale();
    window.addEventListener("languagechange", onLanguageChange);
    return () => window.removeEventListener("languagechange", onLanguageChange);
  });

  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = i18n.t("navigation:brand");
    const description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    description?.setAttribute(
      "content",
      i18n.t("navigation:description"),
    );
  }, [locale]);

  const value = useMemo<LocaleContextValue>(
    () => ({
      preference,
      locale,
      setPreference: (next) => {
        writeLocalePreference(next);
        setPreferenceState(next);
        const resolved = resolveLocale(next);
        void i18n.changeLanguage(resolved).then(() => setLocale(resolved));
      },
    }),
    [locale, preference],
  );

  // Keep the namespace subscribed so consumers rerender after a language change.
  void t;
  return (
    <I18nextProvider i18n={i18n}>
      <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
    </I18nextProvider>
  );
}

export function useLocale(): LocaleContextValue {
  const value = useContext(LocaleContext);
  if (!value) throw new Error("useLocale must be used inside I18nProvider");
  return value;
}
