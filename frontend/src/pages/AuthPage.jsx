import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { gsap } from "gsap";
import TopographyBackground from "../components/three/TopographyBackground";
import useStore from "../store/useStore";
import { login } from "../lib/api";

export default function AuthPage() {
  const navigate = useNavigate();
  const { login: storeLogin, user, token } = useStore();
  const [form, setForm] = useState({ email: "", password: "", role: "user" });
  const [system, setSystem] = useState("disaster");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showLoginForm, setShowLoginForm] = useState(false);
  const formRef = useRef();
  const logoRef = useRef();

  // GSAP entrance
  useEffect(() => {
    if (!logoRef.current || !formRef.current) return;
    const ctx = gsap.context(() => {
      gsap.fromTo(logoRef.current,
        { y: -30, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.9, ease: "power3.out" }
      );
      gsap.fromTo(
        formRef.current.querySelectorAll(".auth-field"),
        { y: 40, opacity: 0, rotateX: 12 },
        { y: 0, opacity: 1, rotateX: 0, duration: 0.7, stagger: 0.1, ease: "power3.out", delay: 0.3 }
      );
    });
    return () => ctx.revert();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await login(form.email, form.password);
      storeLogin(data.user, data.token);
      if (system === "crop") {
        window.location.href = `http://localhost:8501/?token=${data.token}`;
      } else {
        navigate(data.user.role === "admin" ? "/admin" : "/hub", { replace: true });
      }
    } catch (err) {
      setError(err.response?.data?.error || "Login failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleContinueAsUser = () => {
    if (system === "crop") {
      window.location.href = `http://localhost:8501/?token=${token}`;
    } else {
      navigate(user.role === "admin" ? "/admin" : "/hub", { replace: true });
    }
  };

  const handleSwitchAccount = () => {
    localStorage.clear();
    sessionStorage.clear();
    window.location.reload();
  };

  const Logo = () => (
    <div ref={logoRef} style={{ textAlign: "center", marginBottom: 30 }}>
      <div style={{
        width: 100, height: 100, borderRadius: 24,
        background: "#fff", margin: "0 auto 24px",
        display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: "0 10px 30px rgba(0,0,0,0.06)",
        padding: 10
      }}>
        <img src="/bisag_logo.png" alt="BISAG Logo" style={{ width: "100%", height: "100%", objectFit: "contain" }} />
      </div>
      <p className="t-label" style={{ letterSpacing: "0.2em", marginBottom: 6 }}>PREDICTION ENGINE PLATFORM</p>
      <h1 className="t-title" style={{ fontSize: 32, letterSpacing: "-0.035em" }}>GM3 Intelligence</h1>
      <p style={{ color: "#39bd97", fontSize: 12, marginTop: 4, fontWeight: 500 }}>Geospatial Multi-Thematic Mathematical Modal</p>
      <p style={{ color: "#ff9f0a", fontSize: 12, marginTop: 4, fontWeight: 500 }}>This system is only for the India region</p>
    </div>
  );

  const SystemSelector = () => (
    <div className="auth-field form-group" style={{ marginBottom: 20 }}>
      <label className="form-label" style={{ marginBottom: 8, display: "block" }}>Select System</label>
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 1fr",
        gap: 6, background: "#f0f0f1", borderRadius: 12, padding: 4
      }}>
        {[
          { id: "disaster", label: "⚠ Disaster Prediction" },
          { id: "crop", label: "🌾 Crop Prediction" }
        ].map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setSystem(id)}
            style={{
              padding: "10px 8px", borderRadius: 9, border: "none", cursor: "pointer",
              fontFamily: "inherit", fontSize: 12, fontWeight: 500,
              transition: "all 0.2s ease",
              background: system === id ? "#fff" : "transparent",
              color: system === id ? "#000" : "#999",
              boxShadow: system === id ? "0 2px 8px rgba(0,0,0,0.08)" : "none"
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {system === "crop" && (
        <p style={{ fontSize: 11, color: "#39bd97", marginTop: 6, marginLeft: 2 }}>
          You will be redirected to the Crop Intelligence app after sign in.
        </p>
      )}
    </div>
  );

  // ── Already signed in ── show system picker without re-entering credentials
  if (token && user && !showLoginForm) {
    return (
      <div style={{ position: "relative", height: "100vh", overflow: "hidden", background: "#f8f8f9" }}>
        <TopographyBackground />
        <div style={{ position: "fixed", inset: 0, zIndex: 1, background: "linear-gradient(135deg, rgba(248,248,249,0.85) 0%, rgba(255,255,255,0.75) 100%)" }} />
        <div style={{
          position: "relative", zIndex: 2, height: "100vh",
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", padding: "24px"
        }}>
          <Logo />
          <div className="glass" style={{ width: "100%", maxWidth: 400, padding: "40px", borderRadius: 28 }}>
            {/* Signed-in indicator */}
            <div style={{
              display: "flex", alignItems: "center", gap: 12, marginBottom: 24,
              padding: "14px 16px", borderRadius: 14,
              background: "rgba(57,189,151,0.08)", border: "1px solid rgba(57,189,151,0.2)"
            }}>
              <div style={{
                width: 38, height: 38, borderRadius: "50%",
                background: "linear-gradient(135deg,#39bd97,#27a080)",
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "#fff", fontWeight: 700, fontSize: 15, flexShrink: 0
              }}>
                {(user.name || user.email || "U")[0].toUpperCase()}
              </div>
              <div>
                <p style={{ fontSize: 13, fontWeight: 600, margin: 0 }}>{user.name || user.email}</p>
                <p style={{ fontSize: 11, color: "#999", margin: 0, marginTop: 2 }}>
                  {user.role === "admin" ? "Administrator" : "User"} · Signed in
                </p>
              </div>
            </div>

            <SystemSelector />

            <button
              className="btn btn-primary"
              onClick={handleContinueAsUser}
              style={{ marginTop: 8, width: "100%", justifyContent: "center", padding: "15px" }}
            >
              Continue →
            </button>

            <button
              type="button"
              onClick={handleSwitchAccount}
              style={{
                marginTop: 12, width: "100%", padding: "10px",
                background: "transparent", border: "none", cursor: "pointer",
                color: "#999", fontSize: 12, fontFamily: "inherit"
              }}
            >
              Sign in as a different account
            </button>
          </div>
          <p className="t-small" style={{ marginTop: 32, opacity: 0.5 }}>
            Prediction Engine © {new Date().getFullYear()} @ BISAG-N
          </p>
        </div>
      </div>
    );
  }

  return (
    <div style={{ position: "relative", minHeight: "100vh", background: "#f8f8f9", overflowY: "auto" }}>
      <TopographyBackground />

      {/* Overlay gradient */}
      <div style={{
        position: "fixed", inset: 0, zIndex: 1,
        background: "linear-gradient(135deg, rgba(248,248,249,0.92) 0%, rgba(255,255,255,0.7) 100%)"
      }} />

      {/* Centered Auth Content */}
      <div style={{
        position: "relative", zIndex: 2,
        minHeight: "100vh",
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        padding: "60px 24px" // Increased vertical padding to prevent clipping
      }}>
        {/* Logo / Brand */}
        <Logo />

        {/* Form Card */}
        <div className="glass" style={{
          width: "100%", maxWidth: 440,
          padding: "48px 40px", borderRadius: 32,
          boxShadow: "0 20px 60px rgba(0,0,0,0.05), inset 0 0 0 1px rgba(255,255,255,0.4)"
        }}>
          <p style={{ color: "#999", fontSize: 12, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 24, textAlign: "center" }}>Identity Verification</p>

          {/* System Selector */}
          <SystemSelector />

          {/* Role Toggle */}
          <div className="auth-field" style={{
            display: "grid", gridTemplateColumns: "1fr 1fr",
            gap: 6, marginBottom: 28,
            background: "rgba(0,0,0,0.05)", borderRadius: 14, padding: 5
          }}>
            {["user", "admin"].map((r) => (
              <button
                key={r}
                onClick={() => setForm(f => ({
                  ...f, role: r,
                  email: r === "admin" ? "admin@bisag.predictionengine" : "user@bisag.predictionengine",
                  password: "password"
                }))}
                style={{
                  padding: "12px", borderRadius: 10, border: "none", cursor: "pointer",
                  fontFamily: "inherit", fontSize: 13, fontWeight: 600,
                  transition: "all 0.2s ease",
                  background: form.role === r ? "#fff" : "transparent",
                  color: form.role === r ? "#000" : "#888",
                  boxShadow: form.role === r ? "0 4px 12px rgba(0,0,0,0.1)" : "none"
                }}
              >
                {r === "admin" ? "⚙ Admin Space" : "◉ User Portal"}
              </button>
            ))}
          </div>

          <form ref={formRef} onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <div className="auth-field form-group">
              <label className="form-label">Email Address</label>
              <input
                className="input"
                type="email"
                placeholder="you@bisag.predictionengine"
                value={form.email}
                onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                required
                autoComplete="email"
                style={{ background: "rgba(255,255,255,0.6)" }}
              />
            </div>

            <div className="auth-field form-group">
              <label className="form-label">Password</label>
              <input
                className="input"
                type="password"
                placeholder="••••••••"
                value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                required
                autoComplete="current-password"
                style={{ background: "rgba(255,255,255,0.6)" }}
              />
            </div>

            {error && (
              <div className="auth-field" style={{
                background: "rgba(255,59,48,0.08)", border: "1px solid rgba(255,59,48,0.2)",
                borderRadius: 12, padding: "12px 16px",
                color: "#ff3b30", fontSize: 13
              }}>
                {error}
              </div>
            )}

            <button
              className="auth-field btn btn-primary"
              type="submit"
              disabled={loading}
              style={{ marginTop: 10, width: "100%", justifyContent: "center", padding: "16px", borderRadius: 14, fontSize: 15 }}
            >
              {loading
                ? <><span className="anim-spin" style={{ display: "inline-block", width: 16, height: 16, border: "2px solid rgba(255,255,255,0.3)", borderTopColor: "#fff", borderRadius: "50%" }} /> Authenticating...</>
                : "Authorize & Sign In"
              }
            </button>
          </form>

        </div>

        {/* Footer */}
        <div style={{ marginTop: 40, textAlign: "center" }}>
          <p className="t-small" style={{ opacity: 0.6, fontSize: 11, letterSpacing: "0.05em" }}>
            PREDICTION ENGINE · VERSION 2.4.0-STABLE
          </p>
          <div style={{ display: "flex", gap: 15, justifyContent: "center", marginTop: 8 }}>
            <span style={{ fontSize: 10, color: "#999" }}>Security Policy</span>
            <span style={{ fontSize: 10, color: "#999" }}>Infrastructure Status</span>
          </div>
          <p className="t-small" style={{ marginTop: 16, opacity: 0.4 }}>
            © {new Date().getFullYear()} @ BISAG-N · Government of India
          </p>
        </div>
      </div>
    </div>
  );
}
