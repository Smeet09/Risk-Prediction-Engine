# =============================================================================
#  main_app.py  —  AgriIntel v2 · 4-Tab Satellite Crop Intelligence System
#
#  Pipeline:
#    TIFF upload → band check → crop/stage classify → area compute
#    → lat/lon/date extract (priority: EXIF/GeoTIFF > acquisition tags > filename)
#    → NASA POWER daily fetch (sowing→harvest)
#    → stage-wise ideal weather comparison (data_bank_ref.xlsx)
#    → stage-wise deviation with Key_Parameter weighting
#    → per-crop weather risk + yield prediction (Monte Carlo internal only)
#
#  4 Tabs: Agriculture Map | Crop Classification | Growth Stage | Weather & Yield Forecast
#  Historic Analysis tab: REMOVED
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import re as _re
import io, json, warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st
from PIL import Image
from datetime import date, timedelta

warnings.filterwarnings("ignore")
Image.MAX_IMAGE_PIXELS = None

# ── Classifier imports ────────────────────────────────────────────────────────
try:
    from nn_classifier import NNClassifier, SPECTRAL_FEATURES
    NN_AVAILABLE = True
except ImportError:
    NN_AVAILABLE = False

try:
    from xgb_crop_classifier import XGBCropClassifier
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

# ── Pipeline imports ──────────────────────────────────────────────────────────
try:
    from pipeline.nasa_power import fetch_nasa_power, aggregate_weather, NASA_PARAMS, PARAM_LABELS
    from pipeline.sowing_harvest import (
        estimate_sowing_harvest, get_crop_stages, get_ideal_weather_for_crop,
        get_ideal_weather_from_xlsx, ideal_to_nasa_param_map,
    )
    from pipeline.weather_risk import (
        compute_weather_risk_score, compute_stagewise_risk, aggregate_crop_risk,
        get_risk_color, get_risk_advisory,
    )
    from pipeline.yield_pipeline import run_yield_forecast
    PIPELINE_OK = True
except ImportError as _pe:
    PIPELINE_OK = False

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="AgriIntel — Satellite Crop Intelligence", page_icon="🛰️")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;700&display=swap');

/* ── Global reset to match React light theme ── */
html, body, [class*="css"], [data-testid="stAppViewContainer"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    background-color: #f8f8f9 !important;
    color: #000000 !important;
}

/* Main content area */
.main .block-container {
    padding-top: 1.5rem;
    padding-bottom: 2rem;
    max-width: 1400px;
    background-color: #f8f8f9;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background-color: #ffffff !important;
    border-right: 1px solid rgba(0,0,0,0.06) !important;
    box-shadow: 2px 0 12px rgba(0,0,0,0.04) !important;
}
[data-testid="stSidebar"] * { color: #000000 !important; }
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] label { color: #444444 !important; }

/* Glass cards — matches .glass in React */
.stat-box {
    background: rgba(255,255,255,0.72);
    border: 1px solid rgba(0,0,0,0.06);
    border-radius: 16px;
    padding: 16px 20px;
    text-align: center;
    box-shadow: 0 8px 40px rgba(0,0,0,0.06), 0 1px 3px rgba(0,0,0,0.04);
    backdrop-filter: blur(30px);
}
.dash-card {
    background: rgba(255,255,255,0.72);
    border: 1px solid rgba(0,0,0,0.06);
    border-radius: 16px;
    padding: 14px 18px;
    text-align: center;
    box-shadow: 0 8px 40px rgba(0,0,0,0.06), 0 1px 3px rgba(0,0,0,0.04);
}
.pipeline-card {
    background: rgba(255,255,255,0.72);
    border: 1px solid rgba(0,0,0,0.06);
    border-radius: 12px;
    padding: 14px 18px;
    margin-bottom: 10px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.05);
}
.crop-block {
    background: rgba(255,255,255,0.72);
    border: 1px solid rgba(0,0,0,0.06);
    border-radius: 14px;
    padding: 18px 22px;
    margin-bottom: 20px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.05);
}
.stage-row {
    background: #f0f0f1;
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 6px;
}

