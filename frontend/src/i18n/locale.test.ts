import { describe, expect, it } from "vitest";
import {
  LOCALE_STORAGE_KEY,
  localeFromLanguages,
  readLocalePreference,
  resolveLocale,
  writeLocalePreference,
} from "./locale";

describe("locale preference", () => {
  it("uses explicit preference before browser languages", () => {
    expect(resolveLocale("en-US", ["zh-CN"])).toBe("en-US");
    expect(resolveLocale("system", ["en-GB", "zh-CN"])).toBe("en-US");
  });

  it("falls back to zh-CN for unknown languages", () => {
    expect(localeFromLanguages(["fr-FR"])).toBe("zh-CN");
    expect(resolveLocale("system", [])).toBe("zh-CN");
  });

  it("survives storage errors and persists the versioned key", () => {
    const throwing = {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
    };
    expect(readLocalePreference(throwing)).toBe("system");
    expect(() => writeLocalePreference("en-US", throwing)).not.toThrow();
    const values = new Map<string, string>();
    writeLocalePreference("en-US", { setItem: (key, value) => values.set(key, value) });
    expect(values.get(LOCALE_STORAGE_KEY)).toBe("en-US");
  });
});
