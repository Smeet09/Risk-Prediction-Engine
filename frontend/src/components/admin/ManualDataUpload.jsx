import { useState, useEffect } from "react";
import { getManualDatasets, uploadManualDataset, deleteManualDataset, registerManualPath } from "../../lib/api";

const DATASET_TYPES = [
  {
    type: "lulc",
    label: "LULC",
    fullName: "Land Use / Land Cover",
    icon: "🌿",
    description: "Minimum 4 classes: Forest, Agriculture, Built-up, Water.",
    accept: ".shp,.zip,.tif,.tiff,.geojson",
    color: "#34c759",
  },
  {
    type: "river",
    label: "River",
    fullName: "River / Stream Network",
    icon: "🌊",
    description: "Stream order attribute required for susceptibility weighting.",
    accept: ".shp,.zip,.geojson",
    color: "#0071e3",
  },
  {
    type: "soil",
    label: "Soil",
    fullName: "Soil Classification",
    icon: "🟤",
    description: "Use HWSD or FAO soil classification system.",
    accept: ".shp,.zip,.tif,.tiff,.geojson",
    color: "#a0522d",
  },
  {
    type: "fault",
    label: "Fault",
    fullName: "Geological Fault Lines",
    icon: "⚡",
    description: "Distance-based buffer will be generated automatically.",
    accept: ".shp,.zip,.geojson",
    color: "#ff3b30",
  },
  {
    type: "coastline",
    label: "Coastline",
    fullName: "Geological Coastline",
    icon: "🏖️",
    description: "Required for coastal proximity mask in terrain classification.",
    accept: ".shp,.zip,.geojson",
    color: "#c084fc",
  },
];

