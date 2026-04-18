import { useState, useEffect, useRef } from "react";
import { getBoundaryStatus, uploadBoundaryZip } from "../../lib/api";

export default function BoundaryImporter() {
  const [status, setStatus] = useState(null);
  const [log, setLog]       = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone]       = useState(false);
  
  const [level, setLevel] = useState("state");
  const [overwrite, setOverwrite] = useState(false);
  const [file, setFile] = useState(null);
  const [drag, setDrag] = useState(false);
  const fileInputRef = useRef(null);

  const fetchStatus = async () => {
    try {
      const d = await getBoundaryStatus();
      setStatus(d);
    } catch (_) {}
  };

  useEffect(() => {
    fetchStatus();
    // Auto-poll the DB counts every 3 seconds
    const iv = setInterval(fetchStatus, 3000);
    return () => clearInterval(iv);
  }, []);

  const handleImport = async () => {
    if (!file) return alert("Please select a .zip shapefile packet first!");
    if (overwrite && !window.confirm(`WARNING: This will completely WIPE the existing ${level.toUpperCase()} tables and replace them with this zip file. Are you sure?`)) {
      return;
    }

    setLoading(true);
    setDone(false);
    setLog(`[INFO] Targeting ${level.toUpperCase()} level...\n[INFO] Starting upload for ${file.name}...\n`);

    try {
      const res = await uploadBoundaryZip(file, level, overwrite);

      if (!res.ok) {
        const d = await res.json();
        setLog(l => l + `\n[HTTP Error] ${res.status} - ${d.error || ''}\n`);
        setLoading(false);
        return;
      }

      setLog(l => l + "[INFO] File successfully uploaded. Spawning Geometry Engine...\n");
      const reader = res.body?.getReader();
      if (!reader) {
        setLog(l => l + "[ERROR] Stream failed.\n");
        setLoading(false);
        return;
      }

      const decoder = new TextDecoder();
      while (true) {
        const { done: streamDone, value } = await reader.read();
        if (streamDone) break;
        const chunk = decoder.decode(value, { stream: true });
        setLog(prev => prev + chunk);
      }
      setDone(true);
    } catch (err) {
      setLog(l => l + `\n[FATAL ERROR] ${err.message}\n`);
    } finally {
      setLoading(false);
      fetchStatus();
    }
  };

  return (
    <div style={{ background: "#fff", borderRadius: 20, padding: 24,
      border: "1px solid #e5e5e7", display: "flex", flexDirection: "column", gap: 20 }}>
      
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h3 style={{ fontSize: 16, fontWeight: 700, letterSpacing: "-0.02em" }}>
              🗺️ Dynamic Boundary Extractor
            </h3>
            <p style={{ fontSize: 13, color: "#888", marginTop: 4 }}>
              Directly upload <b>.zip</b> files containing your shapefiles (.shp, .dbf, .shx) to dynamically provision spatial tables.
            </p>
          </div>
          {status?.imported && (
            <div style={{ padding: "6px 12px", background: "#f0fdf4", color: "#16a34a",
              borderRadius: 20, fontSize: 12, fontWeight: 700 }}>
              ✓ Data Present
            </div>
          )}
        </div>
      </div>

      {/* Stats row */}
      {status && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
          {["state", "district", "taluka", "village"].map(lvl => (
            <div key={lvl} style={{ padding: "12px", background: status.counts[lvl] > 0 ? "#f0fdf4" : "#f5f5f7",
              borderRadius: 10, border: `1px solid ${status.counts[lvl] > 0 ? "#bbf7d0" : "#e5e5e7"}` }}>
              <div style={{ fontSize: 20, fontWeight: 800, 
                color: status.counts[lvl] > 0 ? "#16a34a" : "#aaa" }}>
                {status.counts[lvl]?.toLocaleString() || 0}
              </div>
              <div style={{ fontSize: 11, color: "#888", fontWeight: 600, textTransform: "uppercase" }}>
                {lvl}s
              </div>
            </div>
          ))}
        </div>
      )}

      <hr style={{ borderTop: "1px solid #e5e5e7", margin: "10px 0" }} />

      {/* Configuration */}
      <div style={{ display: "flex", gap: 20, alignItems: "center" }}>
         <div style={{ flex: 1 }}>
            <label style={{ display: "block", fontSize: 11, fontWeight: 700, color: "#555",
              letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 6 }}>
              Target Level
            </label>
            <select value={level} onChange={(e) => setLevel(e.target.value)}
              style={{ width: "100%", padding: "12px 14px", borderRadius: 10,
              border: "1px solid #d1d1d6", background: "#fff", fontSize: 14 }}>
              <option value="state">States</option>
              <option value="district">Districts</option>
              <option value="taluka">Talukas</option>
              <option value="village">Villages</option>
            </select>
         </div>

         <div style={{ flex: 1 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", 
                padding: "12px", borderRadius: 10, border: `1px solid ${overwrite ? "#ef4444" : "#d1d1d6"}`, 
                background: overwrite ? "#fef2f2" : "#f9f9fa", marginTop: 20 }}>
              <input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)} 
                style={{ width: 18, height: 18, accentColor: "#ef4444" }}/>
              <div>
                <div style={{ fontSize: 14, fontWeight: 600, color: overwrite ? "#b91c1c" : "#333" }}>Overwrite Existing Data</div>
                <div style={{ fontSize: 11, color: overwrite ? "#dc2626" : "#888" }}>Wipes completely replacing current layer.</div>
              </div>
            </label>
         </div>
      </div>

      {/* Dropzone */}
      <div
        onDragOver={e => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={e => {
          e.preventDefault(); setDrag(false);
          const dropped = e.dataTransfer.files[0];
          if (dropped) setFile(dropped);
        }}
        onClick={() => fileInputRef.current?.click()}
        style={{
          border: `2px dashed ${drag ? "#0071e3" : "#d1d1d6"}`,
          background: drag ? "#f0f9ff" : file ? "#f8fafc" : "#fafafa",
          borderRadius: 16, padding: "30px", textAlign: "center",
          cursor: "pointer", transition: "all 0.2s"
        }}
      >
        <input type="file" ref={fileInputRef} hidden accept=".zip"
          onChange={e => { if (e.target.files[0]) setFile(e.target.files[0]); }} />
        
        {file ? (
          <div>
            <div style={{ fontSize: 32, marginBottom: 8 }}>📦</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: "#333" }}>{file.name}</div>
            <div style={{ fontSize: 12, color: "#888", marginTop: 4 }}>
              {(file.size / 1024 / 1024).toFixed(2)} MB
            </div>
            <button onClick={(e) => { e.stopPropagation(); setFile(null); }}
              style={{ marginTop: 12, fontSize: 12, color: "#ef4444", border: "none", 
                background: "transparent", cursor: "pointer", fontWeight: 600 }}>Remove</button>
          </div>
        ) : (
          <div>
            <div style={{ fontSize: 32, marginBottom: 8 }}>☁️</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: "#333" }}>
              Drop .zip payload here
            </div>
            <div style={{ fontSize: 12, color: "#888", marginTop: 4 }}>
              Must contain .shp, .shx, .dbf inside
            </div>
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 10 }}>
        <button
          onClick={handleImport}
          disabled={loading || !file}
          style={{
            padding: "14px 20px", borderRadius: 12, border: "none", width: 220,
            background: loading ? "#e5e5e7" : "#000",
            color: loading ? "#999" : "#fff",
            fontSize: 14, fontWeight: 700, cursor: loading || !file ? "not-allowed" : "pointer",
          }}
        >
          {loading ? "⏳ Processing..." : "Start Sequence"}
        </button>
      </div>

      {log && (
        <div style={{
          background: "#000", color: "#00ff00", padding: "16px",
          borderRadius: 12, fontFamily: "monospace", fontSize: 13,
          lineHeight: 1.5, maxHeight: 300, overflowY: "auto",
          whiteSpace: "pre-wrap"
        }}>
          {log}
        </div>
      )}

      {done && (
        <div style={{
          padding: "12px 16px", background: "#f0fdf4", color: "#16a34a",
          borderRadius: 10, fontSize: 14, fontWeight: 600,
          display: "flex", alignItems: "center", gap: 8
        }}>
          ✅ Ingestion complete! The new boundary maps are actively synced.
        </div>
      )}
    </div>
  );
}
