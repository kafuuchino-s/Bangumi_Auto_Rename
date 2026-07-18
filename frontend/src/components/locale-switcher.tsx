import { useTranslation } from "react-i18next";
import { useLocale } from "@/i18n/use-locale";

export function LocaleSwitcher() {
  const { t } = useTranslation("navigation");
  const { preference, setPreference } = useLocale();
  return (
    <label className="flex items-center gap-2 text-xs text-muted-foreground" title={t("language")}>
      <span className="sr-only">{t("language")}</span>
      <select
        aria-label={t("language")}
        value={preference}
        onChange={(event) => setPreference(event.target.value as typeof preference)}
        className="h-8 rounded-md border bg-background px-2 text-xs text-foreground"
      >
        <option value="system">{t("localeSystem")}</option>
        <option value="zh-CN">{t("localeZh")}</option>
        <option value="en-US">{t("localeEn")}</option>
      </select>
    </label>
  );
}