/* Labels & values */
.stat-label {
    color: #999999;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 4px;
    font-family: 'JetBrains Mono', monospace;
}
.stat-value { font-size: 1.7rem; font-weight: 700; line-height: 1.1; color: #000000; }
.stat-sub   { color: #999999; font-size: 0.78rem; margin-top: 4px; font-family: 'JetBrains Mono', monospace; }

/* Accent colours */
.green  { color: #39bd97; }
.orange { color: #ff9f0a; }
.white  { color: #000000; }
.yellow { color: #ff9f0a; }
.blue   { color: #0071e3; }
.gray   { color: #999999; }

/* Info / warning banners */
.info-banner {
    background: rgba(0,113,227,0.06);
    border-left: 4px solid #0071e3;
    border-radius: 8px;
    padding: 12px 16px;
    color: #0071e3;
    font-size: 0.86rem;
    margin-bottom: 1rem;
}
.warn-banner {
    background: rgba(255,159,10,0.08);
    border-left: 4px solid #ff9f0a;
    border-radius: 8px;
    padding: 12px 16px;
    color: #a05e00;
    font-size: 0.84rem;
    margin-bottom: 1rem;
}

/* Risk badges */
.risk-badge    { display:inline-block; padding:8px 28px; border-radius:30px; font-size:1.1rem; font-weight:800; letter-spacing:1px; text-transform:uppercase; }
.risk-Low      { background:#e8f5e9; color:#1b5e20; border:2.5px solid #2e7d32; }
.risk-Moderate { background:#fff8e1; color:#bf360c; border:2.5px solid #e65100; }
.risk-High     { background:#fce4ec; color:#b71c1c; border:2.5px solid #e53935; }
.risk-Extreme  { background:#ff3b3015; color:#c0392b; border:2.5px solid #ff3b30; }

/* Primary button — matches React .btn-primary */
.stButton > button {
    background: #000000 !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-size: 0.95rem !important;
    padding: 0.65rem 1.5rem !important;
    width: 100% !important;
    transition: background 0.2s ease !important;
    font-family: 'Inter', sans-serif !important;
}
.stButton > button:hover {
    background: #222222 !important;
}
.stButton > button p,
.stButton > button span,
.stButton > button div {
    color: #ffffff !important;
}

/* Inputs */
.stTextInput input, .stSelectbox select, [data-baseweb="select"] {
    background: #ffffff !important;
    border: 1px solid rgba(0,0,0,0.12) !important;
    border-radius: 10px !important;
    color: #000000 !important;
    font-family: 'Inter', sans-serif !important;
}
.stTextInput input:focus {
    border-color: #39bd97 !important;
    box-shadow: 0 0 0 3px rgba(57,189,151,0.12) !important;
}

/* Tabs */
[data-baseweb="tab-list"] {
    background: #f0f0f1 !important;
    border-radius: 12px !important;
    padding: 4px !important;
    gap: 4px !important;
}
[data-baseweb="tab"] {
    border-radius: 9px !important;
    font-weight: 500 !important;
    color: #999999 !important;
    font-family: 'Inter', sans-serif !important;
}
[aria-selected="true"][data-baseweb="tab"] {
    background: #ffffff !important;
    color: #000000 !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08) !important;
}

/* Expander */
[data-testid="stExpander"] {
    background: rgba(255,255,255,0.72) !important;
    border: 1px solid rgba(0,0,0,0.06) !important;
    border-radius: 12px !important;
}

/* File uploader */
[data-testid="stFileUploader"] {
    background: #ffffff !important;
    border: 1.5px dashed rgba(0,0,0,0.15) !important;
    border-radius: 12px !important;
}

/* Divider */
hr { border-color: rgba(0,0,0,0.06) !important; }

/* Metric labels */
[data-testid="stMetricLabel"] { color: #999999 !important; font-size: 0.75rem !important; }
[data-testid="stMetricValue"] { color: #000000 !important; font-weight: 700 !important; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATION GATE
# Every entry point — direct URL or redirect from React — must pass through here.
# ═══════════════════════════════════════════════════════════════════════════════

_BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:4000")


def _validate_token(token: str):
    """Call the Node backend to confirm the JWT is valid. Returns user dict or None."""
    try:
        import requests as _req
        r = _req.get(
            f"{_BACKEND_URL}/api/auth/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _call_login(email: str, password: str):
    """POST credentials to the Node backend. Returns (token, user) or raises."""
    import requests as _req
    r = _req.post(
        f"{_BACKEND_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json()
        return data["token"], data["user"]
    raise ValueError(r.json().get("error", "Login failed"))


def _set_auth(tok: str, usr: dict):
    """Store auth state in both session_state AND query_params so refresh keeps the user in."""
    st.session_state["_auth"]  = True
    st.session_state["_token"] = tok
    st.session_state["_user"]  = usr.get("name") or usr.get("email") or "User"
    st.session_state["_role"]  = usr.get("role", "user")
    try:
        # Persist token in URL — survives F5 / tab refresh
        st.query_params["token"] = tok
    except Exception:
        pass


def _clear_auth():
    for _k in ["_auth", "_token", "_user", "_role"]:
        st.session_state.pop(_k, None)
    try:
        st.query_params.clear()
    except Exception:
        pass


def _show_login_page(error_msg: str = ""):
    """Render a full-page login form and call st.stop() so nothing else renders."""
    st.markdown("""
    <div style="max-width:420px;margin:60px auto;">
      <div style="text-align:center;margin-bottom:28px">
        <div style="font-size:2.6rem">🌾</div>
        <div style="color:#60a5fa;font-weight:800;font-size:1.5rem;margin-top:8px">AgriIntel</div>
        <div style="color:#8899aa;font-size:0.9rem;margin-top:4px">Crop Intelligence System · Sign In</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        if error_msg:
            st.error(error_msg)
        with st.form("_agri_login_form", clear_on_submit=False):
            email    = st.text_input("Email", placeholder="you@example.com")
            password = st.text_input("Password", type="password", placeholder="••••••••")
            submitted = st.form_submit_button("Sign In →", use_container_width=True)
            if submitted:
                if not email or not password:
                    st.error("Please enter your email and password.")
                else:
                    try:
                        tok, usr = _call_login(email, password)
                        _set_auth(tok, usr)
                        st.rerun()
                    except ValueError as _ve:
                        st.error(str(_ve))
                    except Exception:
                        st.error("Cannot reach the platform server. Make sure the backend is running.")

        st.markdown(
            '<div style="text-align:center;margin-top:16px;font-size:0.8rem;color:#8899aa">'
            '<a href="http://localhost:3001" style="color:#60a5fa;text-decoration:none">'
            "← Back to Platform Sign-In</a></div>",
            unsafe_allow_html=True,
        )
    st.stop()


# ── Auth check: session_state first (fast), then URL token (survives refresh) ─
if not st.session_state.get("_auth"):
    try:
        _tok_param = st.query_params.get("token")
    except Exception:
        _tok_param = None

    if _tok_param:
        _result = _validate_token(_tok_param)
        if _result and _result.get("valid"):
            _u = _result.get("user", {})
            _set_auth(_tok_param, _u)
            st.rerun()
        else:
            # Token in URL is expired/invalid — clear it and show login
            try:
                st.query_params.clear()
            except Exception:
                pass
            _show_login_page("Your session has expired. Please sign in again.")
    else:
        _show_login_page()

# ── Signed-in user indicator in sidebar ──────────────────────────────────────
_signed_in_user = st.session_state.get("_user", "User")
_signed_in_role = st.session_state.get("_role", "user")


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

CONF_THRESHOLD  = 0.30
NDVI_MIN_KHARIF = 0.15
NDVI_MIN_RABI   = 0.10

CROP_COLORS_9 = {
    'rice':[144,238,144,220],'wheat':[255,215,0,220],'groundnut':[245,158,11,220],
    'cotton':[59,130,246,220],'sugarcane':[34,139,34,220],'maize':[255,165,0,220],
    'soybean':[107,142,35,220],'mustard':[255,255,0,220],
    'others':[156,163,175,160],
}
CROP_HEX = {
    'rice':'#90EE90','wheat':'#FFD700','groundnut':'#F59E0B','cotton':'#3B82F6',
    'sugarcane':'#228B22','maize':'#FFA500','soybean':'#6B8E23','mustard':'#FFFF00',
    'others':'#9CA3AF',
}
CROP_EMOJI = {
    'rice':'🌾','wheat':'🌿','groundnut':'🥜','cotton':'🌸','sugarcane':'🎋',
    'maize':'🌽','soybean':'🫘','mustard':'🌼','others':'⬜',
}
STAGE_COLORS = {
    'Germination':[250,199,75,200],'Vegetative':[99,190,123,200],
    'Peak Canopy':[29,158,117,200],'Maturity':[216,90,48,200],
    'Senescence':[136,135,128,200],'Nursery':[180,220,140,200],
    'Transplanting':[120,200,100,200],'Harvest':[220,160,60,200],
}

MY_DATA_PATH      = os.path.join(os.path.dirname(__file__), "my_data.csv")
DATABANK_XLSX     = os.path.join(os.path.dirname(__file__), "data_bank_ref_new.xlsx")
DATABANK_CSV_NEW  = os.path.join(os.path.dirname(__file__), "data_bank_reference_updated.csv")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def try_import_rasterio():
    try:
        import rasterio; return rasterio
    except ImportError:
        return None

@st.cache_resource(show_spinner=False)
def load_nn_classifier():
    if not NN_AVAILABLE: return None
    try:
        clf = NNClassifier()
        # Use new updated CSV as primary reference
        csv_path = DATABANK_CSV_NEW if os.path.exists(DATABANK_CSV_NEW) else "data_bank_reference.csv"
        clf.load(data_bank_csv=csv_path, gee_csv=None, mode="data_bank")
        return clf
    except Exception as e:
        st.error(f"❌ NN Classifier failed: {e}")
        return None

@st.cache_resource(show_spinner=False)
def load_xgb_classifier():
    if not XGB_AVAILABLE: return None
    if not os.path.exists("xgb_crop_model.pkl"): return None
    try:
        clf = XGBCropClassifier(); clf.load("xgb_crop_model.pkl"); return clf
    except: return None

@st.cache_data(show_spinner=False)
def load_my_data():
    if os.path.exists(MY_DATA_PATH): return pd.read_csv(MY_DATA_PATH)
    return None

@st.cache_data(show_spinner=False)
def get_location_name(lon, lat):
    try:
        import urllib.request
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&zoom=8"
        req = urllib.request.Request(url, headers={"User-Agent": "AgriIntel/2.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        addr = data.get("address", {}); parts = []
        for k in ["village","town","city","district","state_district","state"]:
            if k in addr and addr[k] not in parts:
                parts.append(addr[k])
                if len(parts) == 3: break
        return ", ".join(parts) if parts else data.get("display_name","").split(",")[0]
    except: return None

def sdfn(a, b, fill=0.0):
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(np.abs(b) > 1e-6, a / b, fill).astype(np.float32)

def ndfn(a, b): return sdfn(a - b, a + b, 0.0)

def compute_features(tiff_data):
    d = tiff_data.astype(np.float32)
    if d.max() > 100: d = d / 10000.0
    B2=d[0].ravel(); B3=d[1].ravel(); B4=d[2].ravel()
    B5=d[3].ravel(); B8=d[6].ravel(); B8A=d[7].ravel()
    B11=d[8].ravel(); B12=d[9].ravel()
    NDVI=ndfn(B8,B4); NDRE=ndfn(B8,B5); GNDVI=ndfn(B8,B3)
    NDWI=ndfn(B3,B8); LSWI=ndfn(B8,B11); NDMI=ndfn(B8A,B11)
    NDBI=ndfn(B11,B8); BSI=ndfn(B11+B4,B8+B2)
    EVI=np.clip(2.5*sdfn(B8-B4,B8+6*B4-7.5*B2+1),-1,10)
    SAVI=np.clip(sdfn(B8-B4,B8+B4+0.5)*1.5,-1,10)
    CIre=sdfn(B8A,B5)-1; RVI=sdfn(B8,B4); SR=sdfn(B11,B8A)
    feat = np.column_stack([B4,B8,B8A,B11,B12,NDVI,NDRE,GNDVI,EVI,SAVI,CIre,RVI,NDWI,LSWI,NDMI,BSI,NDBI,SR])
    return feat, NDVI, NDWI

def enhance_image(img, method="📊 Stretch 2–98%"):
    arr = np.array(img, dtype=np.float32)
    if method == "🔲 No enhancement": return img
    p_map = {"📊 Stretch 2–98%":(2,98),"📊 Stretch 1–99%":(1,99),"⚡ Stretch 0.5–99.5%":(0.5,99.5)}
    lo, hi = p_map.get(method, (2,98))
    out = np.zeros_like(arr)
    for c in range(3):
        ch = arr[:,:,c]; l,h = np.percentile(ch,lo), np.percentile(ch,hi)
        out[:,:,c] = np.clip((ch-l)/(h-l+1e-9)*255,0,255) if h>l else ch
    return Image.fromarray(out.astype(np.uint8))

def fa(ha, unit="Hectares (ha)"):
    if ha is None: return "—"
    if unit == "Hectares (ha)":             return f"{ha:,.1f} ha"
    elif unit == "Square metres (m²)":      return f"{ha*10000:,.0f} m²"
    elif unit == "Square kilometres (km²)": return f"{ha/100:,.3f} km²"
    elif unit == "Acres":                   return f"{ha*2.47105:,.1f} ac"
    return f"{ha:,.1f} ha"

def dark_chart(fig, ax):
    fig.patch.set_facecolor("#0f1923"); ax.set_facecolor("#131f2e")
    ax.tick_params(colors="white"); ax.spines[:].set_color("#2a3a4a")

def get_all_detected_crops(crop_map_2d, stage_map_2d, conf_map_2d):
    """Return list of (crop, dom_stage, pixel_count, avg_conf) for all detected crops."""
    crops = []
    for cname in CROP_COLORS_9:
        if cname == 'others': continue
        mask = (crop_map_2d == cname)
        n = int(mask.sum())
        if n == 0: continue
        sv = stage_map_2d[mask]
        unique, counts = np.unique(sv[sv != ''], return_counts=True)
        top_stage = unique[counts.argmax()] if len(unique) > 0 else 'Vegetative'
        avg_conf  = float(conf_map_2d[mask].mean())
        crops.append((cname, top_stage, n, avg_conf))
    crops.sort(key=lambda x: -x[2])  # sort by pixel count desc
    return crops


# ═══════════════════════════════════════════════════════════════════════════════
# TIFF DATE EXTRACTION (priority: EXIF/GeoTIFF > acquisition tags > filename)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_tiff_date(ds, filename: str):
    """
    Extract image acquisition date from TIFF metadata with priority:
    1. GeoTIFF acquisition tags (TIFFTAG_DATETIME, ACQUISITION_DATE, etc.)
    2. Dataset-level tags / description XML
    3. Subdataset metadata
    4. Filename pattern (YYYYMMDD)
    Returns a date object or None.
    """
    import re

    def _parse_date_str(s):
        """Try to parse YYYY-MM-DD or YYYY:MM:DD or YYYYMMDD from a string."""
        patterns = [
            r'(\d{4})[:\-/](\d{2})[:\-/](\d{2})',   # YYYY-MM-DD / YYYY:MM:DD
            r'(\d{4})(\d{2})(\d{2})',                 # YYYYMMDD
        ]
        for pat in patterns:
            m = re.search(pat, s)
            if m:
                try:
                    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    continue
        return None

    # Priority 1 & 2: TIFF metadata tags
    priority_keys = [
        # Standard TIFF / EXIF
        'TIFFTAG_DATETIME', 'DATE_TIME', 'DateTime',
        # GeoTIFF acquisition
        'ACQUISITION_DATE', 'ACQUISITION_DATETIME', 'AcquisitionDate',
        'IMAGE_DATE', 'image_date', 'date', 'Date',
        # Sentinel-2 / ESA metadata
        'PRODUCT_START_TIME', 'PRODUCT_STOP_TIME',
        'DATATAKE_SENSING_START', 'GENERATION_TIME',
    ]

    try:
        tags = ds.tags()
        for key in priority_keys:
            if key in tags:
                d = _parse_date_str(tags[key])
                if d: return d

        # Scan all tags for any date-like value
        for key, val in tags.items():
            if any(k in key.upper() for k in ['DATE', 'TIME', 'ACQUI', 'SENSING']):
                d = _parse_date_str(str(val))
                if d: return d
    except Exception:
        pass

    # Priority 2: band-level / namespace tags
    try:
        for ns in ds.tag_namespaces():
            ns_tags = ds.tags(ns)
            for key in priority_keys:
                if key in ns_tags:
                    d = _parse_date_str(str(ns_tags[key]))
                    if d: return d
    except Exception:
        pass

    # Priority 3: description field (may contain XML with dates)
    try:
        desc = ds.tags().get("TIFFTAG_IMAGEDESCRIPTION", "") or ""
        if desc:
            d = _parse_date_str(desc)
            if d: return d
    except Exception:
        pass

    # Priority 4: filename — e.g. S2B_20240115_... or file_20240115.tif
    d = _parse_date_str(filename)
    if d: return d

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════════════════

_logo_path = os.path.join(os.path.dirname(__file__), "company_logo.png")
if os.path.exists(_logo_path):
    _logo_img = Image.open(_logo_path)
st.markdown("# AgriIntel — Satellite Crop Intelligence System")
st.markdown("**Upload Sentinel-2 TIFF → Crop Classification → Stage-wise Weather Risk → Yield Forecast**")
st.markdown("---")

with st.expander("ℹ️ How this system works", expanded=False):
    st.markdown("""
| Step | What happens |
|---|---|
| 1. Upload | Sentinel-2 TIFF loaded; band count detected |
| 2. Band check | 10-band → full pipeline · <10 bands → agri/non-agri only |
| 3. Classification | 18 spectral features → NN/XGBoost → crop type + growth stage |
| 4. Metadata | Lat/Lon + image date extracted (EXIF/GeoTIFF priority, then filename) |
| 5. Sowing/Harvest | Stage-wise duration from data_bank_ref.xlsx |
| 6. NASA POWER | 7 daily weather variables fetched for full crop season |
| 7. Stage-wise Risk | Actual vs ideal per stage · Key_Parameter weighted · consistent final category |
| 8. Yield Forecast | XGBoost + Monte Carlo internally → current-year yield prediction |

**All classified crops** get individual weather risk + yield forecast in Tab 4.
""")

rasterio = try_import_rasterio()
if rasterio is None:
    st.warning("⚠️ rasterio not installed. Run: `pip install rasterio`")

# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════

if os.path.exists(_logo_path):
    st.sidebar.markdown(
        '<div style="display:flex;justify-content:center;margin-bottom:8px">'
        f'<img src="data:image/png;base64,{__import__("base64").b64encode(open(_logo_path,"rb").read()).decode()}" width="120"/>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("---")
st.sidebar.header("⚙️ Settings")

# Signed-in user card
st.sidebar.markdown(
    f'<div style="background:rgba(57,189,151,0.1);border:1px solid rgba(57,189,151,0.25);'
    f'border-radius:10px;padding:10px 12px;margin-bottom:4px;">'
    f'<div style="font-size:0.75rem;color:#8899aa;letter-spacing:0.8px;text-transform:uppercase">Signed in as</div>'
    f'<div style="font-weight:600;font-size:0.9rem;color:#39bd97;margin-top:2px">{_signed_in_user}</div>'
    f'<div style="font-size:0.72rem;color:#8899aa;margin-top:1px">'
    f'{"Administrator" if _signed_in_role == "admin" else "User"}</div>'
    f'</div>',
    unsafe_allow_html=True,
)
if st.sidebar.button("Sign Out", use_container_width=True):
    _clear_auth()
    st.rerun()

st.sidebar.markdown("---")

st.sidebar.subheader("🧠 Classifier Engine")
xgb_model_ok = os.path.exists("xgb_crop_model.pkl")
engine_opts = ["📚 Nearest Neighbour"]
if XGB_AVAILABLE and xgb_model_ok:
    engine_opts.append("🎯 XGBoost (52% accuracy)")
engine_sel = st.sidebar.radio("Engine", engine_opts, label_visibility="collapsed")
clf_engine = "xgb" if "XGBoost" in engine_sel and XGB_AVAILABLE and xgb_model_ok else "nn"

st.sidebar.markdown("---")
st.sidebar.subheader("📐 Area Calibration")
scale_method = st.sidebar.radio("Cal", [
    "📊 Percentage only", "📏 Pixel size (m/px)", "🗺️ Known region area (ha)"],
    label_visibility="collapsed")
pixel_size_m = None; known_total_ha = None
if scale_method == "📏 Pixel size (m/px)":
    pixel_size_m = st.sidebar.number_input("m/px", 0.01, 5000.0, 10.0, 0.5)
    st.sidebar.caption("Sentinel-2 = 10m/px")
elif scale_method == "🗺️ Known region area (ha)":
    known_total_ha = st.sidebar.number_input("Total ha", 0.1, 10_000_000.0, 1000.0)

st.sidebar.markdown("---")
st.sidebar.subheader("📏 Area Unit")
area_unit = st.sidebar.radio("Unit",
    ["Hectares (ha)", "Square metres (m²)", "Square kilometres (km²)", "Acres"],
    label_visibility="collapsed")

st.sidebar.markdown("---")
st.sidebar.subheader("⚖️ Yield Display Unit")
yield_unit = st.sidebar.radio("Yield Unit",
    ["kg/ha", "Tonnes"],
    label_visibility="collapsed")

st.sidebar.markdown("---")
st.sidebar.subheader("🔆 Image Enhancement")
enhance_method = st.sidebar.radio("Enh", [
    "🔲 No enhancement", "📊 Stretch 2–98%",
    "📊 Stretch 1–99%", "⚡ Stretch 0.5–99.5%"], index=1, label_visibility="collapsed")

# ── Load classifiers ──────────────────────────────────────────────────────────
clf_nn  = load_nn_classifier()
clf_xgb = load_xgb_classifier() if clf_engine == "xgb" else None
clf     = clf_xgb if clf_engine == "xgb" and clf_xgb else clf_nn

if clf:
    lbl = (f"✅ XGBoost · {clf.n_refs} classes" if clf_engine == "xgb"
           else f"✅ NN · {clf.n_refs} refs")
    st.sidebar.caption(lbl)

my_data_df = load_my_data()

# ═══════════════════════════════════════════════════════════════════════════════
# FILE UPLOAD
# ═══════════════════════════════════════════════════════════════════════════════

uploaded = st.file_uploader(
    "📂 Upload Sentinel-2 satellite image (TIFF preferred for full pipeline)",
    type=["jpg","jpeg","png","tif","tiff"],
)

if uploaded is None:
    st.markdown("""<div class="info-banner">
    🛰️ Upload a satellite image to begin.<br>
    &nbsp;&nbsp;• <b>10-band Sentinel-2 TIFF</b> → Full pipeline: crop type + growth stage + weather risk + yield forecast<br>
    &nbsp;&nbsp;• Any TIFF (other band count) → Agriculture vs Non-Agriculture map only<br>
    &nbsp;&nbsp;• JPG / PNG → Agriculture vs Non-Agriculture map only<br><br>
    ⚙️ Set <b>Pixel size = 10 m/px</b> in sidebar for accurate area calculations.
    </div>""", unsafe_allow_html=True)
    st.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# LOAD IMAGE & EXTRACT METADATA
# ═══════════════════════════════════════════════════════════════════════════════

fname      = uploaded.name.lower()
is_tiff    = fname.endswith((".tif", ".tiff"))
raw_bytes  = uploaded.read()
img_pil    = None; tiff_data = None; n_bands = 0
tiff_bounds = None; tiff_img_date = None

if is_tiff:
    if rasterio is None:
        st.error("❌ rasterio not installed. Run: pip install rasterio")
        st.stop()
    _tmp_path = None
    try:
        import tempfile, traceback, warnings as _w
        from rasterio.env import Env as _REnv
        from rasterio.warp import transform_bounds as _tb

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as _tmp:
            _tmp.write(raw_bytes)
            _tmp_path = _tmp.name

        with _w.catch_warnings():
            _w.simplefilter("ignore")

            def _open_and_read(path, **env_kwargs):
                """Open a GeoTIFF with rasterio inside a proper GDAL Env and return all data."""
                with _REnv(**env_kwargs):
                    with rasterio.open(path) as ds:
                        _nb   = ds.count
                        _nd   = ds.nodata
                        _bnds = None
                        try:
                            b = ds.bounds
                            if ds.crs:
                                l, bt, r, t = _tb(ds.crs, "EPSG:4326",
                                                  b.left, b.bottom, b.right, b.top)
                                _bnds = {"lon": (l+r)/2, "lat": (bt+t)/2,
                                         "lon1": l, "lat1": bt, "lon2": r, "lat2": t}
                        except: pass
                        _date = extract_tiff_date(ds, uploaded.name)
                        _arr = ds.read().astype(np.float32)   # read all bands at once
                        if _nd is not None:
                            _arr[_arr == _nd] = np.nan
                        return _nb, _nd, _bnds, _date, _arr

            _read_err = None

            # ── Pass 1: plain rasterio (how every other system reads it) ─────
            try:
                n_bands, nodata, tiff_bounds, tiff_img_date, ab = _open_and_read(_tmp_path)
                tiff_data = ab
            except Exception:
                _read_err = traceback.format_exc()

                # ── Pass 2: same but with GTIFF_IGNORE_READ_ERRORS ───────────
                # Windows pip rasterio bundles a libtiff that rejects early-change
                # LZW codes — this flag makes GDAL tolerate them instead of failing.
                try:
                    n_bands, nodata, tiff_bounds, tiff_img_date, ab = _open_and_read(
                        _tmp_path, GTIFF_IGNORE_READ_ERRORS="YES")
                    tiff_data = ab
                    st.info("ℹ️ Loaded with LZW tolerance mode. "
                            "Re-exporting with DEFLATE compression will give better results.")
                except Exception:
                    _read_err = traceback.format_exc()
                    st.error("❌ Could not read the TIFF file.")
                    with st.expander("🔍 Error details"):
                        st.code(_read_err)
                        st.markdown(
                            "**Fix:** Re-export or re-download your TIFF with **DEFLATE** compression.\n\n"
                            "If using Google Earth Engine:\n"
                            "```javascript\n"
                            "Export.image.toDrive({..., fileFormat: 'GeoTIFF',\n"
                            "  formatOptions: { cloudOptimized: true }})\n"
                            "```"
                        )
                    st.stop()

            # ── Build RGB preview from loaded data ────────────────────────────
            def _rn(bnd):
                v = bnd[np.isfinite(bnd)]
                if len(v) == 0: return np.zeros_like(bnd, dtype=np.uint8)
                lo, hi = np.percentile(v, 2), np.percentile(v, 98)
                return np.clip((bnd - lo) / (hi - lo + 1e-9) * 255, 0, 255).astype(np.uint8)
            _r = _rn(ab[0]); _g = _rn(ab[1])
            _b3 = _rn(ab[2]) if n_bands >= 3 else _g
            img_pil = Image.fromarray(np.stack([_r, _g, _b3], axis=2), "RGB")

    finally:
        if _tmp_path and os.path.exists(_tmp_path):
            try:
                os.unlink(_tmp_path)
            except Exception:
                pass
else:
    try:
        img_pil = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except Exception as e:
        st.error(f"❌ Image load error: {e}"); st.stop()

if img_pil is None:
    st.error("❌ Could not load image."); st.stop()

is_10band = (n_bands == 10 and tiff_data is not None)
H, W = img_pil.size[1], img_pil.size[0]
img_pil_display = enhance_image(img_pil, enhance_method)

# Vegetation index for RGB images
a_rgb = np.array(img_pil, dtype=np.float32)
R_,G_,B_ = a_rgb[:,:,0],a_rgb[:,:,1],a_rgb[:,:,2]
denom_ = np.where(R_+G_+B_==0,1,R_+G_+B_)
vi = ((G_-R_)/denom_).astype(np.float32)
vi_safe = np.nan_to_num(vi, nan=-999.0)

cs = max(5,min(20,H//50,W//50))
corners = np.concatenate([a_rgb[:cs,:cs].reshape(-1,3),a_rgb[:cs,-cs:].reshape(-1,3),
    a_rgb[-cs:,:cs].reshape(-1,3),a_rgb[-cs:,-cs:].reshape(-1,3)])
bg_col = np.median(corners,axis=0)
bg_mask = ((np.abs(a_rgb[:,:,0]-bg_col[0])<30) &
           (np.abs(a_rgb[:,:,1]-bg_col[1])<30) &
           (np.abs(a_rgb[:,:,2]-bg_col[2])<30)) | ~np.isfinite(vi)
valid_mask = ~bg_mask
total_px   = int(valid_mask.sum())

if is_tiff:
    if is_10band:
        st.success("✅ **10-band Sentinel-2 TIFF detected** — Full intelligence pipeline enabled")
    else:
        st.warning(f"⚠️ {n_bands}-band TIFF — Full pipeline requires exactly 10 bands.")

# ─── Run Analysis button ───────────────────────────────────────────────────────
if not st.session_state.get('analysis_done', False):
    st.image(img_pil_display, caption="Preview — click Run Analysis to begin")
    if st.button("🚀 Run Analysis", type="primary"):
        st.session_state['analysis_done'] = True
        st.rerun()
    st.info("👆 Click **Run Analysis** to begin.")
    st.stop()

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION (10-band only)
# ═══════════════════════════════════════════════════════════════════════════════

crop_map_2d  = np.full((H, W), 'others', dtype=object)
conf_map_2d  = np.zeros((H, W), dtype=np.float32)
stage_map_2d = np.full((H, W), '', dtype=object)
_cvm         = np.zeros((H, W), dtype=bool)
actual_doy   = 280
season       = 'kharif'

if is_10band:
    if tiff_img_date:
        actual_doy = tiff_img_date.timetuple().tm_yday
    else:
        tiff_img_date = date(2024, 10, 7)  # fallback: image date 07/10/2024
        actual_doy    = tiff_img_date.timetuple().tm_yday

    season = 'rabi' if not (152 <= actual_doy <= 345) else 'kharif'

    with st.spinner("🔬 Computing 18 spectral features..."):
        feat_matrix, NDVI_flat, NDWI_flat = compute_features(tiff_data)
        ndvi_2d = NDVI_flat.reshape(H, W); ndwi_2d = NDWI_flat.reshape(H, W)
        ndvi_thr = NDVI_MIN_RABI if season == 'rabi' else NDVI_MIN_KHARIF
        _cvm = (ndvi_2d >= ndvi_thr) & (ndwi_2d < 0.05) & (~bg_mask)

    if clf:
        with st.spinner(f"🌾 Classifying {int(_cvm.sum()):,} crop pixels..."):
            valid_idx = np.where(_cvm.ravel())[0]
            if len(valid_idx) > 0:
                X_valid = feat_matrix[valid_idx]
                labels, confs, stages = clf.classify(X_valid, doy=actual_doy, conf_threshold=CONF_THRESHOLD)
                crop_map_2d.ravel()[valid_idx] = labels
                conf_map_2d.ravel()[valid_idx] = confs
                if clf_engine == "xgb" and np.all(stages == "Unknown"):
                    try:
                        _nn = NNClassifier()
                        csv_path = DATABANK_CSV_NEW if os.path.exists(DATABANK_CSV_NEW) else "data_bank_reference.csv"
                        _nn.load(data_bank_csv=csv_path, gee_csv=None, mode="data_bank")
                        _, _, nn_stages = _nn.classify(X_valid, doy=actual_doy, conf_threshold=CONF_THRESHOLD)
                        stage_map_2d.ravel()[valid_idx] = nn_stages
                    except:
                        stage_map_2d.ravel()[valid_idx] = stages
                else:
                    stage_map_2d.ravel()[valid_idx] = stages
    else:
        st.error("❌ Classifier not loaded.")

# ─── Location header ─────────────────────────────────────────────────────────
if tiff_bounds:
    with st.spinner("📍 Detecting location..."):
        loc_name = get_location_name(tiff_bounds['lon'], tiff_bounds['lat'])
    if loc_name:
        date_str = tiff_img_date.strftime('%d %b %Y') if tiff_img_date else "Unknown"
        src_str  = "from TIFF metadata" if tiff_img_date else "estimated"
        st.markdown(f"""<div style="background:linear-gradient(135deg,#0a1f35,#0d2b4a);
            border:1px solid #1e4a6a;border-radius:12px;padding:14px 20px;
            margin-bottom:1rem;text-align:center;">
            <div style="font-size:1.4rem;font-weight:700;color:#60a5fa;">📍 {loc_name}</div>
            <div style="font-size:0.82rem;color:#8899aa;margin-top:4px;">
            {tiff_bounds['lat']:.4f}°N, {tiff_bounds['lon']:.4f}°E &nbsp;·&nbsp;
            {W}×{H}px &nbsp;·&nbsp; DOY {actual_doy} &nbsp;·&nbsp;
            📅 {date_str} ({src_str})
            {' &nbsp;·&nbsp; ' + season + ' season' if is_10band else ''}
            </div></div>""", unsafe_allow_html=True)

# ─── Summary cards ─────────────────────────────────────────────────────────
st.markdown("### 📊 Analysis Summary")
agri_px = int(((vi_safe > 0.08) & valid_mask).sum())
ap      = agri_px / max(total_px,1) * 100
_ha = lambda px: px * pixel_size_m**2 / 10000 if pixel_size_m else None

all_detected = get_all_detected_crops(crop_map_2d, stage_map_2d, conf_map_2d) if is_10band else []
dom_crop  = all_detected[0][0]  if all_detected else None
dom_stage = all_detected[0][1]  if all_detected else None

dc1,dc2,dc3,dc4 = st.columns(4)
with dc1:
    st.markdown(f"""<div class="dash-card"><div class="stat-label">🗺️ Total Image Area</div>
    <div class="stat-value white">{fa(_ha(total_px),area_unit) if pixel_size_m else f"{total_px/1e6:.1f}M px"}</div>
    <div class="stat-sub">{W}×{H} px</div></div>""", unsafe_allow_html=True)
with dc2:
    st.markdown(f"""<div class="dash-card"><div class="stat-label">🌾 Agriculture Area</div>
    <div class="stat-value green">{fa(_ha(agri_px),area_unit) if pixel_size_m else f"{ap:.1f}%"}</div>
    <div class="stat-sub">{ap:.1f}% of image</div></div>""", unsafe_allow_html=True)
with dc3:
    if is_10band and dom_crop:
        st.markdown(f"""<div class="dash-card"><div class="stat-label">🌱 Dominant Crop</div>
        <div class="stat-value" style="color:{CROP_HEX.get(dom_crop,'#fff')};font-size:1.3rem;padding-top:4px">
        {CROP_EMOJI.get(dom_crop,'')} {dom_crop.title()}</div>
        <div class="stat-sub">DOY {actual_doy} · {season}</div></div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""<div class="dash-card"><div class="stat-label">🧠 Mode</div>
        <div class="stat-value white" style="font-size:1rem;padding-top:8px">Agri Map Only</div>
        <div class="stat-sub">Upload 10-band TIFF for crop detection</div></div>""", unsafe_allow_html=True)
with dc4:
    date_display = tiff_img_date.strftime('%d %b %Y') if tiff_img_date else 'Unknown'
    n_crops_det  = len(all_detected)
    if is_10band:
        st.markdown(f"""<div class="dash-card"><div class="stat-label">🔍 Crops Detected</div>
        <div class="stat-value blue" style="font-size:1.4rem;padding-top:4px">{n_crops_det}</div>
        <div class="stat-sub">📅 {date_display}</div></div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""<div class="dash-card"><div class="stat-label">📅 Image Date</div>
        <div class="stat-value white" style="font-size:1rem;padding-top:8px">{date_display}</div>
        <div class="stat-sub">DOY {actual_doy}</div></div>""", unsafe_allow_html=True)

st.markdown("---")


# ═══════════════════════════════════════════════════════════════════════════════
# TABS  (4 only — Historic Analysis removed)
# ═══════════════════════════════════════════════════════════════════════════════

if is_10band:
    tab1, tab2, tab3, tab4 = st.tabs([
        "🌍 Agriculture Map",
        "🌾 Crop Classification",
        "🌱 Growth Stage",
        "🌤️ Weather & Yield Forecast",
    ])
else:
    tab1, = st.tabs(["🌍 Agriculture Map"])
    tab2 = tab3 = tab4 = None


# ──────────────────────────────────────────────────────────────────────────────
# TAB 1 — AGRICULTURE MAP
# ──────────────────────────────────────────────────────────────────────────────

@st.fragment
def render_agri_map():
    st.markdown("## 🌍 Agriculture vs Non-Agriculture")
    c1,c2,c3 = st.columns(3)
    with c1: thr = st.slider("NDVI threshold",0.01,0.40,0.08,0.01,key="agri_thr")
    with c2:
        sa  = st.checkbox("🟢 Show Agriculture",True,key="cb_agri")
        sna = st.checkbox("🟠 Show Non-Agriculture",True,key="cb_nonagri")
    with c3: st.caption(f"Image: {W}×{H} px")

    am = (vi_safe>thr)&valid_mask; nm = (vi_safe<=thr)&valid_mask
    apx=int(am.sum()); npx=int(nm.sum()); ap2=apx/max(total_px,1)*100
    ov = np.zeros((H,W,4),dtype=np.uint8)
    if sa:  ov[am]=[46,204,113,220]
    if sna: ov[nm]=[230,126,34,180]
    bl = Image.alpha_composite(img_pil_display.convert("RGBA"),Image.fromarray(ov,"RGBA")).convert("RGB")

    ca,cb = st.columns(2)
    with ca: st.markdown("**Original**");       st.image(img_pil_display)
    with cb: st.markdown("**Agriculture Map**"); st.image(bl)

    st.markdown("---")
    ak=False; tha=aha=nha=None
    if pixel_size_m:
        tha=total_px*pixel_size_m**2/10000; aha=apx*pixel_size_m**2/10000
        nha=npx*pixel_size_m**2/10000; ak=True
    elif known_total_ha:
        tha=known_total_ha; aha=known_total_ha*ap2/100
        nha=known_total_ha*(100-ap2)/100; ak=True

    if not ak:
        st.markdown('<div class="warn-banner">⚠️ Set pixel size in sidebar for area values.</div>',unsafe_allow_html=True)

    c1,c2,c3 = st.columns(3)
    with c1: st.markdown(f'<div class="stat-box"><div class="stat-label">Total</div>'
        f'<div class="stat-value white">{fa(tha,area_unit) if ak else f"{total_px:,} px"}</div></div>',unsafe_allow_html=True)
    with c2: st.markdown(f'<div class="stat-box"><div class="stat-label">🌾 Agriculture</div>'
        f'<div class="stat-value green">{fa(aha,area_unit) if ak else "—"}</div>'
        f'<div class="stat-sub">{ap2:.1f}%</div></div>',unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="stat-box"><div class="stat-label">🏗️ Non-Agriculture</div>'
        f'<div class="stat-value orange">{fa(nha,area_unit) if ak else "—"}</div>'
        f'<div class="stat-sub">{100-ap2:.1f}%</div></div>',unsafe_allow_html=True)

    ch1, ch2 = st.columns(2)
    chart_data = pd.DataFrame({"Category":["🌾 Agriculture","🏗️ Non-Agriculture"],
        "Coverage":[round(aha if ak else ap2,1),round(nha if ak else 100-ap2,1)]}).set_index("Category")
    with ch1:
        st.markdown("**Bar Chart**")
        st.bar_chart(chart_data)
    with ch2:
        st.markdown("**Pie Chart**")
        fig_pie1, ax_pie1 = plt.subplots(figsize=(4,4))
        vals1 = [round(aha if ak else ap2,1), round(nha if ak else 100-ap2,1)]
        lbls1 = ["🌾 Agriculture","🏗️ Non-Agriculture"]
        clrs1 = ["#2ecc71","#e67e22"]
        ax_pie1.pie(vals1, labels=lbls1, colors=clrs1, autopct="%1.1f%%",
                    textprops={"color":"white","fontsize":9}, startangle=90)
        dark_chart(fig_pie1, ax_pie1)
        ax_pie1.set_facecolor("#131f2e")
        st.pyplot(fig_pie1, use_container_width=True)
        plt.close(fig_pie1)

    b1=io.BytesIO(); bl.save(b1,format="PNG"); b1.seek(0)
    st.download_button("⬇️ Download Agriculture Map",data=b1,file_name="agriculture_map.png",mime="image/png")


# ──────────────────────────────────────────────────────────────────────────────
# TAB 2 — CROP CLASSIFICATION
# ──────────────────────────────────────────────────────────────────────────────

@st.fragment
def render_crop_classification():
    st.markdown("## 🌾 Crop Type Classification — 8 Crops")
    if clf is None:
        st.error("❌ Classifier not loaded."); return

    all_opts = ['🌾 Rice','🌿 Wheat','🥜 Groundnut','🌸 Cotton','🎋 Sugarcane',
                '🌽 Maize','🫘 Soybean','🌼 Mustard','⬜ Others']
    selected = st.multiselect("Show crops",all_opts,default=all_opts,key="t2_crops")
    nm_map = {'🌾 Rice':'rice','🌿 Wheat':'wheat','🥜 Groundnut':'groundnut',
              '🌸 Cotton':'cotton','🎋 Sugarcane':'sugarcane','🌽 Maize':'maize',
              '🫘 Soybean':'soybean','🌼 Mustard':'mustard','⬜ Others':'others'}
    sel_crops = [nm_map[s] for s in selected if s in nm_map]

    ov2 = np.zeros((H,W,4),dtype=np.uint8)
    for cname,color in CROP_COLORS_9.items():
        if cname not in sel_crops: continue
        mask = (crop_map_2d==cname)
        if not mask.any(): continue
        ov2[mask,0]=color[0]; ov2[mask,1]=color[1]; ov2[mask,2]=color[2]
        c_here = conf_map_2d[mask]
        ov2[mask,3]=np.clip(color[3]*np.where(c_here>0,c_here,0.7),80,255).astype(np.uint8)
    bl2 = Image.alpha_composite(img_pil_display.convert("RGBA"),Image.fromarray(ov2,"RGBA")).convert("RGB")

    ca,cb = st.columns(2)
    with ca: st.markdown("**Original**");            st.image(img_pil_display)
    with cb: st.markdown("**Crop Classification**"); st.image(bl2)

    detected = [c for c in CROP_COLORS_9 if c in sel_crops and (crop_map_2d==c).any()]
    if detected:
        lc = st.columns(min(len(detected),5))
        for i,c in enumerate(detected):
            lc[i%5].markdown(f"<div style='background:{CROP_HEX[c]};padding:6px;border-radius:6px;"
                f"text-align:center;color:black;font-size:11px;font-weight:bold'>"
                f"{CROP_EMOJI.get(c,'')} {c.title()}</div>",unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 📊 Crop Statistics")
    total_cp = sum((crop_map_2d==c).sum() for c in CROP_COLORS_9 if c!='others')
    records = []
    for c in [x for x in CROP_COLORS_9 if x!='others']:
        n = int((crop_map_2d==c).sum())
        if n==0: continue
        avg_c = float(conf_map_2d[crop_map_2d==c].mean())
        records.append({
            'Crop': CROP_EMOJI.get(c,'')+" "+c.title(),
            'Pixels': n,
            'Area': fa(n*pixel_size_m**2/10000,area_unit) if pixel_size_m else f"{n/max(H*W,1)*100:.1f}%",
            '% of Crops': round(n/max(total_cp,1)*100,1),
            'Avg Confidence %': round(avg_c*100,1),
        })
    if records:
        df_crops = pd.DataFrame(records).sort_values('Pixels',ascending=False)
        st.dataframe(df_crops, use_container_width=True)

        # Charts: bar + pie
        chart_names  = [r['Crop'] for r in records]
        chart_vals   = [r['% of Crops'] for r in records]
        chart_colors = [CROP_HEX.get(r['Crop'].split()[-1].lower(), '#9CA3AF') for r in records]

        ch_c1, ch_c2 = st.columns(2)
        with ch_c1:
            st.markdown("**Bar Chart — % of Crops**")
            df_bar2 = pd.DataFrame({"Crop": chart_names, "% of Crops": chart_vals}).set_index("Crop")
            st.bar_chart(df_bar2)
        with ch_c2:
            st.markdown("**Pie Chart — Crop Distribution**")
            fig_pie2, ax_pie2 = plt.subplots(figsize=(4,4))
            ax_pie2.pie(chart_vals, labels=chart_names, colors=chart_colors,
                        autopct="%1.1f%%", textprops={"color":"white","fontsize":8}, startangle=90)
            dark_chart(fig_pie2, ax_pie2)
            ax_pie2.set_facecolor("#131f2e")
            st.pyplot(fig_pie2, use_container_width=True)
            plt.close(fig_pie2)
    else:
        st.warning("No crops detected. Try lowering the confidence threshold.")

    b2=io.BytesIO(); bl2.save(b2,format="PNG"); b2.seek(0)
    st.download_button("⬇️ Download Crop Map",data=b2,file_name="crop_map.png",mime="image/png")


# ──────────────────────────────────────────────────────────────────────────────
# TAB 3 — GROWTH STAGE
# ──────────────────────────────────────────────────────────────────────────────

@st.fragment
def render_growth_stage():
    st.markdown("## 🌱 Crop Growth Stage")
    if not all_detected:
        st.warning("No crops detected above confidence threshold."); return

    for cname, top_stage, n_px, avg_conf in all_detected:
        area_str = fa(n_px*pixel_size_m**2/10000,area_unit) if pixel_size_m else f"{n_px:,} px"
        hex_c    = CROP_HEX.get(cname,'#888')

        # Stage distribution
        sv = stage_map_2d[crop_map_2d==cname]
        unique_s, cnt_s = np.unique(sv[sv!=''],return_counts=True)
        stage_breakdown = [(s,int(c)) for s,c in zip(unique_s,cnt_s)] if len(unique_s)>0 else []
        stage_breakdown.sort(key=lambda x:-x[1])

        # Sowing / harvest estimate from databank
        sowing_est, harvest_est, total_d = estimate_sowing_harvest(
            cname, top_stage, tiff_img_date or date.today(), xlsx_path=DATABANK_XLSX)

        st.markdown(f"""<div style="border:1px solid {hex_c};border-radius:12px;padding:16px;
            margin-bottom:14px;background:linear-gradient(135deg,#0a1628,#0f2040);">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
                <div>
                    <span style="font-size:1.6rem">{CROP_EMOJI.get(cname,'🌱')}</span>
                    <span style="color:{hex_c};font-weight:700;font-size:1.15rem;margin-left:8px">{cname.title()}</span>
                    <span style="background:{hex_c};color:black;border-radius:6px;padding:2px 12px;
                        margin-left:12px;font-size:0.85rem;font-weight:700">{top_stage}</span>
                </div>
                <div style="text-align:right;color:#8899aa;font-size:0.83rem">
                    {area_str} &nbsp;·&nbsp; conf {avg_conf*100:.0f}%<br>
                    🌱 Sowing: <b style="color:#2ecc71">{sowing_est.strftime('%d %b %Y') if sowing_est else '?'}</b>
                    &nbsp; 🌾 Harvest: <b style="color:#f59e0b">{harvest_est.strftime('%d %b %Y') if harvest_est else '?'}</b>
                    &nbsp; ({total_d}d total)
                </div>
            </div>
            {('<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">' +
              ''.join(f'<span style="background:#1a2a3a;color:#aaa;border-radius:4px;padding:2px 8px;font-size:0.78rem">{s}: {c}px</span>'
                      for s,c in stage_breakdown[:6]) +
              '</div>') if stage_breakdown else ''}
        </div>""", unsafe_allow_html=True)

    # Stage overlay map
    ov3 = np.zeros((H,W,4),dtype=np.uint8)
    for st_name,color in STAGE_COLORS.items():
        mask=(stage_map_2d==st_name)
        if not mask.any(): continue
        ov3[mask,0]=color[0]; ov3[mask,1]=color[1]; ov3[mask,2]=color[2]; ov3[mask,3]=color[3]
    if ov3.any():
        st.markdown("### 🗺️ Stage Map")
        bl3=Image.alpha_composite(img_pil_display.convert("RGBA"),Image.fromarray(ov3,"RGBA")).convert("RGB")
        st.image(bl3)
        det_stages = sorted(set(stage_map_2d.ravel())-{'','Unknown'})
        if det_stages:
            lc = st.columns(min(len(det_stages),5))
            for i,sn in enumerate(det_stages):
                sc=STAGE_COLORS.get(sn,[150,150,150,200])
                lc[i%5].markdown(
                    f"<div style='background:#{sc[0]:02x}{sc[1]:02x}{sc[2]:02x};"
                    f"padding:5px;border-radius:5px;text-align:center;"
                    f"color:black;font-size:10px;font-weight:bold'>{sn}</div>",unsafe_allow_html=True)

        # Stage charts: bar + pie
        stage_px_counts = {}
        for sn in det_stages:
            stage_px_counts[sn] = int((stage_map_2d == sn).sum())
        if stage_px_counts:
            st.markdown("### 📊 Stage Distribution")
            s_names = list(stage_px_counts.keys())
            s_vals  = list(stage_px_counts.values())
            s_colors = [f"#{STAGE_COLORS.get(sn,[150,150,150,200])[0]:02x}"
                        f"{STAGE_COLORS.get(sn,[150,150,150,200])[1]:02x}"
                        f"{STAGE_COLORS.get(sn,[150,150,150,200])[2]:02x}" for sn in s_names]

            sc1, sc2 = st.columns(2)
            with sc1:
                st.markdown("**Bar Chart**")
                df_bar3 = pd.DataFrame({"Stage": s_names, "Pixels": s_vals}).set_index("Stage")
                st.bar_chart(df_bar3)
            with sc2:
                st.markdown("**Pie Chart**")
                fig_pie3, ax_pie3 = plt.subplots(figsize=(4,4))
                ax_pie3.pie(s_vals, labels=s_names, colors=s_colors,
                            autopct="%1.1f%%", textprops={"color":"white","fontsize":8}, startangle=90)
                dark_chart(fig_pie3, ax_pie3)
                ax_pie3.set_facecolor("#131f2e")
                st.pyplot(fig_pie3, use_container_width=True)
                plt.close(fig_pie3)


# ──────────────────────────────────────────────────────────────────────────────
# TAB 4 — WEATHER & YIELD FORECAST (all crops, stage-wise risk)
# ──────────────────────────────────────────────────────────────────────────────

def _render_stage_table(stage_results: list):
    """Build a readable stage-wise weather comparison table."""
    rows = []
    for sr in stage_results:
        ideal  = sr["ideal"]
        actual = sr["actual"]
        pscores = sr["param_scores"]

        for p, label in PARAM_LABELS.items():
            act_v = actual.get(p)
            idl_v = ideal.get(p)
            ps    = pscores.get(p)
            if act_v is None and idl_v is None:
                continue
            if idl_v is None:
                status = "⚠️ No ideal"
                dev_s  = "N/A"
            elif act_v is None:
                status = "⚠️ No data"
                dev_s  = "N/A"
            else:
                dev_pct = (act_v - idl_v) / max(abs(idl_v), 0.5) * 100
                dev_s   = f"{dev_pct:+.1f}%"
                if ps is None:   status = "—"
                elif ps < 20:    status = "✅ Normal"
                elif ps < 45:    status = "⚡ Moderate"
                elif ps < 65:    status = "⚠️ High"
                else:            status = "🔴 Extreme"
            rows.append({
                "Stage":        sr["stage"],
                "Parameter":    label,
                "Actual":       f"{act_v:.2f}" if act_v is not None else "N/A",
                "Ideal":        f"{idl_v:.2f}" if idl_v is not None else "N/A",
                "Deviation":    dev_s,
                "Stress %":     f"{ps:.0f}" if ps is not None else "N/A",
                "Status":       status,
            })
    return pd.DataFrame(rows)


@st.fragment
def render_weather_yield():
    st.markdown("## 🌤️ Weather Risk & Yield Forecast")

    if not PIPELINE_OK:
        st.error("❌ Pipeline modules not loaded. Check pipeline/ directory."); return
    if not is_10band:
        st.info("ℹ️ This section requires a 10-band Sentinel-2 TIFF."); return
    if not all_detected:
        st.warning("⚠️ No crops detected."); return
    if not tiff_bounds:
        st.warning("⚠️ No geographic coordinates in TIFF. Cannot fetch NASA POWER data."); return

    lat_val = tiff_bounds['lat']; lon_val = tiff_bounds['lon']
    img_date_used = tiff_img_date or date.today()
    agri_ha_total = _ha(agri_px) if pixel_size_m else None

    # ── Summary metadata cards ────────────────────────────────────────────────
    m1,m2,m3 = st.columns(3)
    with m1:
        st.markdown(f"""<div class="pipeline-card">
            <div class="stat-label">📍 Location</div>
            <div style="color:#60a5fa;font-weight:600;font-size:1rem;margin-top:4px">
            {lat_val:.4f}°N, {lon_val:.4f}°E</div>
            <div class="stat-sub">DOY {actual_doy} · {season} season</div>
        </div>""", unsafe_allow_html=True)
    with m2:
        st.markdown(f"""<div class="pipeline-card">
            <div class="stat-label">📅 Image Date</div>
            <div style="color:#2ecc71;font-weight:600;font-size:1.1rem;margin-top:4px">
            {img_date_used.strftime('%d %b %Y')}</div>
            <div class="stat-sub">{'From TIFF metadata' if tiff_img_date else 'Estimated'}</div>
        </div>""", unsafe_allow_html=True)
    with m3:
        st.markdown(f"""<div class="pipeline-card">
            <div class="stat-label">🌾 Agriculture Area</div>
            <div style="color:#f59e0b;font-weight:600;font-size:1.1rem;margin-top:4px">
            {fa(agri_ha_total,area_unit) if agri_ha_total else f"{ap:.1f}%"}</div>
            <div class="stat-sub">{len(all_detected)} crop type(s) detected</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("---")

    # ── Per-crop processing ───────────────────────────────────────────────────
    for crop_idx, (cname, top_stage, n_px, avg_conf) in enumerate(all_detected):
        hex_c    = CROP_HEX.get(cname,'#888')
        emoji    = CROP_EMOJI.get(cname,'🌱')
        crop_px  = n_px
        crop_ha  = crop_px * pixel_size_m**2 / 10000 if pixel_size_m else (
                   agri_ha_total * (crop_px / max(agri_px, 1)) if agri_ha_total else None)

        st.markdown(f"""<div style="border:2px solid {hex_c};border-radius:14px;padding:18px 22px;
            margin-bottom:24px;background:linear-gradient(135deg,#080f1c,#0d1a2e);">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
                <span style="font-size:2rem">{emoji}</span>
                <div>
                    <div style="color:{hex_c};font-weight:800;font-size:1.3rem">{cname.title()}</div>
                    <div style="color:#8899aa;font-size:0.82rem">
                        Growth stage: <b style="color:#ddd">{top_stage}</b> &nbsp;·&nbsp;
                        Area: <b style="color:#ddd">{fa(crop_ha,area_unit) if crop_ha else f"{crop_px:,} px"}</b> &nbsp;·&nbsp;
                        Conf: <b style="color:#ddd">{avg_conf*100:.0f}%</b>
                    </div>
                </div>
            </div>""", unsafe_allow_html=True)

        # ── Sowing / harvest estimate ─────────────────────────────────────────
        sowing_date, harvest_date, total_crop_days = estimate_sowing_harvest(
            cname, top_stage, img_date_used, xlsx_path=DATABANK_XLSX)

        sc1,sc2,sc3 = st.columns(3)
        with sc1:
            st.markdown(f"""<div class="stat-box">
                <div class="stat-label">🌱 Sowing Date</div>
                <div class="stat-value green" style="font-size:1.1rem">
                {sowing_date.strftime('%d %b %Y') if sowing_date else 'N/A'}</div>
            </div>""", unsafe_allow_html=True)
        with sc2:
            st.markdown(f"""<div class="stat-box">
                <div class="stat-label">🌾 Harvest Date</div>
                <div class="stat-value yellow" style="font-size:1.1rem">
                {harvest_date.strftime('%d %b %Y') if harvest_date else 'N/A'}</div>
            </div>""", unsafe_allow_html=True)
        with sc3:
            days_left = (harvest_date - img_date_used).days if harvest_date else None
            st.markdown(f"""<div class="stat-box">
                <div class="stat-label">⏱️ Duration / Days Left</div>
                <div class="stat-value blue" style="font-size:1.1rem">
                {total_crop_days}d / {days_left if days_left is not None else '?'}d</div>
            </div>""", unsafe_allow_html=True)

        # ── NASA POWER fetch ──────────────────────────────────────────────────
        if sowing_date is None or harvest_date is None:
            st.warning("⚠️ Could not estimate sowing/harvest dates.")
            st.markdown("</div>", unsafe_allow_html=True)
            continue

        with st.spinner(f"🛰️ Fetching NASA POWER weather for {cname.title()} ({sowing_date} → {harvest_date})..."):
            daily_weather = fetch_nasa_power(lat=lat_val, lon=lon_val,
                                             start=sowing_date, end=harvest_date)

        if daily_weather is None or daily_weather.empty:
            st.error("❌ NASA POWER fetch failed. Check internet connectivity.")
            st.markdown("</div>", unsafe_allow_html=True)
            continue

        st.success(f"✅ {len(daily_weather)} daily weather records fetched ({sowing_date.strftime('%d %b')} → {harvest_date.strftime('%d %b %Y')})")

        # ── Stage-wise weather comparison ─────────────────────────────────────
        st.markdown("#### 📊 Stage-wise Weather Analysis")
        crop_stages_list = get_crop_stages(cname, xlsx_path=DATABANK_XLSX)

        if crop_stages_list:
            stage_results = compute_stagewise_risk(daily_weather, crop_stages_list, sowing_date)
            final_score, final_cat = aggregate_crop_risk(stage_results)

            # Stage risk cards
            stage_cols = st.columns(min(len(stage_results), 4))
            for si, sr in enumerate(stage_results):
                col_i = stage_cols[si % len(stage_cols)]
                cat_color = get_risk_color(sr["category"])
                with col_i:
                    st.markdown(f"""<div style="background:#0d1f33;border-left:3px solid {cat_color};
                        border-radius:8px;padding:10px 12px;margin-bottom:8px;text-align:center">
                        <div style="color:#aaa;font-size:0.72rem;text-transform:uppercase;letter-spacing:1px">
                        {sr['stage']}</div>
                        <div style="color:{cat_color};font-weight:700;font-size:1rem;margin:4px 0">
                        {sr['category']}</div>
                        <div style="color:#8899aa;font-size:0.72rem">{sr['score']:.0f}/100</div>
                        <div style="color:#6688aa;font-size:0.68rem">
                        {sr['start_date'].strftime('%d %b')} → {sr['end_date'].strftime('%d %b')}</div>
                    </div>""", unsafe_allow_html=True)

            # Stage-wise detailed table
            with st.expander("📋 Detailed Stage-wise Comparison Table", expanded=False):
                df_table = _render_stage_table(stage_results)
                if not df_table.empty:
                    st.dataframe(df_table, use_container_width=True, hide_index=True)

            # Overall risk badge
            risk_color = get_risk_color(final_cat)
            advisory   = get_risk_advisory(final_cat)
            st.markdown(f"""
            <div style="text-align:center;margin:14px 0 8px;">
                <span class="risk-badge risk-{final_cat}">
                    🌡️ Overall Weather Risk: {final_cat} &nbsp;({final_score:.0f}/100)
                </span>
            </div>
            <div style="background:{
                '#e8f5e9' if final_cat=='Low' else
                '#fff8e1' if final_cat=='Moderate' else
                '#fce4ec' if final_cat=='High' else '#1a0000'};
                border-left:5px solid {risk_color};border-radius:8px;padding:12px 16px;
                color:{
                '#1b5e20' if final_cat=='Low' else
                '#bf360c' if final_cat=='Moderate' else
                '#b71c1c' if final_cat=='High' else '#ff8a80'};
                font-size:0.88rem;margin:8px 0;">
                {advisory}
            </div>""", unsafe_allow_html=True)

        else:
            # Fallback: season-level risk
            st.info("ℹ️ Stage-wise databank not available for this crop — using season-level comparison.")
            actual_season = aggregate_weather(daily_weather)
            ideal_raw     = get_ideal_weather_from_xlsx(cname, xlsx_path=DATABANK_XLSX)
            if not ideal_raw or all(v is None for v in ideal_raw.values()):
                ideal_raw = get_ideal_weather_for_crop(cname, my_data_df)
            ideal_mapped  = ideal_to_nasa_param_map(ideal_raw)
            final_score, final_cat, _ = compute_weather_risk_score(actual_season, ideal_mapped)
            risk_color = get_risk_color(final_cat)
            advisory   = get_risk_advisory(final_cat)
            st.markdown(f"""<div style="text-align:center;margin:14px 0 8px;">
                <span class="risk-badge risk-{final_cat}">🌡️ Weather Risk: {final_cat}</span></div>
                <div style="border-left:5px solid {risk_color};border-radius:8px;padding:12px 16px;
                font-size:0.88rem;">{advisory}</div>""", unsafe_allow_html=True)

        # ── Yield Prediction ─────────────────────────────────────────────────
        st.markdown("#### 📈 Yield Prediction")
        if not pixel_size_m and not known_total_ha:
            st.info("ℹ️ Set pixel size in sidebar for area-based yield forecast (using 1000 ha placeholder).")
            area_for_yield = 1000.0
        else:
            area_for_yield = crop_ha if crop_ha else 1000.0

        image_year = img_date_used.year
        # Use new data_bank_ref_new.xlsx as primary ideal source; fall back to my_data.csv
        ideal_raw_for_yield = get_ideal_weather_from_xlsx(cname, xlsx_path=DATABANK_XLSX)
        if not ideal_raw_for_yield or all(v is None for v in ideal_raw_for_yield.values()):
            ideal_raw_for_yield = get_ideal_weather_for_crop(cname, my_data_df)
        actual_season_for_yield = aggregate_weather(daily_weather)

        with st.spinner(f"🧮 Running yield model + Monte Carlo for {cname.title()}..."):
            fc_result = run_yield_forecast(
                my_data_path=MY_DATA_PATH,
                crop=cname,
                season=season,
                image_year=image_year,
                agri_area_ha=area_for_yield,
                actual_weather=actual_season_for_yield,
                ideal_weather=ideal_raw_for_yield,
                n_forecast=1,  # current year only
            )

        if fc_result and fc_result.get("engine_ok"):
            fci = fc_result.get("fci", [])
            if fci:
                pred_yield = fci[0].get("pred", 0)
                est_prod   = pred_yield * area_for_yield / 1000  # tonnes

                # Apply yield_unit: convert all to same unit
                if yield_unit == "Tonnes":
                    disp_yield = pred_yield / 1000
                    disp_prod  = est_prod
                    unit_lbl   = "t/ha"
                else:
                    disp_yield = pred_yield
                    disp_prod  = est_prod * 1000  # tonnes → kg
                    unit_lbl   = "kg/ha"

                fmt = ".3f" if yield_unit == "Tonnes" else ",.0f"

                yc1, yc2 = st.columns(2)
                with yc1:
                    st.markdown(f"""<div class="stat-box">
                        <div class="stat-label">🌾 Predicted Yield</div>
                        <div class="stat-value green" style="font-size:1.2rem">{disp_yield:{fmt}}</div>
                        <div class="stat-sub">{unit_lbl}</div>
                    </div>""", unsafe_allow_html=True)
                with yc2:
                    prod_label = "Tonnes" if yield_unit == "Tonnes" else "kg"
                    st.markdown(f"""<div class="stat-box">
                        <div class="stat-label">🏭 Est. Production</div>
                        <div class="stat-value yellow" style="font-size:1.2rem">{disp_prod:,.1f}</div>
                        <div class="stat-sub">{prod_label} (from {fa(area_for_yield,'Hectares (ha)')})</div>
                    </div>""", unsafe_allow_html=True)

                prob_loss = fc_result.get("prob_loss", 0)
                eval_res  = fc_result.get("eval_res", {}).get("XGBoost", {})
                r2_pct    = eval_res.get("acc%", 0)

                st.markdown(f"""<div style="background:#0d1f33;border-radius:8px;padding:12px 16px;
                    margin-top:8px;font-size:0.84rem;color:#aac8e4;">
                    📊 Model R² = <b>{r2_pct:.1f}%</b> &nbsp;·&nbsp;
                    ⚠️ P(below threshold) = <b>{prob_loss*100:.1f}%</b> &nbsp;·&nbsp;
                </div>""", unsafe_allow_html=True)
            else:
                st.warning("No forecast intervals returned.")
        else:
            err = fc_result.get("error","Unknown") if fc_result else "Engine unavailable"
            st.warning(f"⚠️ Yield model: {err}. Ensure my_data.csv contains sufficient data for {cname}.")

        st.markdown("</div>", unsafe_allow_html=True)  # close crop-block div
        st.markdown("---")


# ═══════════════════════════════════════════════════════════════════════════════
# RENDER ALL TABS
# ═══════════════════════════════════════════════════════════════════════════════

with tab1:
    render_agri_map()

if tab2 is not None:
    with tab2: render_crop_classification()

if tab3 is not None:
    with tab3: render_growth_stage()

if tab4 is not None:
    with tab4: render_weather_yield()

# Footer
st.markdown("---")
band_lbl = f"{n_bands}-band TIFF" if is_tiff else "RGB Image"
date_src  = "TIFF metadata" if (tiff_img_date and is_tiff) else "filename/estimated"
st.caption(f"🛰️ AgriIntel v2 · {band_lbl} · {W}×{H}px · {uploaded.name} · Date from: {date_src}")
