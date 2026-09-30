import type { Scene, StormCatalog } from "./types";

// Calls go through the Next.js rewrite proxy (/api/* -> FastAPI).

export async function fetchScene(storm?: string, mode?: string): Promise<Scene> {
  const params = new URLSearchParams();
  if (storm) params.set("storm", storm);
  if (mode) params.set("mode", mode);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(`/api/scene${qs}`, { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`scene fetch failed (${res.status}): ${detail}`);
  }
  return res.json();
}

export async function fetchStorms(): Promise<StormCatalog> {
  const res = await fetch(`/api/storms`, { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`storms fetch failed (${res.status}): ${detail}`);
  }
  return res.json();
}
