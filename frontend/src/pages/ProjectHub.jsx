import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { gsap } from "gsap";
import { TerrainCard, RainSphereCard } from "../components/three/ModuleCards3D";

/* Simple animated SVG icon for Dynamic module (no Three.js dep needed) */
function DynamicIcon({ hovered }) {
  return (
    <div style={{
      width: "100%", height: "100%",
      display: "flex", alignItems: "center", justifyContent: "center",
      background: hovered
        ? "rgba(255,255,255,0.1)"
        : "linear-gradient(135deg, #667eea22, #764ba222)",
      borderRadius: 18, position: "relative", overflow: "hidden",
    }}>
      <svg width="80" height="80" viewBox="0 0 80 80" fill="none">
        <circle cx="40" cy="40" r="30"
          stroke={hovered ? "rgba(255,255,255,0.4)" : "#667eea44"}
          strokeWidth="1.5" strokeDasharray="4 3"
          style={{ animation: "spin 12s linear infinite" }} />
        <path d="M20 52 Q32 28 40 36 Q48 44 60 20"
          stroke={hovered ? "#fff" : "#667eea"}
          strokeWidth="2.5" strokeLinecap="round" fill="none"/>
        <circle cx="20" cy="52" r="3.5" fill={hovered ? "#fff" : "#667eea"} />
        <circle cx="40" cy="36" r="3.5" fill={hovered ? "#ffe066" : "#f9c74f"} />
        <circle cx="60" cy="20" r="4"   fill={hovered ? "#ff6b6b" : "#f3722c"} />
        <rect x="34" y="54" width="12" height="12" rx="3"
          fill={hovered ? "rgba(255,255,255,0.2)" : "#f0f0f8"}
          stroke={hovered ? "rgba(255,255,255,0.5)" : "#667eea55"} strokeWidth="1"/>
        <text x="40" y="63" textAnchor="middle" fontSize="8"
          fill={hovered ? "#fff" : "#667eea"} fontWeight="700">AI</text>
      </svg>
    </div>
  );
}
import useStore from "../store/useStore";

const MODULES = [
  {
    id: "susceptibility",
    label: "Susceptibility",
    tag: "MODULE A",
    title: "3D AHP Mapping",
    desc: "Analyze hazard, exposure, and vulnerability layers with AI-powered AHP weighting across any region.",
    route: "/susceptibility",
    color: "#000",
    Icon: TerrainCard,
  },
  {
    id: "rainfall",
    label: "Rainfall & Weather",
    tag: "MODULE B",
    title: "ERA5 Data Engine",
    desc: "Visualize dynamic rainfall patterns with 3D extruded heatmaps and 24-hour temporal scrubbing.",
    route: "/rainfall",
    color: "#0071e3",
    Icon: RainSphereCard,
  },
  {
    id: "dynamic",
    label: "Dynamic Risk",
    tag: "MODULE C",
    title: "Physics AI Engine",
    desc: "Date-wise dynamic risk prediction for flood & landslide. Fuses 10-day weather, susceptibility, and LULC with TOPMODEL + SCS-CN physics.",
    route: "/dynamic",
    color: "#5856d6",
    Icon: DynamicIcon,
  },
];

function ModuleCard({ mod, index }) {
  const navigate = useNavigate();
  const [hovered, setHovered] = useState(false);
  const cardRef = useRef();

  return (
    <div
      ref={cardRef}
      className="module-card"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={() => navigate(mod.route)}
      style={{
        position: "relative",
        background: hovered ? mod.color : "#fff",
        border: `1px solid ${hovered ? mod.color : "#e5e5e7"}`,
        borderRadius: 28,
        padding: "40px",
        cursor: "pointer",
        transition: "all 0.45s cubic-bezier(0.34,1.56,0.64,1)",
        transform: hovered ? "translateY(-8px) scale(1.02)" : "translateY(0) scale(1)",
        boxShadow: hovered
          ? `0 24px 60px rgba(0,0,0,0.14), 0 2px 8px rgba(0,0,0,0.06)`
          : "0 4px 20px rgba(0,0,0,0.04)",
        overflow: "hidden",
        animationDelay: `${index * 0.15 + 0.5}s`,
      }}
    >
      {/* 3D Canvas area */}
      <div style={{
        height: 200, marginBottom: 28, borderRadius: 18,
        background: hovered ? "rgba(255,255,255,0.08)" : "#f8f8f9",
        overflow: "hidden",
        transition: "background 0.4s ease",
      }}>
        <mod.Icon hovered={hovered} />
      </div>

      {/* Tag */}
      <p style={{
        fontSize: 11, fontWeight: 600, letterSpacing: "0.12em",
        color: hovered ? "rgba(255,255,255,0.6)" : "#999",
        marginBottom: 10, transition: "color 0.3s"
      }}>
        {mod.tag}
      </p>

      {/* Title */}
      <h2 style={{
        fontSize: 26, fontWeight: 700, letterSpacing: "-0.03em",
        color: hovered ? "#fff" : "#000",
        marginBottom: 12, transition: "color 0.3s"
      }}>
        {mod.title}
      </h2>

      {/* Description */}
      <p style={{
        fontSize: 14, lineHeight: 1.65,
        color: hovered ? "rgba(255,255,255,0.75)" : "#666",
        transition: "color 0.3s"
      }}>
        {mod.desc}
      </p>

      {/* Arrow */}
      <div style={{
        marginTop: 28, display: "flex", alignItems: "center", gap: 8,
        color: hovered ? "#fff" : "#000",
        fontSize: 14, fontWeight: 500, transition: "all 0.3s",
        transform: hovered ? "translateX(6px)" : "translateX(0)"
      }}>
        Open Module
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path d="M4 9h10M10 5l4 4-4 4"
            stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>

      {/* Noise texture overlay */}
      <div style={{
        position: "absolute", inset: 0,
        backgroundImage: "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E\")",
        pointerEvents: "none", borderRadius: 28, zIndex: 1
      }} />
    </div>
  );
}

