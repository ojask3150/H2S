import type { Scene, StormCatalog } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL
  ? process.env.NEXT_PUBLIC_BACKEND_URL.replace(/\/$/, "")
  : "/api";

function getApiUrl(endpoint: string): string {
  const path = endpoint.startsWith("/") ? endpoint : `/${endpoint}`;
  const apiPath = path.startsWith("/api") ? path : `/api${path}`;
  if (API_BASE.startsWith("http")) {
    return `${API_BASE}${apiPath}`;
  }
  return apiPath;
}

export async function fetchScene(storm?: string, mode?: string): Promise<Scene> {
  const params = new URLSearchParams();
  if (storm) params.set("storm", storm);
  if (mode) params.set("mode", mode);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(getApiUrl(`/scene${qs}`), { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`scene fetch failed (${res.status}): ${detail}`);
  }
  return res.json();
}

export async function fetchStorms(): Promise<StormCatalog> {
  const res = await fetch(getApiUrl("/storms"), { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`storms fetch failed (${res.status}): ${detail}`);
  }
  return res.json();
}
