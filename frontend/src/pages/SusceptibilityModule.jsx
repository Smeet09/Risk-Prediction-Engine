import React, { useState, useEffect, useRef, forwardRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";
import useStore from "../store/useStore";
import { getRegions, getSusceptibility, getDisasters } from "../lib/api";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

gsap.registerPlugin(ScrollTrigger);

/* Fly-to control on map */
function FlyTo({ center, bounds, zoom }) {
  const map = useMap();
  useEffect(() => {
    try {
      if (center && !bounds) {
        map.setView(center, zoom || 10);
      }
    } catch (e) {
      console.warn("Map setting failed: ", e);
    }
  }, [center, bounds, map]);
  return null;
}

/* Dynamically fit camera bounds to actual loaded geojson */
function FitData({ data }) {
  const map = useMap();
  useEffect(() => {
    if (data && data.features && data.features.length > 0) {
      try {
        const layer = L.geoJSON(data);
        map.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 12 });
      } catch (e) {
        console.warn("Could not fit data bounds natively: ", e);
      }
    }
  }, [data, map]);
  return null;
}

/* 3D Floating Pane */
const FloatingPane = forwardRef(({ label, color }, ref) => {
  return (
    <div ref={ref} style={{
      background: "rgba(255,255,255,0.78)",
      backdropFilter: "blur(20px)",
      border: "1px solid rgba(0,0,0,0.06)",
      borderRadius: 16,
      padding: "18px 24px",
      display: "flex", alignItems: "center", gap: 14,
      boxShadow: "0 8px 32px rgba(0,0,0,0.07)",
      transformOrigin: "center center",
    }}>
      <div style={{ width: 10, height: 10, borderRadius: "50%", background: color, flexShrink: 0 }} />
      <span style={{ fontSize: 14, fontWeight: 600, flex: 1 }}>{label}</span>
      <span style={{ fontSize: 12, color: "#999", fontWeight: 500 }}>AHP Layer</span>
    </div>
  );
});

/* Susceptibility colour scale */
const SUSC_COLORS = {
  "Very Low": "#2dc653",
  "Low": "#80b918",
  "Moderate": "#f9c74f",
  "High": "#f3722c",
  "Very High": "#d62828",
};

function styleFeature(feature) {
  const cls = feature?.properties?.susceptibility || "Medium";
  return {
    fillColor: SUSC_COLORS[cls] || "#ccc",
    fillOpacity: 0.6,
    color: "#fff",
    weight: 1,
  };
}

