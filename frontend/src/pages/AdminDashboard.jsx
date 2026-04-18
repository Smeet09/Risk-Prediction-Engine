import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import useStore from "../store/useStore";
import { getRegionsFlat, getJobs, getSusceptibilityList, syncJobs, syncIntegrity, deleteRegionData } from "../lib/api";
import DemUpload from "../components/admin/DemUpload";
import ManualDataUpload from "../components/admin/ManualDataUpload";
import SusceptibilityMapping from "../components/admin/SusceptibilityMapping";
import DisasterManager from "../components/admin/DisasterManager";
import BoundaryImporter from "../components/admin/BoundaryImporter";
import WeatherDownload from "../components/admin/WeatherDownload";

const NAV_ITEMS = [
  { id: "overview",       label: "Overview",               icon: "◈" },
  { id: "dem",            label: "DEM Upload",             icon: "⛰️" },
  { id: "manual",         label: "Manual Data (India)",    icon: "🌏" },
  { id: "weather",        label: "Weather Download",       icon: "🌤️" },
  { id: "susceptibility", label: "Susceptibility Gen.",    icon: "⚡" },
  { id: "disasters",      label: "Disaster Types",         icon: "🔧" },
];

const CONTEXT = {
  overview: {
    title: "System Overview",
    steps: [
      { n: "1", title: "Import Boundaries", desc: "One-time import of State, District, Taluka, Village boundaries from shapefiles." },
      { n: "2", title: "Upload DEM", desc: "Upload Digital Elevation Model (State or District level). Topo features + terrain classification auto-generated." },
      { n: "3", title: "Manual Data", desc: "Upload LULC, River, Soil, Fault for all India — done once, shared across all regions." },
      { n: "4", title: "Generate Map", desc: "Select region + disaster type → configure terrain weights → generate susceptibility map." },
    ]
  },
  dem: {
    title: "DEM Guidelines",
    steps: [
      { n: "✓", title: "Level", desc: "State-level or District-level DEM only. No Taluka/Village." },
      { n: "✓", title: "Format", desc: "GeoTIFF (.tif / .tiff), ASCII grid (.asc), or .img raster." },
      { n: "✓", title: "Resolution", desc: "30m or better recommended (SRTM, ALOS, CartoDEM)." },
      { n: "✓", title: "Pipeline", desc: "Auto: DEM → Slope/Aspect/TWI → 12-class terrain classification." },
    ]
  },
  manual: {
    title: "Manual Data Tips",
    steps: [
      { n: "✓", title: "LULC", desc: "Min 4 classes: Forest, Agriculture, Built-up, Water. Source: Bhuvan/NRSC." },
      { n: "✓", title: "River", desc: "Stream order attribute required for slope-flow weighting." },
      { n: "✓", title: "Soil", desc: "HWSD or FAO classification. Source: NBSS&LUP." },
      { n: "✓", title: "Fault", desc: "GSI fault lines. Distance buffer generated automatically." },
    ]
  },
  weather: {
    title: "Weather Data Tips",
    steps: [
      { n: "✓", title: "Source", desc: "Google Earth Engine via ERA5 and IMERG datasets." },
      { n: "✓", title: "Resolution", desc: "Aggregated down to 10k meters (approx 10km grid size) resampled to 2km." },
      { n: "✓", title: "Export", desc: "Tasks are submitted to Earth Engine and CSVs will appear in Google Drive." },
      { n: "✓", title: "Features", desc: "Includes Rainfall, Temp, Pressure, Wind, Soil Moisture, Runoff." },
    ]
  },
  susceptibility: {
    title: "Susceptibility Tips",
    steps: [
      { n: "✓", title: "Prerequisites", desc: "DEM + Terrain classification must be done. Manual data (India) should be uploaded." },
      { n: "✓", title: "Region", desc: "Select State → District → Taluka for layer stacking." },
      { n: "✓", title: "Weights", desc: "Default weights are disaster-specific. Customise per run." },
      { n: "✓", title: "Output", desc: "Result stored per (region, disaster). Viewable in Susceptibility Module." },
    ]
  },
  disasters: {
    title: "Disaster Manager Tips",
    steps: [
      { n: "✓", title: "Categories", desc: "Group by: Hydro-meteorological, Geological, Climatological, Biological, Other." },
      { n: "✓", title: "Hide vs Delete", desc: "Use Hide to temporarily remove from user view. Delete is permanent." },
      { n: "✓", title: "Weights", desc: "Default terrain weights can be set per disaster type." },
      { n: "✓", title: "Custom Disasters", desc: "Add new types with your own icon, color, and default weights." },
    ]
  },
};

