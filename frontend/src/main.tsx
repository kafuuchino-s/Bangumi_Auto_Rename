import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { RootLayout } from "@/app/layout";
import DashboardLayout from "@/app/(dashboard)/layout";
import TaskListPage from "@/app/(dashboard)/page";
import LogsPage from "@/app/(dashboard)/logs/page";
import SubtitlesPage from "@/app/(dashboard)/subtitles/page";
import SettingsLayout from "@/app/(dashboard)/settings/layout";
import GeneralSettingsPage from "@/app/(dashboard)/settings/general/page";
import AiSettingsPage from "@/app/(dashboard)/settings/ai/page";
import SubtitleSettingsPage from "@/app/(dashboard)/settings/subtitle/page";
import NotifySettingsPage from "@/app/(dashboard)/settings/notify/page";
import AdvancedSettingsPage from "@/app/(dashboard)/settings/advanced/page";
import { NotFoundPage } from "@/app/not-found";
import { I18nProvider } from "@/i18n/provider";
import { initI18n } from "@/i18n";
import "@/app/globals.css";

const router = createBrowserRouter([
  {
    element: <RootLayout />,
    children: [{
      element: <DashboardLayout />,
      children: [
        { path: "/", element: <TaskListPage /> },
        { path: "/logs", element: <LogsPage /> },
        { path: "/subtitles", element: <SubtitlesPage /> },
        {
          path: "/settings",
          element: <SettingsLayout />,
          children: [
            { index: true, element: <Navigate to="general" replace /> },
            { path: "general", element: <GeneralSettingsPage /> },
            { path: "ai", element: <AiSettingsPage /> },
            { path: "subtitle", element: <SubtitleSettingsPage /> },
            { path: "notify", element: <NotifySettingsPage /> },
            { path: "advanced", element: <AdvancedSettingsPage /> },
          ],
        },
        { path: "*", element: <NotFoundPage /> },
      ],
    }],
  },
]);

async function bootstrap() {
  const { preference } = await initI18n();
  document.getElementById("boot-splash")?.remove();
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <I18nProvider initialPreference={preference}>
        <RouterProvider router={router} />
      </I18nProvider>
    </React.StrictMode>,
  );
}

void bootstrap();
