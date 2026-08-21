import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SettingsTabContent } from "./settings-tab-content";
import type { FieldSpecEntry } from "@/lib/api/types";

const api = vi.hoisted(() => ({
  discoverModels: vi.fn(),
  getConfig: vi.fn(),
  getFieldSpec: vi.fn(),
  testAi: vi.fn(),
  testEmby: vi.fn(),
  testTelegram: vi.fn(),
  updateConfig: vi.fn(),
}));

const toast = vi.hoisted(() => ({
  error: vi.fn(),
  info: vi.fn(),
  success: vi.fn(),
}));

vi.mock("@/lib/api/client", () => api);
vi.mock("./path-browser-dialog", () => ({ PathBrowserDialog: () => null }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("sonner", () => ({ toast }));

const fieldSpec: FieldSpecEntry[] = [
  {
    key: "ai_base_url",
    control: "text",
    level: "basic",
    group: "ai_recognition",
    tab: "ai",
  },
  {
    key: "ai_api_key",
    control: "text",
    level: "basic",
    group: "ai_recognition",
    tab: "ai",
  },
  {
    key: "openai_api_interface",
    control: "text",
    level: "basic",
    group: "ai_recognition",
    tab: "ai",
  },
  {
    key: "ai_model",
    control: "text",
    level: "basic",
    group: "ai_recognition",
    tab: "ai",
  },
];

describe("settings interactions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getConfig.mockResolvedValue({
      ai_base_url: "https://api.example.test/v1",
      ai_api_key: "*********",
      openai_api_interface: "responses_api",
      ai_model: "old-model",
    });
    api.getFieldSpec.mockResolvedValue(fieldSpec);
    api.discoverModels.mockResolvedValue({ models: ["model-a", "model-b"] });
    api.updateConfig.mockResolvedValue({});
  });

  it("discovers models from the current config and saves an edited model", async () => {
    render(<SettingsTabContent tab="ai" />);

    await waitFor(() =>
      expect(api.discoverModels).toHaveBeenCalledWith({
        base_url: "https://api.example.test/v1",
        api_key: "*********",
        api_interface: "responses_api",
      }),
    );
    expect(await screen.findByText("modelFetchCount")).toBeInTheDocument();
    expect(
      document.querySelector('option[value="model-a"]'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTitle("fetchModels"));
    await waitFor(() => expect(api.discoverModels).toHaveBeenCalledTimes(2));

    fireEvent.change(screen.getByDisplayValue("old-model"), {
      target: { value: "model-b" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));

    await waitFor(() =>
      expect(api.updateConfig).toHaveBeenCalledWith({
        ai_base_url: "https://api.example.test/v1",
        ai_api_key: "*********",
        openai_api_interface: "responses_api",
        ai_model: "model-b",
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("saveSuccess");
  });

  it("keeps selected title languages in priority order when saving", async () => {
    api.getConfig.mockResolvedValue({
      rename_output_title_language_order: ["zh-CN"],
    });
    api.getFieldSpec.mockResolvedValue([
      {
        key: "rename_output_title_language_order",
        control: "ordered_select",
        level: "basic",
        group: "rename_titles",
        tab: "general",
        options: ["auto", "zh-CN", "zh-TW", "en-US", "original"],
      },
    ]);

    render(<SettingsTabContent tab="general" />);

    const titleLanguageTrigger = await screen.findByRole("button", {
      name: "option.zh-CN",
    });
    fireEvent.keyDown(titleLanguageTrigger, {
      key: "Enter",
      code: "Enter",
      charCode: 13,
    });
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "option.en-US" }),
    );
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "save" }));

    await waitFor(() =>
      expect(api.updateConfig).toHaveBeenCalledWith({
        rename_output_title_language_order: ["zh-CN", "en-US"],
      }),
    );
  });
});
