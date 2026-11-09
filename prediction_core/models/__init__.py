"""Sport-specific model interfaces. Soccer is out of scope."""

from prediction_core.models.cfb import ensemble_margin, predict_cfb
from prediction_core.models.mlb import elo_win_prob, predict_mlb
from prediction_core.models.nfl import blend_margin, gaussian_cover_prob, predict_nfl

__all__ = [
    "blend_margin",
    "elo_win_prob",
    "ensemble_margin",
    "gaussian_cover_prob",
    "predict_cfb",
    "predict_mlb",
    "predict_nfl",
]
