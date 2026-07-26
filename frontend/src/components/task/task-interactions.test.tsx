import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CreateTaskWizard } from "./create-task-wizard";
import { TaskDetailDialog } from "./task-detail-dialog";

const api = vi.hoisted(() => ({
  createTask: vi.fn(),
  deleteTask: vi.fn(),
  getTaskDetail: vi.fn(),
  retryTask: vi.fn(),
}));

const taskStore = vi.hoisted(() => ({ fetchTasks: vi.fn() }));
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));

vi.mock("@/lib/api/client", () => api);
vi.mock("@/store/task-store", () => ({
  useTaskStore: () => ({ fetchTasks: taskStore.fetchTasks }),
}));
vi.mock("@/components/settings/path-browser-dialog", () => ({
  PathBrowserDialog: () => null,
}));
vi.mock("@/i18n/use-locale", () => ({
  useLocale: () => ({ locale: "zh-CN" }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("sonner", () => ({ toast }));

describe("task interactions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("enqueues a trimmed path and remembers its parent directory", async () => {
    api.createTask.mockResolvedValue({});
    const onOpenChange = vi.fn();
    const onCreated = vi.fn();

    render(
      <CreateTaskWizard
        open
        onOpenChange={onOpenChange}
        onCreated={onCreated}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "path" }), {
      target: { value: "  C:\\Media\\Series\\Episode 01.mkv  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "nextStep" }));
    fireEvent.click(screen.getByRole("button", { name: "confirmEnqueue" }));

    await waitFor(() =>
      expect(api.createTask).toHaveBeenCalledWith({
        path: "C:\\Media\\Series\\Episode 01.mkv",
      }),
    );
    expect(window.localStorage.getItem("bar_last_task_dir")).toBe(
      "C:\\Media\\Series",
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onCreated).toHaveBeenCalledOnce();
    expect(toast.success).toHaveBeenCalledWith("taskEnqueued");
  });

  it("retries a loaded task, closes the dialog, and refreshes tasks", async () => {
    api.getTaskDetail.mockResolvedValue({
      found: true,
      uuid: "task-123",
      basic: {
        path: "C:\\Media\\Series",
        name: "Example Series",
        season_id: 1,
        tmdb_media_type: "tv",
        tmdb_name: "Example Series",
        tmdb_year: 2025,
        tmdb_id: 42,
      },
    });
    api.retryTask.mockResolvedValue({});
    taskStore.fetchTasks.mockResolvedValue(undefined);
    const onOpenChange = vi.fn();

    render(
      <TaskDetailDialog
        uuid="task-123"
        open
        onOpenChange={onOpenChange}
      />,
    );

    expect(
      await screen.findByRole("heading", { name: "Example Series" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "retry" }));

    await waitFor(() => expect(api.retryTask).toHaveBeenCalledWith("task-123"));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(taskStore.fetchTasks).toHaveBeenCalledOnce();
    expect(toast.success).toHaveBeenCalledWith("taskEnqueued");
  });
});