export default function SusceptibilityModule() {
  const navigate = useNavigate();
  const { user, logout } = useStore();
  const [searchParams] = useSearchParams();
  const rawDist = searchParams.get("district");
  const initDist = (rawDist === "null" || !rawDist) ? "" : rawDist;

  const [regions, setRegions] = useState([]);
  const [country, setCountry] = useState(searchParams.get("country") || "");
  const [state, setState] = useState(searchParams.get("state") || "");
  const [district, setDistrict] = useState(initDist);
  const [selectedRegion, setSelectedRegion] = useState(null);
  const [mapCenter, setMapCenter] = useState(null);
  const [mapBounds, setMapBounds] = useState(null);
  const [susc, setSusc] = useState(null);
  const [disasterType, setDisasterType] = useState(searchParams.get("type") || "");
  const [disasterTypes, setDisasterTypes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const pane1 = useRef(), pane2 = useRef(), pane3 = useRef();
  const sidebarRef = useRef();

  useEffect(() => {
    getRegions().then(d => setRegions(d.countries || [])).catch(() => { });
    // Fetch active disasters instead of hardcoding
    getDisasters().then(d => {
      const active = Object.values(d.grouped || {}).flat().filter(x => x.is_active);
      const names = active.map(x => x.name.toLowerCase());
      // Just keep name as is, but we might want capitalized for the UI
      setDisasterTypes(active.map(x => x.name));
      // if UI auto-loaded from URL but the case differs, fix it:
      const pType = searchParams.get("type");
      if (pType && !disasterType) {
        const match = active.find(x => x.name.toLowerCase() === pType.toLowerCase() || x.code === pType.toLowerCase());
        if (match) setDisasterType(match.name);
      } else if (!pType && active.length > 0) {
        setDisasterType(active[0].name);
      }
    }).catch(() => { });
  }, [searchParams]);

  // 3D scroll parallax on the panes
  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.fromTo(sidebarRef.current,
        { x: 40, opacity: 0 },
        { x: 0, opacity: 1, duration: 0.7, ease: "power3.out", delay: 0.3 }
      );
      [pane1, pane2, pane3].forEach((ref, i) => {
        gsap.fromTo(ref.current,
          { y: 30 + i * 15, opacity: 0 },
          { y: 0, opacity: 1, duration: 0.6, delay: 0.2 + i * 0.15, ease: "power3.out" }
        );
      });
    });
    return () => ctx.revert();
  }, []);

  const countries = regions.map(r => r.country);
  const selectedCountryObj = regions.find(r => r.country === country);
  const states = selectedCountryObj?.states?.map(s => s.state).filter(Boolean) || [];
  const selectedStateObj = selectedCountryObj?.states?.find(s => s.state === state);
  const districts = (selectedStateObj?.districts || []).map(d => d.district).filter(Boolean);

  const handleLoadMap = async () => {
    setError(""); setLoading(true); setSusc(null);
    try {
      // Find region
      let regionId = null;
      if (state) {
        const allDistricts = selectedStateObj?.districts || [];
        const found = allDistricts.find(d => (d.district || "") === (district || ""));
        if (found) {
          regionId = found.id;
          if (found.centroid?.coordinates) {
            setMapCenter([found.centroid.coordinates[1], found.centroid.coordinates[0]]);
          }
        }
      }
      if (regionId && disasterType) {
        // Find correct disaster code (e.g. Flood -> flood)
        const code = disasterType.toLowerCase().replace(/ /g, "_");
        const res = await getSusceptibility(regionId, code);
        setSusc(res.result);
      }
    } catch (e) {
      setError("No susceptibility data found for this region.");
    } finally {
      setLoading(false);
    }
  };

  const autoLoadedRef = useRef(false);

  // Auto load map if arriving from History tracker 
  useEffect(() => {
    if (!autoLoadedRef.current && state && disasterType && regions.length > 0) {
      if (searchParams.get("state")) {
        handleLoadMap();
      }
      autoLoadedRef.current = true;
    }
  }, [district, state, disasterType, regions, searchParams]);

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "#f8f8f9" }}>
      {/* Top Bar */}
      <header style={{
        height: 64, padding: "0 32px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        background: "rgba(255,255,255,0.9)", backdropFilter: "blur(20px)",
        borderBottom: "1px solid #e5e5e7", flexShrink: 0, zIndex: 100
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button onClick={() => navigate("/hub")} style={{
            background: "none", border: "none", cursor: "pointer",
            display: "flex", alignItems: "center", gap: 8, color: "#666", fontSize: 13
          }}>
            ← Hub
          </button>
          <div style={{ width: 1, height: 20, background: "#e5e5e7" }} />
          <span style={{ fontSize: 15, fontWeight: 600 }}>Susceptibility Mapping</span>
          <span style={{
            fontSize: 11, padding: "3px 10px", borderRadius: 50,
            background: "#f0f0f1", color: "#666", fontWeight: 500
          }}>AHP Engine</span>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <select
            value={disasterType}
            onChange={e => setDisasterType(e.target.value)}
            className="input"
            style={{ width: "auto", minWidth: 140, padding: "8px 36px 8px 14px", fontSize: 13 }}
          >
            {disasterTypes.map(t => <option key={t}>{t}</option>)}
          </select>
        </div>
      </header>

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Map (left) */}
        <div style={{ flex: 1, position: "relative", zIndex: 1 }}>
          <MapContainer
            center={[20.5, 78.9]} zoom={5}
            style={{ width: "100%", height: "100%" }}
            zoomControl={true}
          >
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='© <a href="https://openstreetmap.org">OpenStreetMap</a>'
            />
            <FlyTo center={mapCenter} zoom={10} />
            {susc?.final_geojson && <FitData data={susc.final_geojson} />}
            {susc?.final_geojson && (
              <GeoJSON
                key={susc.id || `${susc.region_id || 'new'}-${disasterType}-${Date.now()}`}
                data={susc.final_geojson}
                style={styleFeature}
                onEachFeature={(feat, layer) => {
                  layer.bindTooltip(
                    `<strong>Susceptibility: ${feat.properties?.susceptibility || "N/A"}</strong>`,
                    { permanent: false, direction: "top" }
                  );
                }}
              />
            )}
          </MapContainer>

          {/* AHP Layer Panes — floating over map */}
          <div style={{
            position: "absolute", top: 16, left: 16, zIndex: 999,
            display: "flex", flexDirection: "column", gap: 10, width: 240
          }}>
            <FloatingPane ref={pane1} label="Hazard Layer" color="#d62828" />
            <FloatingPane ref={pane2} label="Exposure Layer" color="#f3722c" />
            <FloatingPane ref={pane3} label="Vulnerability Layer" color="#f9c74f" />
          </div>

          {/* Legend */}
          <div style={{
            position: "absolute", bottom: 24, left: 16, zIndex: 999,
            background: "rgba(255,255,255,0.9)", backdropFilter: "blur(20px)",
            border: "1px solid #e5e5e7", borderRadius: 16, padding: "16px 20px"
          }}>
            <p style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.08em", color: "#999", marginBottom: 10 }}>
              SUSCEPTIBILITY
            </p>
            {Object.entries(SUSC_COLORS).map(([label, col]) => (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                <div style={{ width: 12, height: 12, borderRadius: 3, background: col }} />
                <span style={{ fontSize: 12, color: "#444" }}>{label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Right Control Panel */}
        <div ref={sidebarRef} style={{
          width: 320, flexShrink: 0,
          background: "rgba(255,255,255,0.9)",
          backdropFilter: "blur(30px)",
          borderLeft: "1px solid #e5e5e7",
          padding: "28px 24px",
          overflowY: "auto",
          display: "flex", flexDirection: "column", gap: 20
        }}>
          <div>
            <h2 style={{ fontSize: 18, fontWeight: 700, letterSpacing: "-0.02em" }}>Region Selection</h2>
            <p style={{ fontSize: 13, color: "#999", marginTop: 4 }}>
              Select a region to load susceptibility data
            </p>
          </div>

          <div className="form-group">
            <label className="form-label">Country</label>
            <select className="input" value={country} onChange={e => { setCountry(e.target.value); setState(""); setDistrict(""); }}>
              <option value="">Select country…</option>
              {countries.map(c => <option key={c}>{c}</option>)}
            </select>
          </div>

          <div className="form-group">
            <label className="form-label">State</label>
            <select className="input" value={state} onChange={e => { setState(e.target.value); setDistrict(""); }} disabled={!country}>
              <option value="">Select state…</option>
              {states.map(s => <option key={s}>{s}</option>)}
            </select>
          </div>

          <div className="form-group">
            <label className="form-label">District</label>
            <select className="input" value={district} onChange={e => setDistrict(e.target.value)} disabled={!state}>
              <option value="">Select district…</option>
              {districts.map(d => <option key={d}>{d}</option>)}
            </select>
          </div>

          {error && (
            <div style={{
              background: "rgba(255,59,48,0.06)", border: "1px solid rgba(255,59,48,0.15)",
              borderRadius: 12, padding: "12px 16px", fontSize: 13, color: "#ff3b30"
            }}>{error}</div>
          )}

          <button
            className="btn btn-primary"
            onClick={handleLoadMap}
            disabled={!state || loading}
            style={{ justifyContent: "center" }}
          >
            {loading
              ? <><span style={{ display: "inline-block", width: 14, height: 14, border: "2px solid rgba(255,255,255,0.3)", borderTopColor: "#fff", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} /> Loading…</>
              : "Load Susceptibility Map"
            }
          </button>

          {susc && (
            <div style={{
              background: "rgba(52,199,89,0.06)", border: "1px solid rgba(52,199,89,0.2)",
              borderRadius: 16, padding: "16px"
            }}>
              <p style={{ fontSize: 12, fontWeight: 600, color: "#1a7a35", marginBottom: 6 }}>
                ✓ DATA LOADED
              </p>
              <p style={{ fontSize: 12, color: "#666" }}>
                Generated: {new Date(susc.generated_at).toLocaleDateString()}
              </p>
              <p style={{ fontSize: 12, color: "#666" }}>
                Type: {susc.disaster_type}
              </p>
            </div>
          )}

          <div className="divider" />

          {/* Info */}
          <div>
            <p className="t-label" style={{ marginBottom: 12 }}>About AHP Layers</p>
            {[
              { color: "#d62828", name: "Hazard", desc: "DEM-derived terrain features" },
              { color: "#f3722c", name: "Exposure", desc: "Roads, buildings, infrastructure" },
              { color: "#f9c74f", name: "Vulnerability", desc: "Soil, LULC, proximity factors" },
            ].map(l => (
              <div key={l.name} style={{ display: "flex", gap: 12, marginBottom: 14 }}>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: l.color, marginTop: 5, flexShrink: 0 }} />
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{l.name}</div>
                  <div style={{ fontSize: 12, color: "#999" }}>{l.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
