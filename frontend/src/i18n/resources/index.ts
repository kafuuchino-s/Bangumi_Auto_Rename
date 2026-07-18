import { common, commonEn } from "./common";
import { navigation, navigationEn } from "./navigation";
import { tasks, tasksEn } from "./tasks";
import { subtitles, subtitlesEn } from "./subtitles";
import { settings, settingsEn } from "./settings";
import { logs, logsEn } from "./logs";
import { errors, errorsEn } from "./errors";

export const resources = {
  "zh-CN": { common, navigation, tasks, subtitles, settings, logs, errors },
  "en-US": {
    common: commonEn,
    navigation: navigationEn,
    tasks: tasksEn,
    subtitles: subtitlesEn,
    settings: settingsEn,
    logs: logsEn,
    errors: errorsEn,
  },
} as const;

export type TranslationNamespace = keyof (typeof resources)["zh-CN"];
