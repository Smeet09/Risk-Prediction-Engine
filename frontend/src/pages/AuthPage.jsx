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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const formRef = useRef();
  const logoRef = useRef();

  // Redirect if already logged in
  useEffect(() => {
    if (token && user) {
      navigate(user.role === "admin" ? "/admin" : "/hub", { replace: true });
    }
  }, [token, user]);

  // GSAP entrance
  useEffect(() => {
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
      navigate(data.user.role === "admin" ? "/admin" : "/hub", { replace: true });
    } catch (err) {
      setError(err.response?.data?.error || "Login failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

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
        <div ref={logoRef} style={{ textAlign: "center", marginBottom: 48 }}>
          <div style={{
            width: 52, height: 52, borderRadius: 16,
            background: "#000", margin: "0 auto 20px",
            display: "flex", alignItems: "center", justifyContent: "center"
          }}>
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
                stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <p className="t-label" style={{ letterSpacing: "0.2em", marginBottom: 6 }}>AETHER PLATFORM</p>
          <h1 className="t-title" style={{ fontSize: 32, letterSpacing: "-0.035em" }}>
            Disaster Intelligence
          </h1>
          <p style={{ color: "#999", fontSize: 14, marginTop: 8 }}>
            Sign in to access the management system
          </p>
        </div>

        {/* Form Card */}
        <div className="glass" style={{
          width: "100%", maxWidth: 400,
          padding: "40px", borderRadius: 28,
        }}>
          {/* Role Toggle */}
          <div className="auth-field" style={{
            display: "grid", gridTemplateColumns: "1fr 1fr",
            gap: 6, marginBottom: 28,
            background: "#f0f0f1", borderRadius: 12, padding: 4
          }}>
            {["user", "admin"].map((r) => (
              <button
                key={r}
                onClick={() => setForm(f => ({ ...f, role: r,
                  email: r === "admin" ? "admin@aether.local" : "user@aether.local",
                  password: "password" }))}
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
                ? <><span className="anim-spin" style={{ display:"inline-block",width:16,height:16,border:"2px solid rgba(255,255,255,0.3)",borderTopColor:"#fff",borderRadius:"50%" }} /> Signing in...</>
                : "Sign In →"
              }
            </button>
          </form>

          {/* Demo creds hint */}
          <p style={{ marginTop: 20, fontSize: 12, color: "#bbb", textAlign: "center" }}>
            Demo — admin@aether.local / user@aether.local · <em>password</em>
          </p>
        </div>

        {/* Footer */}
        <p className="t-small" style={{ marginTop: 32, opacity: 0.5 }}>
          Aether-Disaster © {new Date().getFullYear()} · GIS Intelligence Platform
        </p>
      </div>
    </div>
  );
}
