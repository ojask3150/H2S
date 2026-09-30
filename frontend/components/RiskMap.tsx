"use client";

import { useEffect } from "react";
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Polyline,
  Popup,
  Tooltip,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import type { Scene } from "@/lib/types";
import {
  COLORS,
  assetAtRisk,
  assetColor,
  roadColor,
  vulnById,
  pathwayById,
} from "@/lib/risk";

function centroid(scene: Scene): [number, number] {
  const pts = scene.meteorology.trajectory;
  const lat = pts.reduce((s, p) => s + p.lat, 0) / pts.length;
  const lon = pts.reduce((s, p) => s + p.lon, 0) / pts.length;
  return [lat, lon];
}

// Refit the viewport whenever the storm changes (historical storms make
// landfall at different points along the coast).
function FitBounds({ scene }: { scene: Scene }) {
  const map = useMap();
  useEffect(() => {
    const pts: [number, number][] = scene.meteorology.trajectory.map((p) => [p.lat, p.lon]);
    for (const a of scene.layers.infrastructure) {
      if (a.geom?.type === "Point") {
        const [lon, lat] = a.geom.coordinates as number[];
        pts.push([lat, lon]);
      }
    }
    if (pts.length) {
      map.fitBounds(L.latLngBounds(pts).pad(0.25), { animate: true });
    }
  }, [map, scene]);
  return null;
}

export default function RiskMap({ scene }: { scene: Scene }) {
  const vuln = vulnById(scene.assessment);
  const paths = pathwayById(scene.assessment);
  const center = centroid(scene);

  const track: [number, number][] = scene.meteorology.trajectory.map((p) => [
    p.lat,
    p.lon,
  ]);

  return (
    <MapContainer center={center} zoom={9} scrollWheelZoom>
      <FitBounds scene={scene} />
      <TileLayer
        attribution="&copy; OpenStreetMap contributors, &copy; CARTO"
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
      />

      {/* Storm trajectory */}
      <Polyline
        positions={track}
        pathOptions={{ color: COLORS.track, weight: 3, dashArray: "6 6" }}
      />
      {scene.meteorology.trajectory.map((p, i) => (
        <CircleMarker
          key={`t${i}`}
          center={[p.lat, p.lon]}
          radius={p.status === "current" ? 7 : 5}
          pathOptions={{ color: COLORS.track, fillColor: COLORS.track, fillOpacity: 0.9 }}
        >
          <Tooltip>{`${p.status ?? "point"} · ${p.timestamp_utc}`}</Tooltip>
        </CircleMarker>
      ))}

      {/* Arterial roads */}
      {scene.layers.arterial_roads.map((r) => {
        if (!r.geom || r.geom.type !== "LineString") return null;
        const coords = (r.geom.coordinates as number[][]).map(
          ([lon, lat]) => [lat, lon] as [number, number]
        );
        const p = paths[r.id];
        const color = p ? roadColor(p) : COLORS.muted;
        return (
          <Polyline key={r.id} positions={coords} pathOptions={{ color, weight: 5 }}>
            <Popup>
              <strong>{r.id}</strong> ({r.type})<br />
              DEM {r.dem_elevation_m}m · {p?.designation ?? "—"}
              <br />
              {p?.note}
            </Popup>
          </Polyline>
        );
      })}

      {/* Infrastructure */}
      {scene.layers.infrastructure.map((a) => {
        if (!a.geom || a.geom.type !== "Point") return null;
        const [lon, lat] = a.geom.coordinates as number[];
        const v = vuln[a.id];
        const risk = assetAtRisk(v);
        const color = assetColor(a.type, risk);
        return (
          <CircleMarker
            key={a.id}
            center={[lat, lon]}
            radius={a.type === "medical_shelter" ? 9 : 7}
            pathOptions={{ color, fillColor: color, fillOpacity: 0.85 }}
          >
            <Popup>
              <strong>{a.id}</strong> ({a.type})<br />
              elev {a.elevation_m}m · surge {v?.expected_surge_m}m<br />
              {v?.anticipated_failure_window}
              <br />
              {v?.failure_mode}
            </Popup>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}
