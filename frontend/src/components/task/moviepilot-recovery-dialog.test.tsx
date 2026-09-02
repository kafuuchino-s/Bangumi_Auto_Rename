import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MoviePilotRecoveryDialog } from "./moviepilot-recovery-dialog";

const api = vi.hoisted(() => ({
  enqueueMoviePilotRecovery: vi.fn(),
  getMoviePilotRecovery: vi.fn(),
}));
const taskStore = vi.hoisted(() => ({ fetchTasks: vi.fn() }));
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));

vi.mock("@/lib/api/client", () => api);
vi.mock("@/store/task-store", () => ({
  useTaskStore: () => ({ fetchTasks: taskStore.fetchTasks }),
}));
vi.mock("@/i18n/use-locale", () => ({
  useLocale: () => ({ locale: "zh-CN" }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("sonner", () => ({ toast }));

const report = {
  items: [
    {
      history_id: 9,
      source_path: "H:/Anime/Title",
      local_path: "/media/Anime/Title",
      title: "Recoverable Title",
      year: "2026",
      media_type: "电视剧",
      tmdb_id: 42,
      seasons: "S01",
      episodes: "E01-E12",
      download_hash: "abc123",
      torrent_name: "Title S01",
      torrent_site: "Site",
      downloaded_at: "2026-09-02 12:00:00",
      status: "recoverable" as const,
      completion_state: "completed" as const,
    },
    {
      history_id: 8,
      source_path: "H:/Anime/Done",
      local_path: "/media/Anime/Done",
      title: "Processed Title",
      year: "2025",
      media_type: "电视剧",
      tmdb_id: 41,
      seasons: "S01",
      episodes: "E01-E12",
      download_hash: "done",
      torrent_name: "Done S01",
      torrent_site: "Site",
      downloaded_at: "2026-09-01 12:00:00",
      status: "processed" as const,
      completion_state: "unknown" as const,
    },
  ],
  summary: {
    history_count: 2,
    deduplicated_count: 2,
    shown_count: 2,
    recoverable_count: 1,
    processed_count: 1,
    queued_count: 0,
    downloading_count: 0,
    unavailable_count: 0,
  },
  warnings: {},
};

describe("MoviePilot recovery dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getMoviePilotRecovery.mockResolvedValue(report);
    api.enqueueMoviePilotRecovery.mockResolvedValue({
      task_id: "task-1",
      history_id: 9,
    });
    taskStore.fetchTasks.mockResolvedValue(undefined);
  });

  it("shows only recoverable rows and confirms server-side enqueue", async () => {
    render(<MoviePilotRecoveryDialog open onOpenChange={vi.fn()} />);

    expect(await screen.findByText("Recoverable Title")).toBeInTheDocument();
    expect(screen.queryByText("Processed Title")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "moviepilotRecoveryEnqueue" }),
    );
    expect(
      await screen.findByRole("heading", {
        name: "moviepilotRecoveryConfirmTitle",
      }),
    ).toBeInTheDocument();
    const confirmButtons = screen.getAllByRole("button", {
      name: "moviepilotRecoveryEnqueue",
    });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() =>
      expect(api.enqueueMoviePilotRecovery).toHaveBeenCalledWith(9),
    );
    expect(taskStore.fetchTasks).toHaveBeenCalledOnce();
    expect(toast.success).toHaveBeenCalledWith("moviepilotRecoveryEnqueued");
  });
});
