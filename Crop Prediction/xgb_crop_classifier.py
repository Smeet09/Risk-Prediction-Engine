# =============================================================================
# xgb_crop_classifier.py  —  XGBoost Crop Classifier for Satellite Crop Analyzer
# Trained on clean_9crop.csv  |  Compatible with nn_classifier.py interface
# Drop-in replacement: same .classify(feat_matrix, doy, conf_threshold) API
# =============================================================================

import numpy as np
import pandas as pd
import pickle
import warnings
warnings.filterwarnings("ignore")

from pathlib import Path

# ── 18 spectral features (matches nn_classifier.py exactly) ──────────────────
SPECTRAL_FEATURES = [
    'b4','b8','b8a','b11','b12',
    'ndvi','ndre','gndvi','evi','savi',
    'cire','rvi','ndwi','lswi','ndmi',
    'bsi','ndbi','swir_ratio'
]

# 7 weather features present in clean_9crop.csv but NOT in satellite TIFF
# XGBoost was trained on 25 features; we pad weather columns with 0 at inference
WEATHER_FEATURES = [
    'temp_max', 'temp_min', 'rainfall',
    'humidity', 'soil_moisture', 'vpd', 'gdd'
]

ALL_FEATURES  = SPECTRAL_FEATURES + WEATHER_FEATURES   # 25 features for training
N_SPECTRAL    = len(SPECTRAL_FEATURES)                  # 18
N_FEATURES    = len(ALL_FEATURES)                       # 25


