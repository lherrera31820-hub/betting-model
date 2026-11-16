"""Locked CLEAR_RULES_SEP15 decisions. No live odds."""

from __future__ import annotations

from prediction_core.gates import (
    RejectCode,
    apply_daily_cap,
    evaluate_ticket,
    GateDecision,
    GateTicket,
)


def _base(**kwargs) -> GateTicket:
    defaults = dict(
        price_source="shop",
        packet_sp=True,
        packet_sit=True,
        packet_wx=True,
        proposed_units=1.0,
    )
    defaults.update(kwargs)
    return GateTicket(**defaults)


def test_cfb_dog_clear_ok():
    decision = evaluate_ticket(
        _base(
            sport="CFB",
            market="SPREAD",
            side="AUB",
            american=110,
            line=7.0,
            model_pct=58.0,
            edge=3.2,
            is_home=False,
        )
    )
    assert decision.status == "CLEAR"
    assert decision.units == 1.0
    assert decision.reject_codes == []
    assert decision.is_dog is True


def test_soft_plus_ml_reject():
    # Mirrors Sep 13–14 HOU-style ticket: model 54 / edge 7.05 under dual gate.
    decision = evaluate_ticket(
        _base(
            sport="MLB",
            market="ML",
            side="HOU",
            american=127,
            model_pct=54.0,
            edge=7.05,
        )
    )
    assert decision.status == "FILL"
    assert decision.units == 0.0
    assert RejectCode.MLB_PLUS_ML_SOFT in decision.reject_codes


def test_fav_cfb_hold():
    decision = evaluate_ticket(
        _base(
            sport="CFB",
            market="SPREAD",
            side="SYR",
            american=-115,
            line=-3.5,
            model_pct=61.0,
            edge=1.8,
            is_home=True,
        )
    )
    assert decision.status == "HOLD"
    assert decision.units == 0.0
    assert RejectCode.CFB_FAV_BAR in decision.reject_codes


def test_cfb_fav_clears_only_with_raised_bar():
    decision = evaluate_ticket(
        _base(
            sport="CFB",
            market="SPREAD",
            side="MSST",
            american=-110,
            line=-7.0,
            model_pct=72.0,
            edge=5.5,
            is_home=True,
        )
    )
    assert decision.status == "CLEAR"
    assert decision.reject_codes == []


def test_opener_prim_auto_reject():
    decision = evaluate_ticket(
        _base(
            sport="MLB",
            market="ML",
            side="MIA",
            american=118,
            model_pct=66.0,
            edge=8.5,
            price_source="opener",
        )
    )
    assert decision.status == "FILL"
    assert RejectCode.OPENER_PRIM in decision.reject_codes


def test_f5_juice_skip_unless_model_75():
    decision = evaluate_ticket(
        _base(
            sport="MLB",
            market="F5",
            side="SEA",
            american=-225,
            model_pct=72.0,
            edge=4.0,
            f5_market_stamped=True,
        )
    )
    assert decision.status == "FILL"
    assert RejectCode.F5_JUICE in decision.reject_codes


def test_soccer_off():
    decision = evaluate_ticket(
        _base(
            sport="EPL",
            market="ML",
            side="ARS",
            american=-140,
            model_pct=62.0,
            edge=5.0,
        )
    )
    assert decision.status == "HOLD"
    assert RejectCode.SOCCER_OFF in decision.reject_codes


def test_missing_model_hold_never_invents():
    decision = evaluate_ticket(
        _base(
            sport="MLB",
            market="ML",
            side="NYY",
            american=-150,
            model_pct=None,
            edge=None,
        )
    )
    assert decision.status == "HOLD"
    assert RejectCode.NO_MODEL in decision.reject_codes
    assert decision.model_pct is None


def test_daily_cap_soft_five_units():
    clears = [
        GateDecision(
            status="CLEAR",
            sport="CFB",
            market="SPREAD",
            side=f"DOG{i}",
            model_pct=58.0,
            edge=3.0 + i,
            proposed_units=1.0,
            units=1.0,
            is_dog=True,
        )
        for i in range(6)
    ]
    out = apply_daily_cap(clears, cap_u=5.0)
    still_clear = [d for d in out if d.status == "CLEAR"]
    capped = [d for d in out if RejectCode.DAILY_CAP in d.reject_codes]
    assert len(still_clear) == 5
    assert sum(d.units for d in still_clear) == 5.0
    assert len(capped) == 1
    assert capped[0].status == "FILL"
