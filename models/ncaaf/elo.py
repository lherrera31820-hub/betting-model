"""
elo.py — Elo rating tracker for NFL.

There's no free equivalent of MLB's statsapi historical stats for NFL, so
this sport runs on Elo alone (no trained ensemble): ratings start neutral
(1500) for every team and update game-by-game as real results come in via
sportsdata.py. This is the same cold-start fallback logic models/mlb/model.py
uses before a trained model exists.
"""

import os
import pickle

from config import ELO_K, ELO_HFA


class EloTracker:
    """Tracks Elo ratings across a season. Ratings persist between daily runs."""

    def __init__(self, k=ELO_K, hfa=ELO_HFA):
        self.k = k
        self.hfa = hfa
        self.ratings = {}

    def get_rating(self, team_key):
        return self.ratings.get(team_key, 1500)

    def predict(self, home_key, away_key):
        """Return P(home win)."""
        rh = self.get_rating(home_key) + self.hfa
        ra = self.get_rating(away_key)
        return 1 / (1 + 10 ** ((ra - rh) / 400))

    def update(self, home_key, away_key, home_win):
        """home_win: 1.0 if home team won, 0.0 if away won, 0.5 for a tie."""
        p = self.predict(home_key, away_key)
        self.ratings[home_key] = self.get_rating(home_key) + self.k * (home_win - p)
        self.ratings[away_key] = self.get_rating(away_key) + self.k * ((1 - home_win) - (1 - p))

    def save(self, path):
        with open(path, "wb") as f:
            pickle.dump(self.ratings, f)

    def load(self, path):
        if os.path.exists(path):
            with open(path, "rb") as f:
                self.ratings = pickle.load(f)
