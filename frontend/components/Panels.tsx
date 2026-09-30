import type { Scene } from "@/lib/types";

export default function Panels({
  scene,
  children,
}: {
  scene: Scene;
  children?: React.ReactNode;
}) {
  const a = scene.assessment;
  const d = scene.dispatch;

  return (
    <div className="panels">
      {children}
      <section className="card">
        <h2>Vulnerability Assessment</h2>
        <div className="body">
          {a.vulnerability_assessment.map((v) => {
            const risk = v.inundation_margin_m != null && v.inundation_margin_m < 0;
            return (
              <div className="row" key={v.asset_id}>
                <div className="head">
                  <span className="title">
                    {v.asset_id} · {v.asset_type}
                  </span>
                  <span className={`badge ${risk ? "FLOOD_RISK" : "SAFE_ROUTE"}`}>
                    {risk ? "AT RISK" : "CLEAR"}
                  </span>
                </div>
                <div className="detail">
                  Elevation {v.asset_elevation_m ?? "—"}m · surge {v.expected_surge_m}m ·
                  window <strong>{v.anticipated_failure_window}</strong>
                  <br />
                  {v.failure_mode}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="card">
        <h2>Parametric Triggers</h2>
        <div className="body">
          {a.parametric_triggers.map((t) => (
            <div className="row" key={t.trigger_id}>
              <div className="head">
                <span className="title">{t.trigger_id}</span>
                <span className={`badge ${t.status}`}>{t.status.replace(/_/g, " ")}</span>
              </div>
              <div className="detail">
                {t.predefined_threshold} · observed {t.observed_value ?? "—"}
                <br />
                {t.liquidity_action}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Evacuation Pathways</h2>
        <div className="body">
          {a.evacuation_pathways.map((p) => (
            <div className="row" key={p.road_id}>
              <div className="head">
                <span className="title">
                  {p.clearance_priority ? `#${p.clearance_priority} ` : ""}
                  {p.road_id}
                </span>
                <span className={`badge ${p.designation}`}>
                  {p.designation.replace(/_/g, " ")}
                </span>
              </div>
              <div className="detail">
                DEM {p.dem_elevation_m ?? "—"}m · depth {p.flood_depth_m}m ·{" "}
                {p.projected_first_flood_time ?? "—"}
                <br />
                {p.note}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Automated Advisories</h2>
        <div className="body">
          {a.automated_advisories.map((adv) => (
            <div className="row" key={adv.priority_rank}>
              <div className="head">
                <span className="title">{adv.recipient}</span>
                <span className="badge rank">
                  P{adv.priority_rank} · T-{adv.lead_time_hours}h
                </span>
              </div>
              <div className="detail">{adv.directive}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Dispatch — Contract Triggers</h2>
        <div className="body">
          {d.contract_triggers.length === 0 && (
            <div className="pill-note">No triggers met — nothing staged.</div>
          )}
          {d.contract_triggers.map((c) => (
            <div className="row" key={c.trigger_id}>
              <div className="head">
                <span className="title">
                  {c.trigger_id} · {c.tranche}
                </span>
                <span className={`badge ${c.status}`}>{c.status.replace(/_/g, " ")}</span>
              </div>
              <div className="detail">
                {c.threshold} · observed {c.observed_value ?? "—"}
              </div>
            </div>
          ))}
          <div className="pill-note">
            {d.sms.length} SMS and {d.email.length} email templates staged (not
            transmitted).
          </div>
        </div>
      </section>
    </div>
  );
}
