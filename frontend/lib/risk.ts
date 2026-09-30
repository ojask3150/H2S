import type { Assessment, VulnerabilityItem, EvacuationPathway } from "./types";

export const COLORS = {
  danger: "#ff5d6c",
  warn: "#ffb454",
  ok: "#43d391",
  cond: "#c78bff",
  muted: "#93a1bd",
  accent: "#4aa8ff",
  track: "#ff8a3d",
};

// An infrastructure asset is at surge risk when its vulnerability item has a
// negative inundation margin (surge peak above the asset elevation).
export function assetAtRisk(v: VulnerabilityItem | undefined): boolean {
  return !!v && v.inundation_margin_m != null && v.inundation_margin_m < 0;
}

export function assetColor(type: string, atRisk: boolean): string {
  if (atRisk) return COLORS.danger;
  if (type === "medical_shelter") return COLORS.ok;
  return COLORS.accent;
}

export function roadColor(p: EvacuationPathway): string {
  return p.designation === "FLOOD_RISK" ? COLORS.danger : COLORS.ok;
}

export function vulnById(a: Assessment): Record<string, VulnerabilityItem> {
  return Object.fromEntries(a.vulnerability_assessment.map((v) => [v.asset_id, v]));
}

export function pathwayById(a: Assessment): Record<string, EvacuationPathway> {
  return Object.fromEntries(a.evacuation_pathways.map((p) => [p.road_id, p]));
}
