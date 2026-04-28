# =============================================================================
# nn_classifier.py  —  Nearest-Neighbour crop classifier
# NO sklearn, NO joblib, NO model training required.
# Pure numpy — safe for Streamlit st.cache_resource.
# =============================================================================

import numpy as np
import pandas as pd

SPECTRAL_FEATURES = [
    'b4','b8','b8a','b11','b12',
    'ndvi','ndre','gndvi','evi','savi',
    'cire','rvi','ndwi','lswi','ndmi',
    'bsi','ndbi','swir_ratio'
]

# Weights — higher = more important for separating crops
FEATURE_WEIGHTS = np.array([
    1.0, 2.0, 1.5, 2.0, 1.5,   # b4  b8  b8a  b11  b12
    3.0, 2.5, 2.0, 2.0, 2.0,   # ndvi ndre gndvi evi savi
    1.5, 1.5, 2.5, 2.5, 2.0,   # cire rvi ndwi lswi ndmi
    1.5, 1.0, 2.0,              # bsi ndbi swir_ratio
], dtype=np.float32)


class NNClassifier:
    """
    Nearest-Neighbour classifier.
    Load reference CSV once, then classify any number of pixel arrays.
    Uses only numpy — no sklearn dependency.
    """

    def __init__(self):
        self.ref_scaled  = None
        self.ref_labels  = None
        self.ref_stages  = None
        self.ref_doy_min = None
        self.ref_doy_max = None
        self.feat_mean   = None
        self.feat_std    = None
        self.n_refs      = 0
        self.loaded      = False
        self.info        = "not loaded"

    def load(self, data_bank_csv="data_bank_reference.csv",
             gee_csv=None, mode="data_bank"):
        """
        Load reference data.
        mode: 'data_bank' | 'gee' | 'combined'
        """
        dfs = []

        # ── Data bank CSV ──────────────────────────────────────────────────────
        if mode in ("data_bank", "combined"):
            try:
                db = pd.read_csv(data_bank_csv)
                db.columns = [c.lower() for c in db.columns]
                if 'stage'   not in db.columns: db['stage']   = 'Unknown'
                if 'doy_min' not in db.columns: db['doy_min'] = 0
                if 'doy_max' not in db.columns: db['doy_max'] = 366
                dfs.append(db)
                print(f"[NN] Data bank loaded: {len(db)} rows")
            except Exception as e:
                print(f"[NN] Data bank error: {e}")

        # ── GEE CSV ────────────────────────────────────────────────────────────
        if mode in ("gee", "combined") and gee_csv:
            try:
                gee = pd.read_csv(gee_csv)
                gee.columns = [c.lower() for c in gee.columns]

                # Normalise crop column name
                for col in ['crop_type','crop_name','crop_class','label']:
                    if col in gee.columns:
                        gee = gee.rename(columns={col: 'crop_type'})
                        break

                if 'crop_type' not in gee.columns:
                    print("[NN] GEE CSV: no crop column found — skipping")
                else:
                    if 'stage'   not in gee.columns: gee['stage']   = 'Unknown'
                    if 'doy_min' not in gee.columns: gee['doy_min'] = 0
                    if 'doy_max' not in gee.columns: gee['doy_max'] = 366

                    # Rescale bands if still in raw DN
                    for b in ['b4','b8','b8a','b11','b12']:
                        if b in gee.columns and gee[b].max() > 10:
                            gee[b] = gee[b] / 10000.0

                    # NDVI filter
                    if 'ndvi' in gee.columns:
                        gee = gee[gee['ndvi'] >= 0.10]

                    # Sub-sample — keep max 500 per crop for speed
                    if len(gee) > 4500:
                        gee = (gee.groupby('crop_type', group_keys=False)
                                  .apply(lambda x: x.sample(min(len(x), 500), random_state=42))
                                  .reset_index(drop=True))

                    dfs.append(gee)
                    print(f"[NN] GEE loaded: {len(gee)} rows")
            except Exception as e:
                print(f"[NN] GEE error: {e}")

        if not dfs:
            raise RuntimeError(
                f"No reference data loaded (mode='{mode}'). "
                "Check that data_bank_reference.csv exists in project folder."
            )

        ref = pd.concat(dfs, ignore_index=True)

        # Verify all 18 features present
        missing = [f for f in SPECTRAL_FEATURES if f not in ref.columns]
        if missing:
            raise RuntimeError(f"Reference CSV missing columns: {missing}")

        X = ref[SPECTRAL_FEATURES].fillna(0).values.astype(np.float32)

        # Z-score normalisation (manual — no sklearn)
        self.feat_mean = X.mean(axis=0)
        self.feat_std  = X.std(axis=0)
        self.feat_std[self.feat_std < 1e-6] = 1.0

        X_z = (X - self.feat_mean) / self.feat_std
        # Pre-multiply by weights so we only do it once
        self.ref_scaled  = X_z * FEATURE_WEIGHTS      # (n_refs, 18)
        self.ref_labels  = ref['crop_type'].values.astype(str)
        self.ref_stages  = ref['stage'].values.astype(str)
        self.ref_doy_min = ref['doy_min'].fillna(0).values.astype(int)
        self.ref_doy_max = ref['doy_max'].fillna(366).values.astype(int)
        self.n_refs      = len(ref)
        self.loaded      = True
        self.info        = f"mode={mode} | {self.n_refs} refs | crops={sorted(set(self.ref_labels))}"
        print(f"[NN] Ready: {self.info}")
        return self

    def classify(self, feat_matrix: np.ndarray,
                 doy: int = 280,
                 conf_threshold: float = 0.30,
                 batch_size: int = 20000) -> tuple:
        """
        Classify pixels by nearest-neighbour matching.

        Parameters
        ----------
        feat_matrix    : (N, 18) float32 — pixel spectral features
        doy            : Day of Year of the image (for seasonal filtering)
        conf_threshold : min confidence to accept a match (0 = accept all)
        batch_size     : pixels per batch (lower = less RAM)

        Returns
        -------
        labels      : (N,) str   — crop name per pixel
        confidences : (N,) float — match confidence 0..1
        stages      : (N,) str   — growth stage per pixel
        """
        if not self.loaded:
            raise RuntimeError("Call .load() before .classify()")

        N = feat_matrix.shape[0]
        labels      = np.full(N, 'others', dtype=object)
        confidences = np.zeros(N, dtype=np.float32)
        stages      = np.full(N, 'Unknown', dtype=object)

        # Filter reference to rows active at this DOY
        doy_mask = (self.ref_doy_min <= doy) & (self.ref_doy_max >= doy)
        if doy_mask.sum() < 5:
            doy_mask = np.ones(self.n_refs, dtype=bool)   # fallback: all rows

        ref_w = self.ref_scaled[doy_mask]                 # (m, 18)
        r_lbl = self.ref_labels[doy_mask]
        r_stg = self.ref_stages[doy_mask]

        # Scale pixel features using same stats
        pix   = (feat_matrix.astype(np.float32) - self.feat_mean) / self.feat_std
        pix_w = pix * FEATURE_WEIGHTS                     # (N, 18) weighted

        n_batches = max(1, int(np.ceil(N / batch_size)))

        for b in range(n_batches):
            s = b * batch_size
            e = min((b+1)*batch_size, N)
            pb = pix_w[s:e]                               # (bs, 18)

            # Fast squared distance via: ||p-r||^2 = ||p||^2 + ||r||^2 - 2p@r.T
            p2 = (pb**2).sum(axis=1, keepdims=True)       # (bs, 1)
            r2 = (ref_w**2).sum(axis=1)[np.newaxis, :]    # (1, m)
            cr = pb @ ref_w.T                             # (bs, m)
            d2 = np.maximum(p2 + r2 - 2*cr, 0.0)         # (bs, m)

            bi    = d2.argmin(axis=1)                     # (bs,)
            bd    = np.sqrt(d2[np.arange(e-s), bi])
            md    = np.sqrt(d2.max(axis=1))
            conf  = 1.0 - bd / (md + 1e-6)

            ok = conf >= conf_threshold
            labels[s:e][ok]      = r_lbl[bi[ok]]
            confidences[s:e][ok] = conf[ok].astype(np.float32)
            stages[s:e][ok]      = r_stg[bi[ok]]

        return labels, confidences, stages