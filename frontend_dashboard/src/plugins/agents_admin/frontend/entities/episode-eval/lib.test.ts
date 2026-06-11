import { describe, expect, it } from "vitest";

import { episodeUnitKey, isFlaggedEpisode } from "./lib";

describe("episodeUnitKey", () => {
  it("compone <session>::<episode>", () => {
    expect(episodeUnitKey({ session_id: "wa_x", episode_id: "ep_011" })).toBe(
      "wa_x::ep_011",
    );
  });
});

describe("isFlaggedEpisode", () => {
  const T = 0.7;

  it("marca score bajo el umbral", () => {
    expect(isFlaggedEpisode({ last_avg: 0.43, is_candidate: false }, T)).toBe(true);
  });

  it("marca candidato aunque el avg pase el umbral (falla crítica: ep_010)", () => {
    // ep_010: avg 0.72 ≥ 0.70 pero inventó un color → no_hallucination=0 → candidato.
    expect(isFlaggedEpisode({ last_avg: 0.72, is_candidate: true }, T)).toBe(true);
  });

  it("NO marca un happy path con una métrica de tono apenas baja (ep_011)", () => {
    // ep_011: avg 0.963, role_adherence 0.667 → antes marcaba "falla" (ruido).
    expect(isFlaggedEpisode({ last_avg: 0.963, is_candidate: false }, T)).toBe(false);
  });

  it("trata el límite como NO bajo el umbral (avg == umbral)", () => {
    expect(isFlaggedEpisode({ last_avg: 0.7, is_candidate: false }, T)).toBe(false);
  });

  it("no marca cuando no hay avg ni candidatura", () => {
    expect(isFlaggedEpisode({ last_avg: null, is_candidate: false }, T)).toBe(false);
  });
});