export default function ProjectHub() {
  const { user, logout } = useStore();
  const navigate = useNavigate();
  const heroRef = useRef();
  const cardsRef = useRef();
  const headerRef = useRef();

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.fromTo(headerRef.current,
        { y: -20, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.6, ease: "power3.out" }
      );
      gsap.fromTo(heroRef.current.children,
        { y: 50, opacity: 0, rotateX: 8 },
        { y: 0, opacity: 1, rotateX: 0, stagger: 0.12, duration: 0.8, ease: "power3.out", delay: 0.2 }
      );
      gsap.fromTo(cardsRef.current.children,
        { y: 60, opacity: 0, rotateX: 10 },
        { y: 0, opacity: 1, rotateX: 0, stagger: 0.18, duration: 0.9, ease: "power3.out", delay: 0.55 }
      );
    });
    return () => ctx.revert();
  }, []);

  return (
    <div style={{ minHeight: "100vh", background: "#f8f8f9" }}>
      {/* Top Navigation */}
      <header ref={headerRef} style={{
        position: "sticky", top: 0, zIndex: 100,
        padding: "0 48px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        height: 72,
        background: "rgba(248,248,249,0.85)",
        backdropFilter: "blur(20px)",
        borderBottom: "1px solid #e5e5e7"
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10, background: "#000",
            display: "flex", alignItems: "center", justifyContent: "center"
          }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
                stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <span style={{ fontWeight: 700, fontSize: 16, letterSpacing: "-0.02em" }}>Aether</span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          {user?.role === "admin" && (
            <button className="btn btn-secondary btn-sm" onClick={() => navigate("/admin")}>
              ⚙ Admin Panel
            </button>
          )}
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "8px 16px", borderRadius: 50,
            background: "#fff", border: "1px solid #e5e5e7"
          }}>
            <div style={{
              width: 28, height: 28, borderRadius: "50%",
              background: "#000", color: "#fff",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 12, fontWeight: 600
            }}>
              {user?.name?.[0] || user?.email?.[0]?.toUpperCase()}
            </div>
            <span style={{ fontSize: 13, fontWeight: 500 }}>{user?.name || user?.email}</span>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={() => { logout(); navigate("/"); }}>
            Sign out
          </button>
        </div>
      </header>

      {/* Hero Section */}
      <div style={{ maxWidth: 900, margin: "0 auto", padding: "80px 48px 48px", perspective: "1000px" }}>
        <div ref={heroRef} style={{ textAlign: "center", marginBottom: 72 }}>
          <p className="t-label" style={{ marginBottom: 20 }}>DISASTER MANAGEMENT PLATFORM</p>
          <h1 className="t-hero" style={{ fontSize: "clamp(48px,6vw,80px)", marginBottom: 20 }}>
            Aether Intelligence
          </h1>
          <p style={{ fontSize: 18, color: "#666", maxWidth: 560, margin: "0 auto", lineHeight: 1.7 }}>
            A geospatial analysis engine for susceptibility modeling and dynamic rainfall intelligence.
            Select a module to begin.
          </p>
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            marginTop: 28, padding: "10px 20px",
            background: "#fff", borderRadius: 50, border: "1px solid #e5e5e7",
            fontSize: 13, color: "#666"
          }}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#34c759", display: "inline-block" }} />
            System Online · All services operational
          </div>
        </div>

        {/* Module Cards */}
        <div ref={cardsRef} style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: 20,
          perspective: "800px"
        }}>
          {MODULES.map((mod, i) => (
            <ModuleCard key={mod.id} mod={mod} index={i} />
          ))}
        </div>

        {/* Stats strip */}
        <div style={{
          marginTop: 60, display: "grid", gridTemplateColumns: "repeat(3, 1fr)",
          gap: 1, background: "#e5e5e7", borderRadius: 20, overflow: "hidden"
        }}>
          {[
            { label: "Regions Indexed", value: "4+" },
            { label: "Disaster Types", value: "5" },
            { label: "Data Modules", value: "5" },
          ].map((s) => (
            <div key={s.label} style={{
              background: "#fff", padding: "28px 32px", textAlign: "center"
            }}>
              <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.04em" }}>{s.value}</div>
              <div className="t-label" style={{ marginTop: 6 }}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
