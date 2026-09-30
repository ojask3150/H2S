"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { fetchScene, fetchStorms } from "@/lib/api";
import type { Scene, StormSummary, LiveStatus } from "@/lib/types";
import Panels from "@/components/Panels";
import StormPicker from "@/components/StormPicker";

// Leaflet touches window; load the map only on the client.
const RiskMap = dynamic(() => import("@/components/RiskMap"), {
  ssr: false,
  loading: () => <div className="loading">Loading map…</div>,
});

function Legend() {
  return (
    <div className="legend">
      <div className="item">
        <span className="dot" style={{ background: "#ff5d6c" }} /> Asset at surge risk
      </div>
      <div className="item">
        <span className="dot" style={{ background: "#43d391" }} /> Safe shelter / route
      </div>
      <div className="item">
        <span className="dot" style={{ background: "#4aa8ff" }} /> Infrastructure
      </div>
      <div className="item">
        <span className="line" style={{ background: "#ff5d6c" }} /> Flood-risk road
      </div>
      <div className="item">
        <span className="line" style={{ background: "#ff8a3d" }} /> Storm track
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
    </div>
  );
}

function StormInfo({ scene }: { scene: Scene }) {
  const meta = scene.storm_meta;
  const fmtNum = (n?: number | null) =>
    n == null ? null : n >= 1000 ? n.toLocaleString("en-US") : String(n);
  return (
    <div className="storm-info">
      {meta.summary ? <p className="storm-summary">{meta.summary}</p> : null}
      <div className="storm-facts">
        {meta.landfall_place ? (
          <div className="fact">
            <span>Landfall</span>
            <strong>{meta.landfall_place}</strong>
          </div>
        ) : null}
        {meta.landfall_date ? (
          <div className="fact">
            <span>Date</span>
            <strong>{meta.landfall_date}</strong>
          </div>
        ) : null}
        {meta.deaths != null ? (
          <div className="fact">
            <span>Fatalities</span>
            <strong>{fmtNum(meta.deaths)}</strong>
          </div>
        ) : null}
        {meta.damage_usd_bn != null ? (
          <div className="fact">
            <span>Damage</span>
            <strong>${meta.damage_usd_bn}B</strong>
          </div>
        ) : null}
      </div>
      {meta.source ? <div className="storm-source">Track: {meta.source}</div> : null}
    </div>
  );
}

export default function Page() {
  const [storms, setStorms] = useState<StormSummary[]>([]);
  const [live, setLive] = useState<LiveStatus | undefined>();
  const [selected, setSelected] = useState<string>("demo");
  const [scene, setScene] = useState<Scene | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Initial catalog: pick the live storm if one is active, else the demo.
  useEffect(() => {
    fetchStorms()
      .then((cat) => {
        setStorms(cat.storms);
        setLive(cat.live);
        const preferred = cat.storms.find((s) => s.status === "LIVE")?.id ?? "demo";
        setSelected(preferred);
      })
      .catch((e) => setError(String(e)));
  }, []);

  // Scene for the selected storm.
  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    fetchScene(selected)
      .then((s) => {
        setScene(s);
        if (s.catalog) setStorms(s.catalog);
        if (s.live) setLive(s.live);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [selected]);

  if (error) {
    return (
      <div className="app-fallback">
        <div className="error">
          {error}
          <br />
          Is the FastAPI backend running on :8000?
        </div>
      </div>
    );
  }

  const m = scene?.meteorology;
  const meta = scene?.storm_meta;

  return (
    <div className="app">
      <StormPicker storms={storms} selected={selected} live={live} onSelect={setSelected} />

      <main className="main">
        <header className="topbar">
          <div className="topbar-title">
            <h1>
              {meta?.name ?? m?.storm_id ?? "—"}
              {meta?.status ? <span className={`chip ${meta.status}`}>{meta.status}</span> : null}
            </h1>
            <div className="topbar-cat">{meta?.category ?? m?.source ?? ""}</div>
          </div>
          <div className="topbar-metrics">
            <Metric label="Sustained" value={m ? `${m.sustained_wind_kmh} km/h` : "—"} />
            <Metric label="Gust" value={m?.wind_gust_kmh ? `${m.wind_gust_kmh} km/h` : "—"} />
            <Metric
              label="Surge peak"
              value={m?.storm_surge.peak_height_m ? `${m.storm_surge.peak_height_m} m` : "—"}
            />
            <Metric
              label="ETA landfall"
              value={m?.eta_landfall_hours != null ? `${m.eta_landfall_hours} h` : "—"}
            />
            <Metric label="Rain 48h" value={m?.predicted_rainfall_mm_48h ? `${m.predicted_rainfall_mm_48h} mm` : "—"} />
          </div>
        </header>

        {!scene ? (
          <div className="loading">Loading assessment…</div>
        ) : (
          <div className="layout">
            <div className="map-wrap">
              {loading ? <div className="map-loading">Updating…</div> : null}
              <RiskMap scene={scene} />
              <Legend />
            </div>
            <Panels scene={scene}>
              <section className="card">
                <h2>Storm profile</h2>
                <div className="body">
                  <StormInfo scene={scene} />
                </div>
              </section>
            </Panels>
          </div>
        )}
      </main>
    </div>
  );
}
