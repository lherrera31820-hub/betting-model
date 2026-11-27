"""Slate → candidates. Fixture prices only; no network; no invented live odds."""

from __future__ import annotations

import json
from pathlib import Path

from prediction_core.gates import RejectCode
from prediction_core.pipeline import CANDIDATES_SCHEMA_VERSION, run_slate

EXPECTED_STATUS = {
    "cfb-aub-ala:SPREAD:AUB": ("CLEAR", None),
    "mlb-hou-sea:ML:HOU": ("FILL", RejectCode.MLB_PLUS_ML_SOFT),
    "cfb-syr-unlv:SPREAD:SYR": ("HOLD", RejectCode.CFB_FAV_BAR),
    "mlb-atl-nym:ML:ATL": ("CLEAR", None),
    "mlb-sea-f5:F5:SEA": ("FILL", RejectCode.F5_JUICE),
    "mlb-mia-opener:ML:MIA": ("FILL", RejectCode.OPENER_PRIM),
    "nfl-kc-buf:SPREAD:BUF": ("CLEAR", None),
    "mlb-no-model:ML:NYY": ("HOLD", RejectCode.NO_MODEL),
    "soccer-ars-che:ML:ARS": ("HOLD", RejectCode.SOCCER_OFF),
}


def _load_slate(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixture_slate_is_labeled_not_live(fixture_slate_path: Path):
    slate = _load_slate(fixture_slate_path)
    assert slate["fixture"] is True
    assert "Not live odds" in slate["note"]
    for game in slate["games"]:
        for ticket in game.get("tickets") or []:
            prices = ticket.get("prices") or {}
            for stamp in prices.values():
                if not stamp:
                    continue
                book = stamp.get("book") or ""
                assert book.startswith("FIXTURE") or book == "", book


def test_pipeline_gate_matrix(fixture_slate_path: Path):
    packet = run_slate(_load_slate(fixture_slate_path))
    assert packet["schema_version"] == CANDIDATES_SCHEMA_VERSION
    assert packet["writer"] == "Main"
    assert packet["soccer"] == "off"
    assert packet["scoreboard"] == "side_pass_fail"
    by_id = {c["id"]: c for c in packet["candidates"]}
    for cid, (status, code) in EXPECTED_STATUS.items():
        assert cid in by_id, cid
        cand = by_id[cid]
        assert cand["status"] == status, (cid, cand["status"], cand["reject_codes"])
        if code:
            assert code in cand["reject_codes"], (cid, cand["reject_codes"])
        if status == "CLEAR":
            assert cand["units"] > 0
            assert cand["model_pct"] is not None
        else:
            assert cand["units"] == 0
    # Missing-model ticket must not grow a fabricated MODEL%.
    assert by_id["mlb-no-model:ML:NYY"]["model_pct"] is None


def test_computed_mlb_path_uses_supplied_lambda(fixture_slate_path: Path):
    packet = run_slate(_load_slate(fixture_slate_path))
    computed = next(c for c in packet["candidates"] if c["game_id"] == "mlb-computed-lambda")
    assert computed["model_source"] == "computed"
    assert computed["model_pct"] is not None
    assert computed["extras"]["lam_home"] == 4.8
    assert computed["extras"]["lam_away"] == 4.1
    # Home λ 4.8 vs 4.1 should be a home lean, not a 50/50 invention.
    assert computed["model_pct"] > 50.0


def test_daily_units_under_soft_cap(fixture_slate_path: Path):
    packet = run_slate(_load_slate(fixture_slate_path))
    assert packet["daily"]["clear_u"] <= packet["daily"]["cap_u"]
    # Fixture CLEARs: CFB dog, MLB +ML dual-gate pass, NFL blend edge, computed λ home ML.
    assert packet["summary"]["CLEAR"] == 4
    assert packet["daily"]["clear_u"] == 4.0


def test_sample_candidates_json_matches_pipeline(fixture_slate_path: Path, repo_root: Path):
    sample_path = repo_root / "prediction_core" / "fixtures" / "candidates.json"
    assert sample_path.is_file(), "commit sample candidates.json from the fixture slate"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    live = run_slate(_load_slate(fixture_slate_path))
    assert sample["summary"] == live["summary"]
    live_status = {c["id"]: (c["status"], c["reject_codes"]) for c in live["candidates"]}
    sample_status = {c["id"]: (c["status"], c["reject_codes"]) for c in sample["candidates"]}
    assert sample_status == live_status


def test_cli_prints_statuses(fixture_slate_path: Path, tmp_path: Path, capsys):
    from prediction_core.cli import main

    out = tmp_path / "out.json"
    rc = main(["predict-slate", "--slate", str(fixture_slate_path), "--out", str(out)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "CLEAR" in printed
    assert "FILL" in printed
    assert "HOLD" in printed
    assert "REJECT_MLB_PLUS_ML_SOFT" in printed
    assert "REJECT_CFB_FAV_BAR" in printed
    assert out.is_file()