function OverviewDashboard({ regions, jobs, onRefresh }) {
  const total    = regions.length;
  const demReady = regions.filter(r => r.dem_ready).length;
  const terrainReady = regions.filter(r => r.terrain_ready).length;
  const susc     = regions.filter(r => r.susceptibility_ready).length;

  const stats = [
    { label: "Total Regions",     value: total,       color: "#000" },
    { label: "DEM Processed",     value: demReady,    color: "#5856d6" },
    { label: "Terrain Classified",value: terrainReady,color: "#0071e3" },
    { label: "Maps Generated",    value: susc,        color: "#34c759" },
  ];

  const recentJobs = (jobs || []).slice(0, 6);

  const handleDelete = async (r) => {
    if (!window.confirm(`Are you sure you want to PERMANENTLY delete all data for ${r.district || r.state}? This will delete physical files and reset everything.`)) return;
    try {
      await deleteRegionData(r.id);
      alert("Data purged successfully.");
      onRefresh?.();
    } catch (e) {
      alert("Delete failed: " + (e.response?.data?.error || e.message));
    }
  };

  const handleSync = async () => {
    if (!window.confirm("Perform a deep sync? This will check GIS jobs and verify all physical files on disk actually exist.")) return;
    try {
      const resSync = await syncJobs();
      const resInt  = await syncIntegrity();
      alert(`Sync Complete!\n\n- Active GIS Jobs: ${resSync.active_in_gis}\n- Cleaned Dead Jobs: ${resSync.corrected_dead_jobs}\n- Desynced Regions Fixed: ${resInt.corrected_regions}`);
      onRefresh?.();
    } catch (e) {
      alert("Sync failed: " + e.message);
    }
  };

  const StatusBadge = ({ ready, label, subtitle }) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{
          width: 8, height: 8, borderRadius: "50%",
          background: ready ? "#34c759" : "#e5e5e7"
        }} />
        <span style={{ color: ready ? "#1a7a35" : "#999", fontWeight: ready ? 600 : 400 }}>
          {ready ? (label || "Ready") : "Pending"}
        </span>
      </div>
      {ready && subtitle && (
        <span style={{ fontSize: 11, color: "#666", marginLeft: 14 }}>
          {subtitle.replace(/_/g, " ")}
        </span>
      )}
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      <div>
        <h2 className="t-heading">System Overview</h2>
        <p style={{ fontSize: 14, color: "#666", marginTop: 6 }}>
          Real-time status of all data uploads and processing jobs across India.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14 }}>
        {stats.map(s => (
          <div key={s.label} style={{ background: "#fff", border: "1px solid #e5e5e7", borderRadius: 20, padding: "24px 20px" }}>
            <div style={{ fontSize: 36, fontWeight: 700, letterSpacing: "-0.04em", color: s.color }}>{s.value}</div>
            <div className="t-label" style={{ marginTop: 8 }}>{s.label}</div>
          </div>
        ))}
      </div>

      <BoundaryImporter />

      <div style={{ background: "#fff", border: "1px solid #e5e5e7", borderRadius: 20, overflow: "hidden" }}>
        <div style={{ padding: "20px 24px", borderBottom: "1px solid #e5e5e7", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ fontSize: 15, fontWeight: 700 }}>Region Data Status</h3>
          <span className="t-label">{total} regions</span>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "#fafafa" }}>
                {["Region", "State", "DEM", "Topo", "Terrain", "Manual (India)", "Susc.", "Actions"].map(h => (
                  <th key={h} style={{ padding: "12px 20px", textAlign: h === "Actions" ? "center" : "left", fontWeight: 600, fontSize: 12, color: "#666", borderBottom: "1px solid #e5e5e7", letterSpacing: "0.04em" }}>
                    {h.toUpperCase()}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {regions.map((r, i) => (
                <tr key={r.id} style={{ borderBottom: i < regions.length - 1 ? "1px solid #f0f0f0" : "none" }}>
                  <td style={{ padding: "14px 20px", fontWeight: 600 }}>{r.district || "—"}</td>
                  <td style={{ padding: "14px 20px", color: "#666" }}>{r.state}</td>
                  <td style={{ padding: "14px 20px" }}><StatusBadge ready={r.dem_ready} /></td>
                  <td style={{ padding: "14px 20px" }}><StatusBadge ready={r.topo_ready} /></td>
                  <td style={{ padding: "14px 20px" }}><StatusBadge ready={r.terrain_ready} subtitle={r.dominant_terrain} /></td>
                  <td style={{ padding: "14px 20px" }}><StatusBadge ready={r.manual_india_ready} /></td>
                  <td style={{ padding: "14px 20px" }}><StatusBadge ready={r.susceptibility_ready} /></td>
                  <td style={{ padding: "14px 20px", textAlign: "center" }}>
                    <button className="btn btn-secondary btn-sm" title="Purge Region Data" onClick={() => handleDelete(r)}>🗑️</button>
                  </td>
                </tr>
              ))}
              {regions.length === 0 && (
                <tr><td colSpan={8} style={{ padding: "32px", textAlign: "center", color: "#999" }}>No regions configured yet. Upload a DEM to start.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ background: "#fff", border: "1px solid #e5e5e7", borderRadius: 20, overflow: "hidden" }}>
        <div style={{ padding: "16px 24px", borderBottom: "1px solid #e5e5e7", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ fontSize: 15, fontWeight: 700 }}>Recent Jobs</h3>
          <button className="btn btn-secondary btn-sm" onClick={handleSync}>Verify Sync & Cleanup</button>
        </div>
        {recentJobs.length === 0 ? (
          <div style={{ padding: "32px", textAlign: "center", color: "#999", fontSize: 14 }}>No jobs yet</div>
        ) : recentJobs.map((j, i) => {
          const colors = { pending: "#999", processing: "#0071e3", done: "#34c759", failed: "#ff3b30" };
          return (
            <div key={j.id} style={{ padding: "14px 24px", display: "flex", alignItems: "center", gap: 16, borderBottom: i < recentJobs.length - 1 ? "1px solid #f0f0f0" : "none" }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: colors[j.status], flexShrink: 0 }} />
              <div style={{ flex: 1 }}>
                <span style={{ fontWeight: 600, fontSize: 14 }}>{j.district || j.state || j.country} — {j.module}</span>
                {j.disaster_type && <span style={{ fontSize: 12, color: "#666", marginLeft: 8 }}>({j.disaster_type})</span>}
              </div>
              <span className="badge" style={{ background: `${colors[j.status]}15`, color: colors[j.status], border: `1px solid ${colors[j.status]}30` }}>{j.status}</span>
              <span style={{ fontSize: 12, color: "#aaa" }}>{new Date(j.created_at).toLocaleTimeString()}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function AdminDashboard() {
  const navigate = useNavigate();
  const { user, logout } = useStore();
  const [activeModule, setActiveModule] = useState("overview");
  const [regions, setRegions]   = useState([]);
  const [jobs,    setJobs]      = useState([]);

  const fetchData = () => {
    getRegionsFlat().then(d => setRegions(d.regions || [])).catch(() => {});
    getJobs().then(d        => setJobs(d.jobs || [])).catch(()         => {});
  };

  useEffect(() => {
    fetchData();
    // Auto-poll jobs and regions every 3 seconds to keep entire UI synced with background jobs
    const iv = setInterval(fetchData, 3000);
    return () => clearInterval(iv);
  }, []);

  const ctx = CONTEXT[activeModule] || CONTEXT.overview;

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "#f8f8f9" }}>
      {/* Spinner keyframe */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>

      {/* Top Bar */}
      <header style={{
        height: 64, padding: "0 32px", flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        background: "rgba(255,255,255,0.95)", backdropFilter: "blur(20px)",
        borderBottom: "1px solid #e5e5e7", zIndex: 100
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ width: 36, height: 36, borderRadius: 10, background: "#000",
            display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
                stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <span style={{ fontWeight: 700, fontSize: 16, letterSpacing: "-0.02em" }}>Aether Admin</span>
          <div style={{ width: 1, height: 20, background: "#e5e5e7" }} />
          <span style={{ fontSize: 13, color: "#666" }}>Control Panel v2</span>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button className="btn btn-secondary btn-sm" onClick={() => navigate("/hub")}>← Hub</button>
          <div style={{ display: "flex", alignItems: "center", gap: 10,
            padding: "7px 14px", borderRadius: 50, background: "#000", color: "#fff" }}>
            <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.08em" }}>ADMIN</span>
            <span style={{ fontSize: 13 }}>{user?.name || user?.email}</span>
          </div>
          <button className="btn btn-secondary btn-sm"
            onClick={() => { logout(); navigate("/"); }}>Sign out</button>
        </div>
      </header>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "240px 1fr 280px", overflow: "hidden" }}>
        {/* LEFT SIDEBAR */}
        <div style={{
          borderRight: "1px solid #e5e5e7", background: "#fff",
          padding: "24px 16px", display: "flex", flexDirection: "column",
          gap: 4, overflowY: "auto"
        }}>
          <p className="t-label" style={{ marginBottom: 8, paddingLeft: 12 }}>MODULES</p>
          {NAV_ITEMS.map(item => (
            <button key={item.id} onClick={() => setActiveModule(item.id)} style={{
              width: "100%", textAlign: "left", padding: "12px 14px",
              borderRadius: 12, border: "none", cursor: "pointer",
              fontFamily: "inherit", fontSize: 14,
              display: "flex", alignItems: "center", gap: 12,
              background: activeModule === item.id ? "#000" : "transparent",
              color: activeModule === item.id ? "#fff" : "#444",
              fontWeight: activeModule === item.id ? 600 : 400,
              transition: "all 0.2s ease"
            }}>
              <span style={{ fontSize: 16, opacity: 0.8 }}>{item.icon}</span>
              {item.label}
            </button>
          ))}

          <div className="divider" style={{ margin: "16px 0" }} />
          <p className="t-label" style={{ marginBottom: 8, paddingLeft: 12 }}>STATUS</p>
          {[
            { label: "Regions",            value: regions.length },
            { label: "DEM Processed",      value: regions.filter(r => r.dem_ready).length },
            { label: "Terrain Classified", value: regions.filter(r => r.terrain_ready).length },
            { label: "Maps Generated",     value: regions.filter(r => r.susceptibility_ready).length },
          ].map(s => (
            <div key={s.label} style={{ padding: "8px 14px",
              display: "flex", justifyContent: "space-between", fontSize: 13 }}>
              <span style={{ color: "#666" }}>{s.label}</span>
              <span style={{ fontWeight: 700 }}>{s.value}</span>
            </div>
          ))}
        </div>

        {/* MAIN CONTENT */}
        <div style={{ overflowY: "auto", padding: "32px 36px", background: "#f8f8f9" }}>
          <div style={{ display: activeModule === "overview" ? "block" : "none" }}>
            <OverviewDashboard regions={regions} jobs={jobs} onRefresh={fetchData} />
          </div>
          <div style={{ display: activeModule === "dem" ? "block" : "none" }}>
            <DemUpload />
          </div>
          <div style={{ display: activeModule === "manual" ? "block" : "none" }}>
            <ManualDataUpload />
          </div>
          <div style={{ display: activeModule === "weather" ? "block" : "none" }}>
            <WeatherDownload regionsFlat={regions} />
          </div>
          <div style={{ display: activeModule === "susceptibility" ? "block" : "none" }}>
            <SusceptibilityMapping regionsFlat={regions} jobs={jobs} />
          </div>
          <div style={{ display: activeModule === "disasters" ? "block" : "none" }}>
            <DisasterManager />
          </div>
        </div>

        {/* RIGHT SIDEBAR — Contextual Help */}
        <div style={{
          borderLeft: "1px solid #e5e5e7", background: "#fff",
          padding: "28px 22px", overflowY: "auto"
        }}>
          <p className="t-label" style={{ marginBottom: 16 }}>INSTRUCTIONS</p>
          <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 16,
            letterSpacing: "-0.02em" }}>{ctx.title}</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {ctx.steps.map((step, i) => (
              <div key={i} style={{ display: "flex", gap: 14 }}>
                <div style={{
                  width: 26, height: 26, borderRadius: 8, flexShrink: 0,
                  background: "#f0f0f1", display: "flex", alignItems: "center",
                  justifyContent: "center", fontSize: 11, fontWeight: 700, color: "#444"
                }}>{step.n}</div>
                <div>
                  <p style={{ fontSize: 13, fontWeight: 600, marginBottom: 3 }}>{step.title}</p>
                  <p style={{ fontSize: 12, color: "#777", lineHeight: 1.55 }}>{step.desc}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="divider" style={{ margin: "20px 0" }} />
          <p className="t-label" style={{ marginBottom: 12 }}>QUICK ACTIONS</p>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {NAV_ITEMS.filter(n => n.id !== "overview" && n.id !== activeModule).map(n => (
              <button key={n.id} onClick={() => setActiveModule(n.id)} style={{
                width: "100%", textAlign: "left", padding: "11px 14px",
                borderRadius: 12, border: "1px solid #e5e5e7", background: "#fafafa",
                cursor: "pointer", fontFamily: "inherit", fontSize: 13,
                display: "flex", alignItems: "center", gap: 10, color: "#444",
                transition: "all 0.2s"
              }}>
                <span>{n.icon}</span> {n.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
