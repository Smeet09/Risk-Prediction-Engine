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
    <div ref={logoRef} style={{ textAlign: "center", marginBottom: 40 }}>
      <div style={{
        width: 86, height: 86, borderRadius: 16,
        background: "#fff", margin: "0 auto 20px",
        display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: "0 4px 12px rgba(0,0,0,0.05)"
      }}>
        <img src="/bisag_logo.png" alt="BISAG Logo" style={{ width: "100%", height: "100%", objectFit: "contain", borderRadius: 16 }} />
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
          { id: "crop",     label: "🌾 Crop Prediction" }
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
    <div style={{ position: "relative", height: "100vh", overflow: "hidden", background: "#f8f8f9" }}>
      <TopographyBackground />

      {/* Overlay gradient */}
      <div style={{
        position: "fixed", inset: 0, zIndex: 1,
        background: "linear-gradient(135deg, rgba(248,248,249,0.85) 0%, rgba(255,255,255,0.75) 100%)"
      }} />

      {/* Centered Auth Card */}
      <div style={{
        position: "relative", zIndex: 2,
        height: "100vh",
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        padding: "24px"
      }}>
        {/* Logo / Brand */}
        <Logo />
        <p style={{ color: "#999", fontSize: 14, marginTop: -24, marginBottom: 32 }}>Sign in</p>

        {/* Form Card */}
        <div className="glass" style={{
          width: "100%", maxWidth: 400,
          padding: "40px", borderRadius: 28,
        }}>
          {/* System Selector */}
          <SystemSelector />

          {/* Role Toggle */}
          <div className="auth-field" style={{
            display: "grid", gridTemplateColumns: "1fr 1fr",
            gap: 6, marginBottom: 28,
            background: "#f0f0f1", borderRadius: 12, padding: 4
          }}>
            {["user", "admin"].map((r) => (
              <button
                key={r}
                onClick={() => setForm(f => ({
                  ...f, role: r,
                  email: r === "admin" ? "admin@aether.local" : "user@aether.local",
                  password: "password"
                }))}
                style={{
                  padding: "10px", borderRadius: 9, border: "none", cursor: "pointer",
                  fontFamily: "inherit", fontSize: 13, fontWeight: 500,
                  transition: "all 0.2s ease",
                  background: form.role === r ? "#fff" : "transparent",
                  color: form.role === r ? "#000" : "#999",
                  boxShadow: form.role === r ? "0 2px 8px rgba(0,0,0,0.08)" : "none"
                }}
              >
                {r === "admin" ? "⚙ Admin" : "◉ User"}
              </button>
            ))}
          </div>

          <form ref={formRef} onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="auth-field form-group">
              <label className="form-label">Email Address</label>
              <input
                className="input"
                type="email"
                placeholder="you@example.com"
                value={form.email}
                onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                required
                autoComplete="email"
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
              style={{ marginTop: 8, width: "100%", justifyContent: "center", padding: "15px" }}
            >
              {loading
                ? <><span className="anim-spin" style={{ display: "inline-block", width: 16, height: 16, border: "2px solid rgba(255,255,255,0.3)", borderTopColor: "#fff", borderRadius: "50%" }} /> Signing in...</>
                : "Sign In →"
              }
            </button>
          </form>

        </div>

        {/* Footer */}
        <p className="t-small" style={{ marginTop: 32, opacity: 0.5 }}>
          Prediction Engine © {new Date().getFullYear()} @ BISAG-N
        </p>
      </div>
    </div>
  );
}
