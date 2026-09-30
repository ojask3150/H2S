// Types mirroring the FastAPI /scene payload.

export interface Geom {
  type: "Point" | "LineString";
  coordinates: number[] | number[][];
}

export interface InfrastructureAsset {
  id: string;
  type: "substation" | "power_feeder" | "medical_shelter" | "other";
  elevation_m: number | null;
  serves?: string[] | null;
  capacity_beds?: number | null;
  geom?: Geom | null;
}

export interface ArterialRoad {
  id: string;
  type?: string | null;
  dem_elevation_m: number | null;
  geom?: Geom | null;
}

export interface GeeLayers {
  dem: { id: string; vertical_datum: string; resolution_m: number | null };
  infrastructure: InfrastructureAsset[];
  arterial_roads: ArterialRoad[];
  mode: "live" | "static";
}

export interface TrackPoint {
  lat: number;
  lon: number;
  timestamp_utc: string;
  status?: string | null;
}

export interface Meteorology {
  storm_id: string;
  source: string;
  trajectory: TrackPoint[];
  eta_landfall_hours: number | null;
  sustained_wind_kmh: number;
  wind_gust_kmh: number | null;
  predicted_rainfall_mm_48h: number | null;
  storm_surge: {
    peak_height_m: number | null;
    confidence_interval_m: number[] | null;
  };
}

export interface VulnerabilityItem {
  asset_id: string;
  asset_type: string;
  asset_elevation_m: number | null;
  expected_surge_m: number;
  inundation_margin_m: number | null;
  failure_mode: string;
  anticipated_failure_window: string;
  basis: string;
}

export interface TriggerStatus {
  trigger_id: string;
  predefined_threshold: string;
  observed_value: number | null;
  status: "MET" | "NOT_MET" | "CONDITIONAL_MET" | "NOT_EVALUABLE";
  liquidity_action: string;
  verification_source: string;
}

export interface EvacuationPathway {
  road_id: string;
  dem_elevation_m: number | null;
  projected_first_flood_time: string | null;
  flood_depth_m: number;
  designation: "FLOOD_RISK" | "SAFE_ROUTE";
  clearance_priority: number | null;
  note: string;
}

export interface Advisory {
  priority_rank: number;
  recipient: string;
  lead_time_hours: number;
  directive: string;
  linked_assessment_ref: string;
}

export interface Assessment {
  vulnerability_assessment: VulnerabilityItem[];
  parametric_triggers: TriggerStatus[];
  evacuation_pathways: EvacuationPathway[];
  automated_advisories: Advisory[];
}

export interface ContractTrigger {
  trigger_id: string;
  status: "READY_TO_EXECUTE" | "STAGED_PENDING_CONFIRMATION";
  tranche: string;
  threshold: string;
  observed_value: number | null;
}

export interface DispatchBundle {
  storm_ref: string;
  sms: { recipient: string; priority_rank: number; body: string }[];
  email: { recipient: string; priority_rank: number; subject: string }[];
  contract_triggers: ContractTrigger[];
  summary: Record<string, number>;
}

export interface StormMeta {
  name?: string;
  year?: number | null;
  category?: string;
  basin?: string;
  status?: "LIVE" | "HISTORICAL" | "DEMO";
  landfall_place?: string;
  landfall_date?: string;
  landfall_lat?: number;
  landfall_lon?: number;
  peak_wind_kmh?: number | null;
  deaths?: number | null;
  damage_usd_bn?: number | null;
  source?: string;
  summary?: string;
}

export interface StormSummary extends StormMeta {
  id: string;
  storm_id?: string;
  sustained_wind_kmh?: number;
  surge_peak_m?: number | null;
  eta_landfall_hours?: number | null;
}

export interface LiveStatus {
  active: boolean;
  storm_id: string | null;
  detail: string;
}

export interface StormCatalog {
  live: LiveStatus;
  storms: StormSummary[];
}

export interface Scene {
  storm_id: string;
  storm_meta: StormMeta;
  layers: GeeLayers;
  meteorology: Meteorology;
  assessment: Assessment;
  dispatch: DispatchBundle;
  live?: LiveStatus;
  catalog?: StormSummary[];
}
