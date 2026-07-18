import { describe, expect, it, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nProvider } from "./provider";
import { initI18n, i18n } from ".";
import { LocaleSwitcher } from "@/components/locale-switcher";
import { LOCALE_STORAGE_KEY } from "./locale";

describe("locale switcher", () => {
  beforeEach(async () => {
    window.localStorage.clear();
    await initI18n();
    await i18n.changeLanguage("zh-CN");
    document.documentElement.lang = "zh-CN";
  });

  it("persists the explicit preference and updates html.lang", async () => {
    render(<I18nProvider initialPreference="zh-CN"><LocaleSwitcher /></I18nProvider>);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "en-US" } });
    await waitFor(() => expect(document.documentElement.lang).toBe("en-US"));
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en-US");
  });
});
