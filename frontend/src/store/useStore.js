import { create } from "zustand";

const useStore = create((set, get) => ({
  // Auth
  user: JSON.parse(localStorage.getItem("aether_user") || "null"),
  token: localStorage.getItem("aether_token") || null,
  login: (user, token) => {
    localStorage.setItem("aether_user", JSON.stringify(user));
    localStorage.setItem("aether_token", token);
    set({ user, token });
  },
  logout: () => {
    localStorage.removeItem("aether_user");
    localStorage.removeItem("aether_token");
    set({ user: null, token: null });
  },

  // Regions
  regions: [],
  setRegions: (regions) => set({ regions }),

  // Selected region
  selectedCountry: null,
  selectedState: null,
  selectedDistrict: null,
  selectedRegionId: null,
  setSelection: (country, state, district, regionId) =>
    set({ selectedCountry: country, selectedState: state, selectedDistrict: district, selectedRegionId: regionId }),

  // Jobs
  activeJobs: {},
  updateJob: (jobId, data) =>
    set((s) => ({ activeJobs: { ...s.activeJobs, [jobId]: { ...(s.activeJobs[jobId] || {}), ...data } } })),

  // Admin module
  adminModule: "overview",
  setAdminModule: (mod) => set({ adminModule: mod }),

  // Disaster types
  disasterTypes: ["Flood", "Landslide", "Earthquake", "Cyclone", "Drought"],
}));

export default useStore;
