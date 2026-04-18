import { useState, useEffect, useRef } from "react";
import { getJob } from "../../lib/api";

export default function JobProgress({ jobId, onDone, module }) {
  const [job, setJob] = useState(null);
  const wsRef = useRef(null);
  const pollRef = useRef(null);
  const logEndRef = useRef(null);

  // Auto-scroll logs
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [job?.log]);

  useEffect(() => {
    if (!jobId) return;

    // Try WebSocket first
    const wsUrl = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws?job=${jobId}`;
    try {
      wsRef.current = new WebSocket(wsUrl);
      wsRef.current.onmessage = (e) => {
        const data = JSON.parse(e.data);
        setJob(prev => ({ ...prev, ...data }));
        if (data.status === "done" || data.status === "failed") {
          onDone?.(data.status);
          wsRef.current?.close();
        }
      };
      wsRef.current.onerror = () => startPolling();
    } catch {
      startPolling();
    }

    // Fetch initial state
    getJob(jobId).then(d => setJob(d.job)).catch(() => {});

    return () => {
      wsRef.current?.close();
      clearInterval(pollRef.current);
    };
  }, [jobId]);

  const startPolling = () => {
    pollRef.current = setInterval(async () => {
      try {
        const d = await getJob(jobId);
        setJob(d.job);
        if (d.job.status === "done" || d.job.status === "failed") {
          clearInterval(pollRef.current);
          onDone?.(d.job.status);
        }
      } catch {}
    }, 2500);
  };

  if (!job) return (
    <div style={{ padding: 16, background: "#fafafa", borderRadius: 16, border: "1px solid #e5e5e7" }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", color: "#666", fontSize: 14 }}>
        <div style={{ width: 16, height: 16, border: "2px solid #e5e5e7", borderTopColor: "#000", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
        Connecting to job…
      </div>
    </div>
  );

  const statusColors = {
    pending: "#999", processing: "#0071e3", done: "#34c759", failed: "#ff3b30"
  };
  const statusBg = {
    pending: "rgba(153,153,153,0.08)",
    processing: "rgba(0,113,227,0.06)",
    done: "rgba(52,199,89,0.06)",
    failed: "rgba(255,59,48,0.06)"
  };

  return (
    <div style={{
      background: statusBg[job.status] || "#fafafa",
      border: `1px solid ${statusColors[job.status]}30`,
      borderRadius: 20, padding: 22, display: "flex", flexDirection: "column", gap: 14
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {job.status === "processing" && (
            <div style={{ width: 16, height: 16, border: "2px solid rgba(0,113,227,0.2)", borderTopColor: "#0071e3", borderRadius: "50%", animation: "spin 0.8s linear infinite", flexShrink: 0 }} />
          )}
          <span style={{ fontWeight: 600, fontSize: 15 }}>
            {job.status === "pending" ? "⏳ Job Queued"
              : job.status === "processing" ? "Processing…"
              : job.status === "done" ? "✓ Complete"
              : "✗ Failed"}
          </span>
        </div>
        <span className="badge" style={{
          background: statusBg[job.status],
          color: statusColors[job.status],
          border: `1px solid ${statusColors[job.status]}30`
        }}>
          {job.status}
        </span>
      </div>

      {/* Progress bar */}
      {(job.status === "processing" || job.status === "pending") && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
            <span style={{ fontSize: 12, color: "#666" }}>Progress</span>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{job.progress || 0}%</span>
          </div>
          <div style={{ height: 6, background: "rgba(0,113,227,0.12)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{
              height: "100%", borderRadius: 3, background: "#0071e3",
              width: `${job.progress || 5}%`,
              transition: "width 0.5s ease",
              animation: job.progress < 10 ? "anim-pulse 2s ease infinite" : undefined
            }} />
          </div>
        </div>
      )}

      {/* Log output */}
      {job.log && (
        <div style={{
          background: "rgba(0,0,0,0.03)", borderRadius: 12,
          padding: "12px 16px", fontFamily: "monospace", fontSize: 12,
          color: job.status === "failed" ? "#ff3b30" : "#444",
          lineHeight: 1.6, maxHeight: 160, overflowY: "auto",
          whiteSpace: "pre-wrap", wordBreak: "break-word"
        }}>
          {job.log}
          <div ref={logEndRef} />
        </div>
      )}

      {/* Meta */}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {[
          { label: "Job ID", value: job.id?.slice(0, 8) + "…" },
          { label: "Module", value: job.module },
          { label: "Started", value: job.created_at ? new Date(job.created_at).toLocaleTimeString() : "—" },
        ].map(m => (
          <div key={m.label}>
            <div style={{ fontSize: 10, color: "#aaa", fontWeight: 600, letterSpacing: "0.08em" }}>{m.label}</div>
            <div style={{ fontSize: 12, fontWeight: 500, color: "#444", marginTop: 2 }}>{m.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
