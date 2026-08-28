import { describe, expect, it } from "vitest";
import { getTaskMediaType } from "./task-media-type";

describe("getTaskMediaType", () => {
  it.each([
    [{ is_anime: true, is_movie: true }, "animeMovie"],
    [{ is_anime: true, is_movie: false }, "anime"],
    [{ is_anime: false, is_movie: true }, "movie"],
    [{ is_anime: false, is_movie: false }, "other"],
    [{ is_anime: null, is_movie: null }, "other"],
  ] as const)("classifies %o as %s", (task, expected) => {
    expect(getTaskMediaType(task)).toBe(expected);
  });
});