class XGBCropClassifier:
    """
    XGBoost crop classifier.
    - Trained on clean_9crop.csv (36,833 real satellite pixels).
    - Inference uses 18 spectral features + 7 zero-padded weather features.
    - Same .classify() interface as NNClassifier — drop-in replacement.
    - Provides .n_refs attribute so sidebar status line works unchanged.
    """

    def __init__(self, model_path: str = None):
        self.model           = None
        self.scaler          = None
        self.label_encoder   = None
        self.feature_names   = ALL_FEATURES
        self.classes_        = None
        self.feature_importances_ = None
        self.training_accuracy    = None
        self.val_accuracy         = None
        self.loaded          = False
        self.info            = "not loaded"
        self.model_path      = model_path
        self.n_refs          = 0          # shown in sidebar caption

    # ── Load pre-trained model ────────────────────────────────────────────────
    def load(self, path: str = None) -> "XGBCropClassifier":
        """Load saved model from .pkl file."""
        path = path or self.model_path
        if not path or not Path(path).exists():
            raise FileNotFoundError(f"XGBoost model not found: {path}")

        with open(path, "rb") as f:
            artifacts = pickle.load(f)

        self.model                = artifacts["model"]
        self.scaler               = artifacts["scaler"]
        self.label_encoder        = artifacts["label_encoder"]
        self.feature_names        = artifacts.get("feature_names", ALL_FEATURES)
        self.classes_             = artifacts["classes"]
        self.feature_importances_ = artifacts.get("feature_importances")
        self.training_accuracy    = artifacts.get("training_accuracy")
        self.val_accuracy         = artifacts.get("val_accuracy")
        self.loaded               = True

        n_crops = len(self.classes_)
        val_str = f"{self.val_accuracy:.1%}" if self.val_accuracy else "?"
        self.info    = f"XGBoost | {n_crops} crops | val acc {val_str}"
        self.n_refs  = n_crops      # for sidebar display

        print(f"[XGB] Loaded: {self.info}")
        return self

    # ── Train from scratch ────────────────────────────────────────────────────
    def train(self, csv_path: str, test_size: float = 0.2,
              random_state: int = 42, verbose: bool = True,
              save_path: str = None) -> dict:
        """
        Train on clean_9crop.csv and optionally save model.
        Run once; afterwards use load().
        """
        from xgboost import XGBClassifier
        from sklearn.preprocessing import LabelEncoder, StandardScaler
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import accuracy_score, classification_report

        df = pd.read_csv(csv_path)
        df.columns = [c.lower() for c in df.columns]

        # Accept files with 18 or 25 features
        available = [f for f in self.feature_names if f in df.columns]
        missing_w = [f for f in WEATHER_FEATURES if f not in df.columns]
        if missing_w:
            if verbose:
                print(f"[XGB] Weather features missing ({missing_w}), "
                      f"training on {N_SPECTRAL} spectral only")
            self.feature_names = SPECTRAL_FEATURES
        else:
            self.feature_names = ALL_FEATURES

        X = df[self.feature_names].fillna(0).values.astype(np.float32)
        y = df["crop_type"].astype(str).values

        self.label_encoder = LabelEncoder()
        y_enc = self.label_encoder.fit_transform(y)
        self.classes_ = self.label_encoder.classes_

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        X_tr, X_va, y_tr, y_va = train_test_split(
            X_scaled, y_enc, test_size=test_size,
            random_state=random_state, stratify=y_enc
        )

        # Class weights for imbalance
        unique, counts = np.unique(y_tr, return_counts=True)
        weights = len(y_tr) / (len(unique) * counts)
        sw = weights[y_tr]

        self.model = XGBClassifier(
            n_estimators=200, max_depth=6, learning_rate=0.1,
            subsample=0.8, colsample_bytree=0.8,
            min_child_weight=3, reg_alpha=0.5, reg_lambda=1.0,
            random_state=random_state, n_jobs=-1,
            tree_method="hist", device="cpu",
            eval_metric="mlogloss", early_stopping_rounds=20, verbose=0,
        )
        self.model.fit(
            X_tr, y_tr, sample_weight=sw,
            eval_set=[(X_va, y_va)], verbose=False,
        )

        self.training_accuracy    = float(
            np.mean(self.model.predict(X_tr) == y_tr))
        self.val_accuracy         = float(
            np.mean(self.model.predict(X_va) == y_va))
        self.feature_importances_ = self.model.feature_importances_
        self.loaded               = True
        self.n_refs               = len(self.classes_)
        self.info = (f"XGBoost | {len(self.classes_)} crops "
                     f"| train {self.training_accuracy:.1%} "
                     f"| val {self.val_accuracy:.1%}")

        if verbose:
            print(f"[XGB] {self.info}")
            print(classification_report(
                y_va, self.model.predict(X_va),
                target_names=self.classes_))

        if save_path:
            self.save(save_path)

        return {
            "train_accuracy": self.training_accuracy,
            "val_accuracy":   self.val_accuracy,
            "n_classes":      len(self.classes_),
            "classes":        list(self.classes_),
        }

    # ── Save ──────────────────────────────────────────────────────────────────
    def save(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        artifacts = {
            "model":              self.model,
            "scaler":             self.scaler,
            "label_encoder":      self.label_encoder,
            "feature_names":      self.feature_names,
            "classes":            self.classes_,
            "feature_importances": self.feature_importances_,
            "training_accuracy":  self.training_accuracy,
            "val_accuracy":       self.val_accuracy,
        }
        with open(path, "wb") as f:
            pickle.dump(artifacts, f)
        print(f"[XGB] Saved → {path}")

    # ── Classify — same API as NNClassifier ───────────────────────────────────
    def classify(self, feat_matrix: np.ndarray,
                 doy: int = 280,
                 conf_threshold: float = 0.30,
                 batch_size: int = 20000) -> tuple:
        """
        Classify pixels.  Mirrors NNClassifier.classify() exactly.

        Parameters
        ----------
        feat_matrix   : (N, 18) float32  — 18 spectral features from compute_features()
        doy           : int              — day of year (not used, kept for API compat)
        conf_threshold: float            — min probability to accept label
        batch_size    : int              — pixels per batch

        Returns
        -------
        labels      : (N,) str   — crop name per pixel
        confidences : (N,) float — max class probability 0..1
        stages      : (N,) str   — always 'Unknown' (XGB has no stage output)
        """
        if not self.loaded or self.model is None:
            raise RuntimeError("Call .load() before .classify()")

        N = feat_matrix.shape[0]
        labels      = np.full(N, "others", dtype=object)
        confidences = np.zeros(N, dtype=np.float32)
        stages      = np.full(N, "Unknown", dtype=object)

        # Pad 18 spectral → 25 features if model was trained on 25
        n_model_feats = len(self.feature_names)
        if feat_matrix.shape[1] == N_SPECTRAL and n_model_feats == N_FEATURES:
            pad = np.zeros((N, len(WEATHER_FEATURES)), dtype=np.float32)
            X_in = np.hstack([feat_matrix.astype(np.float32), pad])
        elif feat_matrix.shape[1] == n_model_feats:
            X_in = feat_matrix.astype(np.float32)
        else:
            raise ValueError(
                f"Expected {n_model_feats} features (or {N_SPECTRAL} spectral), "
                f"got {feat_matrix.shape[1]}"
            )

        # Scale
        X_scaled = self.scaler.transform(X_in)

        # Batch inference
        n_batches = max(1, int(np.ceil(N / batch_size)))
        for b in range(n_batches):
            s = b * batch_size
            e = min((b + 1) * batch_size, N)
            preds = self.model.predict(X_scaled[s:e])
            probs = self.model.predict_proba(X_scaled[s:e])
            conf  = probs.max(axis=1).astype(np.float32)
            ok    = conf >= conf_threshold
            names = self.label_encoder.inverse_transform(preds)
            labels[s:e][ok]      = names[ok]
            confidences[s:e][ok] = conf[ok]

        return labels, confidences, stages


# ─────────────────────────────────────────────────────────────────────────────
# Re-train helper — run from terminal to rebuild xgb_crop_model.pkl
# python xgb_crop_classifier.py  [optional: path/to/clean_9crop.csv]
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    csv_path   = sys.argv[1] if len(sys.argv) > 1 else "clean_9crop.csv"
    model_path = sys.argv[2] if len(sys.argv) > 2 else "xgb_crop_model.pkl"

    print(f"[XGB] Training on {csv_path}")
    clf = XGBCropClassifier()
    clf.train(csv_path, verbose=True, save_path=model_path)
    print(f"[XGB] ✅ Done — saved to {model_path}")