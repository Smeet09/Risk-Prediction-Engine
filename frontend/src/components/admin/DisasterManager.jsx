import { useState, useEffect, useRef } from "react";
import { getDisasters, createDisaster, updateDisaster, deleteDisaster, uploadDisasterScript } from "../../lib/api";

const CATEGORIES = [
  "Hydro-meteorological",
  "Geological",
  "Climatological",
  "Biological",
  "Other",
];

const CATEGORY_COLORS = {
  "Hydro-meteorological": "#0071e3",
  "Geological":           "#ff3b30",
  "Climatological":       "#ff9500",
  "Biological":           "#34c759",
  "Other":                "#8e8e93",
};

const DEFAULT_WEIGHTS = {
  "1": 0.5, "2": 0.5, "3": 0.5, "4": 0.5,
  "5": 0.5, "6": 0.5, "7": 0.5, "8": 0.5,
  "9": 0.5, "10": 0.5, "11": 0.5, "12": 0.5,
};

export default function DisasterManager() {
  const [grouped,   setGrouped]  = useState({});
  const [edit,      setEdit]     = useState(null);
  const [saving,    setSaving]   = useState(false);
  const [error,     setError]    = useState("");
  const [success,   setSuccess]  = useState("");
  const [scriptUploading, setScriptUploading] = useState({});  // { code: true }
  const [scriptMsg,       setScriptMsg]       = useState({});  // { code: "msg" }
  const fileInputRefs = useRef({});

  const [form, setForm] = useState({
    name: "", code: "", category: "Hydro-meteorological",
    description: "", icon: "⚠️", color: "#0071e3",
    sort_order: 99, is_active: true,
    default_weights: { ...DEFAULT_WEIGHTS },
  });

  const fetchDisasters = async () => {
    try {
      const d = await getDisasters({ all: "1" });
      setGrouped(d.grouped || {});
    } catch (_) {}
  };

  useEffect(() => { fetchDisasters(); }, []);

  const openNew = () => {
    setEdit("new");
    setForm({
      name: "", code: "", category: "Hydro-meteorological",
      description: "", icon: "⚠️", color: "#0071e3",
      sort_order: 99, is_active: true,
      default_weights: { ...DEFAULT_WEIGHTS },
    });
    setError(""); setSuccess("");
  };

  const openEdit = (d) => {
    setEdit(d);
    setForm({
      name: d.name, code: d.code, category: d.category,
      description: d.description || "", icon: d.icon || "⚠️",
      color: d.color || "#0071e3", sort_order: d.sort_order || 99,
      is_active: d.is_active, default_weights: d.default_weights || { ...DEFAULT_WEIGHTS },
    });
    setError(""); setSuccess("");
  };

  const handleSave = async () => {
    if (!form.name || !form.code || !form.category)
      return setError("Name, code, and category are required.");
    setSaving(true); setError(""); setSuccess("");
    try {
      if (edit === "new") {
        await createDisaster(form);
        setSuccess("Disaster type created successfully.");
      } else {
        await updateDisaster(edit.code, form);
        setSuccess("Disaster type updated.");
      }
      await fetchDisasters();
      setEdit(null);
    } catch (err) {
      setError(err.response?.data?.error || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (d) => {
    try {
      await updateDisaster(d.code, { is_active: !d.is_active });
      await fetchDisasters();
    } catch (_) {}
  };

  const handleDelete = async (code) => {
    if (!window.confirm(`Permanently delete '${code}'? Consider hiding instead.`)) return;
    try {
      await deleteDisaster(code);
      await fetchDisasters();
    } catch (err) {
      alert(err.response?.data?.error || "Delete failed");
    }
  };

  const handleScriptUpload = async (code, file) => {
    if (!file) return;
    setScriptUploading(p => ({ ...p, [code]: true }));
    setScriptMsg(p => ({ ...p, [code]: "" }));
    try {
      const res = await uploadDisasterScript(code, file);
      setScriptMsg(p => ({ ...p, [code]: `✓ Script active: ${res.filename}` }));
      await fetchDisasters();
    } catch (err) {
      setScriptMsg(p => ({ ...p, [code]: `⚠ ${err.message}` }));
    } finally {
      setScriptUploading(p => ({ ...p, [code]: false }));
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h2 className="t-heading">Disaster Type Manager</h2>
          <p style={{ fontSize: 14, color: "#666", marginTop: 6 }}>
            Add, edit, hide, or remove disaster types. Hidden types won't appear to users.
          </p>
        </div>
        <button onClick={openNew} style={{
          padding: "10px 18px", borderRadius: 12, border: "none",
          background: "#000", color: "#fff", fontSize: 14, fontWeight: 600,
          cursor: "pointer", fontFamily: "inherit"
        }}>+ Add Disaster</button>
      </div>

      {/* Grouped disaster list */}
      {Object.entries(grouped).map(([category, items]) => (
        <div key={category} style={{
          background: "#fff", border: "1px solid #e5e5e7",
          borderRadius: 20, overflow: "hidden"
        }}>
          <div style={{
            padding: "14px 22px", borderBottom: "1px solid #e5e5e7",
            background: "#fafafa", display: "flex", alignItems: "center", gap: 10
          }}>
            <div style={{
              width: 10, height: 10, borderRadius: "50%",
              background: CATEGORY_COLORS[category] || "#aaa"
            }} />
            <span style={{ fontWeight: 700, fontSize: 14 }}>{category}</span>
            <span style={{ fontSize: 12, color: "#aaa",
              marginLeft: 4 }}>{items.length} disaster{items.length !== 1 ? "s" : ""}</span>
          </div>
          {items.map((d, i) => (
            <div key={d.code} style={{
              borderBottom: i < items.length - 1 ? "1px solid #f0f0f0" : "none",
              opacity: d.is_active ? 1 : 0.5
            }}>
              {/* Main row */}
              <div style={{ padding: "16px 22px", display: "flex", alignItems: "center", gap: 16 }}>
                <span style={{ fontSize: 22, flexShrink: 0 }}>{d.icon}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 700, fontSize: 14 }}>{d.name}</span>
                    <code style={{ fontSize: 11, padding: "2px 6px", background: "#f0f0f1",
                      borderRadius: 4, color: "#666" }}>{d.code}</code>
                    {!d.is_active && (
                      <span style={{ fontSize: 11, padding: "2px 8px", background: "#f0f0f1",
                        borderRadius: 20, color: "#999" }}>Hidden</span>
                    )}
                    {d.script_path ? (
                      <span style={{ fontSize: 10, padding: "2px 8px",
                        background: "#dcfce7", borderRadius: 20,
                        color: "#166534", fontWeight: 600, border: "1px solid #bbf7d0" }}>
                        ⚡ Custom Script
                      </span>
                    ) : (
                      <span style={{ fontSize: 10, padding: "2px 8px",
                        background: "#f0f0f1", borderRadius: 20,
                        color: "#666", fontWeight: 500 }}>
                        Built-in
                      </span>
                    )}
                  </div>
                  <p style={{ fontSize: 12, color: "#888", marginTop: 2 }}>
                    {d.description?.substring(0, 80)}{d.description?.length > 80 ? "..." : ""}
                  </p>
                </div>
                <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                  <button onClick={() => handleToggle(d)} style={{
                    padding: "6px 12px", borderRadius: 8, cursor: "pointer",
                    border: "1px solid #d1d1d6", background: d.is_active ? "#fff5f5" : "#f0fdf4",
                    fontSize: 12, fontWeight: 600,
                    color: d.is_active ? "#ff3b30" : "#34c759", fontFamily: "inherit"
                  }}>
                    {d.is_active ? "Hide" : "Show"}
                  </button>
                  <button onClick={() => openEdit(d)} style={{
                    padding: "6px 12px", borderRadius: 8, border: "1px solid #d1d1d6",
                    background: "#fafafa", fontSize: 12, cursor: "pointer",
                    fontFamily: "inherit", color: "#444", fontWeight: 600
                  }}>Edit</button>
                  <button onClick={() => handleDelete(d.code)} style={{
                    padding: "6px 12px", borderRadius: 8, border: "1px solid #ffcdd2",
                    background: "#fff5f5", color: "#ff3b30", fontSize: 12,
                    cursor: "pointer", fontFamily: "inherit", fontWeight: 600
                  }}>Delete</button>
                </div>
              </div>

              {/* Script upload row */}
              <div style={{ padding: "0 22px 12px 60px", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <input
                  type="file"
                  accept=".py"
                  style={{ display: "none" }}
                  ref={el => { fileInputRefs.current[d.code] = el; }}
                  onChange={e => handleScriptUpload(d.code, e.target.files[0])}
                />
                <button
                  onClick={() => fileInputRefs.current[d.code]?.click()}
                  disabled={scriptUploading[d.code]}
                  style={{
                    padding: "5px 12px", borderRadius: 8, cursor: "pointer",
                    border: "1px solid #0071e3", background: "#f0f9ff",
                    color: "#0071e3", fontSize: 11, fontWeight: 600,
                    fontFamily: "inherit", display: "flex", alignItems: "center", gap: 5,
                    opacity: scriptUploading[d.code] ? 0.6 : 1,
                  }}
                >
                  {scriptUploading[d.code] ? "⏳ Uploading..." : "📤 Upload Script (.py)"}
                </button>
                {scriptMsg[d.code] && (
                  <span style={{
                    fontSize: 11,
                    color: scriptMsg[d.code].startsWith("✓") ? "#166534" : "#991b1b",
                    fontWeight: 500
                  }}>{scriptMsg[d.code]}</span>
                )}
                {d.script_path && !scriptMsg[d.code] && (
                  <span style={{ fontSize: 11, color: "#555" }}>
                    Active: <code style={{ fontSize: 10 }}>{d.script_path.split(/[/\\]/).pop()}</code>
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      ))}

      {/* ─── Edit / New Modal ──────────────────── */}
      {edit !== null && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)",
          zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: 24
        }}>
          <div style={{
            background: "#fff", borderRadius: 24, padding: 32,
            width: "100%", maxWidth: 560, maxHeight: "90vh", overflowY: "auto",
            boxShadow: "0 25px 80px rgba(0,0,0,0.2)"
          }}>
            <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 20 }}>
              {edit === "new" ? "Add New Disaster Type" : `Edit — ${edit.name}`}
            </h3>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <div>
                <label style={lbl}>Name *</label>
                <input style={inp} value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="e.g. Flood" />
              </div>
              <div>
                <label style={lbl}>Code * <span style={{ fontWeight: 400, color: "#aaa" }}>(lowercase, no spaces)</span></label>
                <input style={inp} value={form.code}
                  onChange={e => setForm(f => ({ ...f, code: e.target.value.toLowerCase().replace(/\s+/g, "_") }))}
                  placeholder="e.g. flood"
                  readOnly={edit !== "new"}
                />
              </div>
              <div>
                <label style={lbl}>Category *</label>
                <select style={inp} value={form.category}
                  onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
                  {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <div>
                <label style={lbl}>Icon (emoji)</label>
                <input style={inp} value={form.icon}
                  onChange={e => setForm(f => ({ ...f, icon: e.target.value }))}
                  placeholder="e.g. 🌊" />
              </div>
              <div>
                <label style={lbl}>Color</label>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input type="color" value={form.color}
                    onChange={e => setForm(f => ({ ...f, color: e.target.value }))}
                    style={{ width: 40, height: 38, borderRadius: 8, border: "1px solid #d1d1d6",
                      cursor: "pointer", padding: 2 }} />
                  <input style={{ ...inp, flex: 1 }} value={form.color}
                    onChange={e => setForm(f => ({ ...f, color: e.target.value }))} />
                </div>
              </div>
              <div>
                <label style={lbl}>Sort Order</label>
                <input type="number" style={inp} value={form.sort_order}
                  onChange={e => setForm(f => ({ ...f, sort_order: parseInt(e.target.value) || 99 }))} />
              </div>
            </div>

            <div style={{ marginTop: 14 }}>
              <label style={lbl}>Description</label>
              <textarea style={{ ...inp, minHeight: 72, resize: "vertical" }}
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="Brief description of this disaster type..." />
            </div>

            <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 10 }}>
              <input type="checkbox" id="is_active" checked={form.is_active}
                onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))} />
              <label htmlFor="is_active" style={{ fontSize: 13, cursor: "pointer" }}>
                Active (visible to users)
              </label>
            </div>

            {error   && <p style={{ color: "#ff3b30", fontSize: 13, marginTop: 12 }}>⚠️ {error}</p>}
            {success && <p style={{ color: "#34c759", fontSize: 13, marginTop: 12 }}>✓ {success}</p>}

            <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
              <button onClick={() => setEdit(null)} style={{
                flex: 1, padding: "12px", borderRadius: 10, border: "1px solid #d1d1d6",
                background: "#fafafa", fontFamily: "inherit", fontSize: 14, cursor: "pointer"
              }}>Cancel</button>
              <button onClick={handleSave} disabled={saving} style={{
                flex: 1, padding: "12px", borderRadius: 10, border: "none",
                background: "#000", color: "#fff", fontFamily: "inherit",
                fontSize: 14, fontWeight: 600, cursor: saving ? "not-allowed" : "pointer"
              }}>{saving ? "Saving..." : "Save"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const lbl = {
  display: "block", fontSize: 11, fontWeight: 700, color: "#555",
  letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 6
};
const inp = {
  width: "100%", padding: "10px 14px", borderRadius: 10, boxSizing: "border-box",
  border: "1px solid #d1d1d6", background: "#fff", fontSize: 14,
  fontFamily: "inherit", outline: "none"
};
