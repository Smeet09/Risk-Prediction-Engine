import React, { useState, useEffect } from "react";
import axios from "axios";

// Node colors mapping based on node type
const NODE_COLORS = {
  orchestrator: "#7C3AED", 
  data_ingestion: "#1D4ED8",
  gis: "#065F46",
  crop: "#166534",
  ml: "#92400E",
  alert: "#B91C1C",
  unknown: "#6B7280"
};

export default function AgentPipeline() {
  const [pipelineData, setPipelineData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchPipeline = async () => {
    try {
      const res = await axios.get("http://localhost:4000/api/agents/history", {
        headers: { Authorization: `Bearer ${localStorage.getItem("aether_token")}` }
      });
      setPipelineData(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPipeline();
    const iv = setInterval(fetchPipeline, 3000);
    return () => clearInterval(iv);
  }, []);

  const triggerRun = async () => {
    try {
      await axios.post("http://localhost:4000/api/agents/run", {}, { 
        headers: { Authorization: `Bearer ${localStorage.getItem("aether_token")}` }
      });
      fetchPipeline();
    } catch (err) {
      alert("Failed to start run");
    }
  };

  if (loading) return <div>Loading pipeline...</div>;

  const activeRun = pipelineData?.runs?.[0];
  const logs = pipelineData?.latest_logs || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 className="t-heading">Agent Pipeline (Orchestrator)</h2>
          <p style={{ color: "#666", fontSize: 14 }}>Real-time view of autonomous agent activity</p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button className="btn btn-secondary" onClick={() => window.open('http://localhost:5678', '_blank')}>
            Open n8n Canvas
          </button>
          <button className="btn btn-primary" onClick={triggerRun} disabled={activeRun?.status === 'running'}>
            {activeRun?.status === 'running' ? 'Pipeline Running...' : 'Force Run Now'}
          </button>
        </div>
      </div>

      <div style={{ 
        background: "#fff", border: "1px solid #e5e5e7", borderRadius: 16, 
        padding: "30px", display: "flex", flexWrap: "wrap", gap: 20 
      }}>
        {/* Simplified DAG View */}
        {['orchestrator', 'data_ingestion', 'gis', 'ml', 'alert'].map((agentName, idx) => {
          const stepLog = logs.find(l => l.agent_name === agentName);
          const isActive = activeRun?.status === 'running' && stepLog?.status !== 'success';
          const isDone = stepLog?.status === 'success';
          const isFailed = stepLog?.status === 'failed';
          
          let borderColor = "#e5e5e7";
          if (isActive) borderColor = NODE_COLORS[agentName] || NODE_COLORS.unknown;
          if (isDone) borderColor = "#34c759";
          if (isFailed) borderColor = "#ff3b30";

          return (
            <React.Fragment key={agentName}>
              <div style={{
                border: `2px solid ${borderColor}`,
                borderRadius: 12, padding: "16px 20px", width: 200,
                background: isActive ? `${borderColor}10` : "#fafafa",
                transition: "all 0.3s"
              }}>
                <div style={{ fontSize: 12, color: "#666", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
                  Agent {idx}
                </div>
                <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4 }}>
                  {agentName.replace('_', ' ').toUpperCase()}
                </div>
                <div style={{ fontSize: 12, color: isDone ? "#34c759" : (isActive ? NODE_COLORS[agentName] : "#999") }}>
                  {isFailed ? "Failed" : (isDone ? "Completed" : (isActive ? "Processing..." : "Waiting"))}
                </div>
              </div>
              
              {idx < 4 && (
                <div style={{ display: "flex", alignItems: "center", color: "#ccc", fontSize: 24 }}>
                  →
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>

      <div style={{ background: "#fff", border: "1px solid #e5e5e7", borderRadius: 16, overflow: "hidden" }}>
        <div style={{ padding: "16px 24px", borderBottom: "1px solid #e5e5e7", display: "flex", justifyContent: "space-between" }}>
          <h3 style={{ fontSize: 15, fontWeight: 700 }}>Live Agent Logs</h3>
          </div>
          <div style={{ background: "#1e1e1e", color: "#00ff00", padding: "16px", height: "180px", overflowY: "auto", fontFamily: "monospace", fontSize: 13 }}>
            {logs.length === 0 ? "Waiting for agents to initialize..." : logs.map((log, i) => (
              <div key={i} style={{ marginBottom: "6px" }}>
                <span style={{ color: "#888" }}>[{new Date(log.started_at).toLocaleTimeString()}]</span>{" "}
                <span style={{ color: log.status === 'failed' ? '#ff4d4f' : '#61dafb', fontWeight: 'bold' }}>[{log.agent_name.toUpperCase()}]</span>{" "}
                {log.current_step} {log.error_message && <span style={{ color: '#ff4d4f' }}> - {log.error_message}</span>}
              </div>
            ))}
          </div>
      </div>

      <div style={{ background: "#fff", border: "1px solid #e5e5e7", borderRadius: 16, overflow: "hidden" }}>
        <div style={{ padding: "16px 24px", borderBottom: "1px solid #e5e5e7", display: "flex", justifyContent: "space-between" }}>
          <h3 style={{ fontSize: 15, fontWeight: 700 }}>Run History</h3>
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "#fafafa" }}>
              {["Run ID", "Trigger", "Started", "Status"].map(h => (
                <th key={h} style={{ padding: "12px 24px", textAlign: "left", fontWeight: 600, color: "#666" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(pipelineData?.runs || []).map(r => (
              <tr key={r.run_id} style={{ borderBottom: "1px solid #f0f0f0" }}>
                <td style={{ padding: "14px 24px", fontFamily: "monospace" }}>{r.run_id.split('-')[0]}</td>
                <td style={{ padding: "14px 24px" }}>{r.triggered_by}</td>
                <td style={{ padding: "14px 24px" }}>{new Date(r.triggered_at).toLocaleString()}</td>
                <td style={{ padding: "14px 24px" }}>
                  <span style={{ 
                    padding: "4px 8px", borderRadius: 4, fontSize: 11, fontWeight: 600, textTransform: "uppercase",
                    background: r.status === 'success' ? '#d4edda' : (r.status === 'failed' ? '#f8d7da' : '#fff3cd'),
                    color: r.status === 'success' ? '#155724' : (r.status === 'failed' ? '#721c24' : '#856404')
                  }}>
                    {r.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
