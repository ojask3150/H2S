"use client";

import type { StormSummary, LiveStatus } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  LIVE: "Live",
  DEMO: "Demo",
  HISTORICAL: "Historical",
};

function StormButton({
  storm,
  active,
  onSelect,
}: {
  storm: StormSummary;
  active: boolean;
  onSelect: (id: string) => void;
}) {
  const wind = storm.peak_wind_kmh ?? storm.sustained_wind_kmh;
  return (
    <button
      className={`storm-item${active ? " active" : ""}`}
      onClick={() => onSelect(storm.id)}
    >
      <div className="storm-item-head">
        <span className="storm-name">{storm.name ?? storm.storm_id ?? storm.id}</span>
        {storm.year ? <span className="storm-year">{storm.year}</span> : null}
      </div>
      <div className="storm-item-sub">
        {storm.landfall_place ?? storm.basin ?? ""}
        {wind ? ` · ${wind} km/h` : ""}
      </div>
    </button>
  );
}

export default function StormPicker({
  storms,
  selected,
  live,
  onSelect,
}: {
  storms: StormSummary[];
  selected: string;
  live?: LiveStatus;
  onSelect: (id: string) => void;
}) {
  const groups: Record<string, StormSummary[]> = { LIVE: [], DEMO: [], HISTORICAL: [] };
  for (const s of storms) {
    (groups[s.status ?? "HISTORICAL"] ??= []).push(s);
  }

  return (
    <aside className="rail">
      <div className="rail-header">
        <span className="brand-dot" />
        <div>
          <div className="brand-title">Anticipatory Action</div>
          <div className="brand-sub">Bay of Bengal · cyclone risk</div>
        </div>
      </div>

      <div className={`live-status ${live?.active ? "on" : "off"}`}>
        <span className="live-dot" />
        {live?.active ? "Live feed active" : "No active live system"}
      </div>

      <div className="rail-scroll">
        {(["LIVE", "DEMO", "HISTORICAL"] as const).map((g) =>
          groups[g].length ? (
            <div className="rail-group" key={g}>
              <div className="rail-group-title">{STATUS_LABEL[g]}</div>
              {groups[g].map((s) => (
                <StormButton
                  key={s.id}
                  storm={s}
                  active={s.id === selected}
                  onSelect={onSelect}
                />
              ))}
            </div>
          ) : null
        )}
      </div>
    </aside>
  );
}
