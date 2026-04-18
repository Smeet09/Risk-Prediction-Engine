import axios from "axios";

const api = axios.create({ baseURL: "/api" });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("aether_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem("aether_token");
      localStorage.removeItem("aether_user");
      window.location.href = "/";
    }
    return Promise.reject(err);
  }
);

export default api;

// ─── Auth ─────────────────────────────────────────────────────────────────────
export const login = (email, password) =>
  api.post("/auth/login", { email, password }).then((r) => r.data);

// ─── Regions ──────────────────────────────────────────────────────────────────
export const getRegions       = () => api.get("/regions").then((r) => r.data);
export const getRegionsFlat   = () => api.get("/regions/flat").then((r) => r.data);
export const getCompleteRegions = () => api.get("/regions/complete").then((r) => r.data);
export const deleteRegionData = (id) => api.delete(`/regions/${id}/data`).then((r) => r.data);

// ─── Boundaries (cascading dropdowns from india_boundaries table) ─────────────
export const getBoundaryStatus = async () => (await api.get("/boundaries/status")).data;

export const uploadBoundaryZip = async (file, level, overwrite) => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("level", level);
  formData.append("overwrite", overwrite);
  
  const token = localStorage.getItem("aether_token");
  // We use fetch directly since we need to stream chunked response logs in real-time
  const res = await fetch("/api/boundaries/upload-zip", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  return res;
};

export const getStates          = () => api.get("/boundaries/states").then((r) => r.data);
export const getDistricts       = (state) => api.get(`/boundaries/districts/${encodeURIComponent(state)}`).then((r) => r.data);
export const getTalukas         = (state, district) =>
  api.get(`/boundaries/talukas/${encodeURIComponent(state)}/${encodeURIComponent(district)}`).then((r) => r.data);
export const getVillages        = (state, district, taluka) =>
  api.get(`/boundaries/villages/${encodeURIComponent(state)}/${encodeURIComponent(district)}/${encodeURIComponent(taluka)}`).then((r) => r.data);
export const importBoundaries   = (opts = {}) =>
  api.post("/boundaries/import", opts, { responseType: "text", timeout: 30 * 60 * 1000 }).then((r) => r.data);

// ─── Upload ───────────────────────────────────────────────────────────────────
export const uploadDem = (formData, onProgress) =>
  api.post("/upload/dem", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (e) => onProgress?.(Math.round((e.loaded * 100) / e.total)),
  }).then((r) => r.data);

export const uploadFile = (formData, onProgress) =>
  api.post("/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (e) => onProgress?.(Math.round((e.loaded * 100) / e.total)),
  }).then((r) => r.data);

// ─── Manual Data India ────────────────────────────────────────────────────────
export const getManualDatasets   = () => api.get("/manual").then((r) => r.data);
export const uploadManualDataset = (formData, onProgress) =>
  api.post("/manual", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (e) => onProgress?.(Math.round((e.loaded * 100) / e.total)),
  }).then((r) => r.data);
export const registerManualPath = (data_type, local_path, description) =>
  api.post("/manual/register-path", { data_type, local_path, description }).then((r) => r.data);
export const deleteManualDataset = (type) => api.delete(`/manual/${type}`).then((r) => r.data);

// ─── Disaster Types ───────────────────────────────────────────────────────────
export const getDisasters       = (params = {}) => api.get("/disasters", { params }).then((r) => r.data);
export const createDisaster     = (data) => api.post("/disasters", data).then((r) => r.data);
export const updateDisaster     = (code, data) => api.patch(`/disasters/${code}`, data).then((r) => r.data);
export const deleteDisaster     = (code) => api.delete(`/disasters/${code}`).then((r) => r.data);
export const uploadDisasterScript = (code, file) => {
  const formData = new FormData();
  formData.append("script", file);
  const token = localStorage.getItem("aether_token");
  return fetch(`/api/disasters/${code}/upload-script`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  }).then(async (r) => {
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Script upload failed");
    return data;
  });
};

// ─── Jobs ─────────────────────────────────────────────────────────────────────
export const getJob    = (id) => api.get(`/jobs/${id}`).then((r) => r.data);
export const getJobs   = ()   => api.get("/jobs").then((r) => r.data);
export const syncJobs  = ()   => api.post("/jobs/sync").then((r) => r.data);
export const syncIntegrity = () => api.post("/jobs/sync-integrity").then((r) => r.data);

// ─── Susceptibility ───────────────────────────────────────────────────────────
export const generateSusceptibility = (region_id, disaster_code, terrain_weights) =>
  api.post("/susceptibility/generate", { region_id, disaster_code, terrain_weights }).then((r) => r.data);
export const getSusceptibility      = (regionId, disaster_code) =>
  api.get(`/susceptibility/${regionId}/${disaster_code}`).then((r) => r.data);
export const getSusceptibilityRegion = (regionId) =>
  api.get(`/susceptibility/${regionId}`).then((r) => r.data);
export const getSusceptibilityList  = () => api.get("/susceptibility").then((r) => r.data);
export const getRegionTerrain = (regionId) => api.get(`/regions/${regionId}/terrain`).then((r) => r.data);

// ─── Rainfall ─────────────────────────────────────────────────────────────────
export const checkRainfall  = (region_id, date) =>
  api.get("/rainfall/check", { params: { region_id, date } }).then((r) => r.data);
export const getRainfallData = (region_id, date) =>
  api.get("/rainfall/data", { params: { region_id, date } }).then((r) => r.data);
export const fetchEra5 = (region_id, date) =>
  api.post("/rainfall/fetch", { region_id, date }).then((r) => r.data);