export default function ManualDataIndia() {
  const [datasets, setDatasets] = useState({});
  const [uploading, setUploading] = useState({});
  const [progress, setProgress] = useState({});
  const [error, setError] = useState({});
  const [success, setSuccess] = useState({});
  const [localPaths, setLocalPaths] = useState({});

  const fetchDatasets = async () => {
    try {
      const d = await getManualDatasets();
      const map = {};
      (d.datasets || []).forEach(ds => { map[ds.data_type] = ds; });
      setDatasets(map);
    } catch (_) { }
  };

  useEffect(() => { fetchDatasets(); }, []);

  const handleUpload = async (type, file) => {
    if (!file) return;
    setError(e => ({ ...e, [type]: "" }));
    setSuccess(s => ({ ...s, [type]: "" }));
    setUploading(u => ({ ...u, [type]: true }));
    setProgress(p => ({ ...p, [type]: 0 }));

    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("data_type", type);
      fd.append("description", `${type.toUpperCase()} dataset for all India`);

      const res = await uploadManualDataset(fd, pct => {
        setProgress(p => ({ ...p, [type]: pct }));
      });

      // Use the message from the backend (which includes PostGIS indexing status)
      setSuccess(s => ({ ...s, [type]: res.message || "Uploaded successfully" }));
      await fetchDatasets();
    } catch (err) {
      setError(e => ({ ...e, [type]: err.response?.data?.error || err.message || "Upload failed" }));
    } finally {
      setUploading(u => ({ ...u, [type]: false }));
      setProgress(p => ({ ...p, [type]: 0 }));
    }
  };

  const handleRegisterPath = async (type) => {
    const path = localPaths[type];
    if (!path) return;
    setError(e => ({ ...e, [type]: "" }));
    setSuccess(s => ({ ...s, [type]: "" }));
    setUploading(u => ({ ...u, [type]: true }));

    try {
      await registerManualPath(type, path, `${type.toUpperCase()} registered from local path`);
      setSuccess(s => ({ ...s, [type]: "Path registered successfully" }));
      await fetchDatasets();
      setLocalPaths(p => ({ ...p, [type]: "" }));
    } catch (err) {
      setError(e => ({ ...e, [type]: err.response?.data?.error || "Registration failed" }));
    } finally {
      setUploading(u => ({ ...u, [type]: false }));
    }
  };

  const handleDelete = async (type) => {
    if (!window.confirm(`Remove the ${type.toUpperCase()} dataset? This cannot be undone.`)) return;
    try {
      await deleteManualDataset(type);
      setDatasets(d => { const nd = { ...d }; delete nd[type]; return nd; });
      setSuccess(s => ({ ...s, [type]: "Dataset removed" }));
    } catch (err) {
      setError(e => ({ ...e, [type]: err.response?.data?.error || "Delete failed" }));
    }
  };

  const allUploaded = DATASET_TYPES.every(t => {
    const ds = datasets[t.type];
    if (!ds) return false;
    const isVector = ["river", "fault", "coastline"].includes(t.type);
    if (isVector && !ds.postgis_imported) return false;
    return true;
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      <div>
        <h2 className="t-heading">Manual Data — All India</h2>
        <p style={{ fontSize: 14, color: "#666", marginTop: 6 }}>
          These 5 datasets are uploaded <strong>once</strong> and apply to the entire country.
          They are shared across all susceptibility computations.
        </p>
      </div>

      {/* Status banner */}
      <div style={{
        padding: "16px 20px", borderRadius: 14,
        background: allUploaded ? "#f0fdf4" : "#fffbeb",
        border: `1px solid ${allUploaded ? "#bbf7d0" : "#fed7aa"}`,
        display: "flex", alignItems: "center", gap: 14
      }}>
        <span style={{ fontSize: 22 }}>{allUploaded ? "✅" : "⚠️"}</span>
        <div>
          <p style={{
            fontWeight: 700, fontSize: 14,
            color: allUploaded ? "#166534" : "#92400e"
          }}>
            {allUploaded
              ? "All India datasets ready — Susceptibility generation unlocked"
              : "Datasets processing — Susceptibility mapping restricted"}
          </p>
          <p style={{ fontSize: 12, color: "#666", marginTop: 2 }}>
            {DATASET_TYPES.filter(t => {
              const ds = datasets[t.type];
              if (!ds) return false;
              const isV = ["river", "fault", "coastline"].includes(t.type);
              return !isV || ds.postgis_imported;
            }).map(t => t.label).join(", ")}
            {allUploaded ? " — all synchronized" : " verified so far"}
          </p>
        </div>
      </div>

      {/* Dataset cards */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {DATASET_TYPES.map(dt => {
          const existing = datasets[dt.type];
          const isUploading = uploading[dt.type];
          const pct = progress[dt.type] || 0;

          return (
            <div key={dt.type} style={{
              background: "#fff", border: `1px solid ${existing ? "#e5e5e7" : "#e5e5e7"}`,
              borderRadius: 20, padding: "22px 24px", position: "relative",
              borderTop: `4px solid ${dt.color}`
            }}>
              {/* Header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <span style={{ fontSize: 22 }}>{dt.icon}</span>
                  <div>
                    <p style={{ fontWeight: 700, fontSize: 15 }}>{dt.fullName}</p>
                    <p style={{ fontSize: 12, color: "#888", marginTop: 2 }}>{dt.label}</p>
                  </div>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {(() => {
                    const isVector = ["river", "fault", "coastline"].includes(dt.type);
                    const isFullyReady = existing && (!isVector || existing.postgis_imported);
                    const isPartiallyReady = existing && isVector && !existing.postgis_imported;

                    if (isFullyReady) return (
                      <span style={{
                        padding: "4px 10px", borderRadius: 20, fontSize: 11, fontWeight: 700,
                        background: "#f0fdf4", color: "#166534", border: "1px solid #bbf7d0"
                      }}>✓ Ready</span>
                    );
                    if (isPartiallyReady) return (
                      <span style={{
                        padding: "4px 10px", borderRadius: 20, fontSize: 11, fontWeight: 700,
                        background: "#fff1f2", color: "#e11d48", border: "1px solid #fecdd3"
                      }}>✕ Data Not Indexed</span>
                    );
                    return (
                      <span style={{
                        padding: "4px 10px", borderRadius: 20, fontSize: 11, fontWeight: 700,
                        background: "#f5f5f7", color: "#999", border: "1px solid #e5e5e7"
                      }}>⬆ Needed</span>
                    );
                  })()}

                  {existing?.postgis_imported && (
                    <span style={{
                      padding: "4px 10px", borderRadius: 20, fontSize: 11, fontWeight: 700,
                      background: "#e0f2fe", color: "#0369a1", border: "1px solid #bae6fd"
                    }}>
                      🗺️ PostGIS Indexed
                    </span>
                  )}
                  {existing && (existing.file_name?.toLowerCase().endsWith('.tif') || existing.file_name?.toLowerCase().endsWith('.tiff')) && (
                    <span style={{
                      padding: "4px 10px", borderRadius: 20, fontSize: 11, fontWeight: 700,
                      background: "#f0f9ff", color: "#0284c7", border: "1px solid #bae6fd"
                    }}>
                      🗺️ Raster Layer
                    </span>
                  )}
                  {existing?.is_local_path && (
                    <span style={{
                      padding: "4px 10px", borderRadius: 20, fontSize: 11, fontWeight: 700,
                      background: "#fef3c7", color: "#92400e", border: "1px solid #fde68a"
                    }}>
                      💾 Local Path
                    </span>
                  )}
                </div>
              </div>

              <p style={{ fontSize: 12, color: "#777", marginTop: 12, lineHeight: 1.5 }}>
                {dt.description}
              </p>

              {/* Existing file info */}
              {existing && (
                <div style={{
                  marginTop: 12, padding: "10px 12px",
                  background: "#f5f5f7", borderRadius: 10, fontSize: 12
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <span style={{ fontWeight: 600 }}>📁 {existing.file_name}</span>
                      <span style={{ color: "#888", marginLeft: 8 }}>
                        {new Date(existing.uploaded_at).toLocaleDateString()}
                      </span>
                    </div>
                    <button
                      onClick={() => handleDelete(dt.type)}
                      style={{
                        padding: "3px 9px", borderRadius: 6, border: "1px solid #ffcdd2",
                        background: "#fff5f5", color: "#ff3b30", fontSize: 11,
                        cursor: "pointer", fontFamily: "inherit"
                      }}
                    >Remove</button>
                  </div>
                </div>
              )}

              {/* Upload or Register area */}
              <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
                {isUploading ? (
                  <div>
                    <div style={{ height: 6, background: "#e5e5e7", borderRadius: 4, overflow: "hidden" }}>
                      <div style={{
                        height: "100%", background: dt.color,
                        borderRadius: 4, width: `${pct}%`, transition: "width 0.3s"
                      }} />
                    </div>
                    <p style={{ fontSize: 12, color: "#666", marginTop: 6 }}>
                      {pct > 0 ? `Uploading... ${pct}%` : "Processing..."}
                    </p>
                  </div>
                ) : (
                  <>
                    <label style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "10px 14px", borderRadius: 10, cursor: "pointer",
                      border: "1px dashed #d1d1d6", background: "#fafafa",
                      fontSize: 13, color: "#555", transition: "all 0.2s"
                    }}>
                      ⬆ {existing ? "Replace file" : "Upload file"}
                      <input
                        type="file" hidden accept={dt.accept}
                        onChange={e => handleUpload(dt.type, e.target.files[0])}
                      />
                    </label>

                    {dt.type === 'lulc' && (
                      <div style={{ display: "flex", gap: 6 }}>
                        <input
                          type="text"
                          placeholder="Or paste local file path (e.g. D:\Data\lulc.tif)"
                          value={localPaths[dt.type] || ""}
                          onChange={e => setLocalPaths(p => ({ ...p, [dt.type]: e.target.value }))}
                          style={{
                            flex: 1, padding: "8px 12px", borderRadius: 8, border: "1px solid #e5e5e7",
                            fontSize: 12, outline: "none"
                          }}
                        />
                        <button
                          onClick={() => handleRegisterPath(dt.type)}
                          className="btn btn-secondary btn-sm"
                          disabled={!localPaths[dt.type]}
                        >Register</button>
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Feedback */}
              {error[dt.type] && (
                <p style={{ fontSize: 12, color: "#ff3b30", marginTop: 8 }}>⚠️ {error[dt.type]}</p>
              )}
              {success[dt.type] && (
                <p style={{ fontSize: 12, color: "#34c759", marginTop: 8 }}>✓ {success[dt.type]}</p>
              )}
            </div>
          );
        })}
      </div>

      {/* Info box */}
      <div style={{
        padding: "16px 20px", borderRadius: 14,
        background: "#f5f5f7", border: "1px solid #e5e5e7"
      }}>
        <p style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>ℹ️ About India-Wide Datasets</p>
        <ul style={{ fontSize: 12, color: "#666", paddingLeft: 18, lineHeight: 1.8, margin: 0 }}>
          <li>These datasets are stored once and shared across all regions and all susceptibility jobs.</li>
          <li>Re-uploading a type will replace the previous file instantly.</li>
          <li>Supported sources: NRSC Bhuvan, USGS, Bhunaksha, NATMO, GSI.</li>
          <li>Recommended formats: Shapefile (.zip), GeoTIFF (.tif), or GeoJSON.</li>
        </ul>
      </div>
    </div>
  );
}
