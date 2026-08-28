import type { TaskRow } from "@/lib/api/types";

export type TaskMediaType = "animeMovie" | "anime" | "movie" | "other";

export function getTaskMediaType(
  task: Pick<TaskRow, "is_anime" | "is_movie">,
): TaskMediaType {
  if (task.is_anime === true && task.is_movie === true) return "animeMovie";
  if (task.is_anime === true) return "anime";
  if (task.is_movie === true) return "movie";
  return "other";
}
