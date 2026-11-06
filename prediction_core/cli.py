"""CLI: ``predict-slate`` — slate JSON in, candidates + gate table out."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO

from prediction_core.pipeline import run_slate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prediction_core",
        description=(
            "Prediction Core v1 — calibrated probs, market join, locked CLEAR gates. "
            "Emits candidate JSON for Main (sole CLEAR writer). Soccer OFF."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    pred = sub.add_parser("predict-slate", help="Evaluate a slate JSON and print CLEAR/FILL/HOLD")
    pred.add_argument("--slate", required=True, help="Path to slate JSON (no network)")
    pred.add_argument("--out", help="Write candidates JSON to this path")
    pred.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent for --out (default 2)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "predict-slate":
        return _predict_slate(args.slate, args.out, args.indent)
    parser.error(f"unknown command {args.command}")
    return 2


def _predict_slate(slate_path: str, out_path: str | None, indent: int) -> int:
    path = Path(slate_path)
    if not path.is_file():
        print(f"error: slate not found: {path}", file=sys.stderr)
        return 2
    with path.open(encoding="utf-8") as fh:
        slate = json.load(fh)
    packet = run_slate(slate)
    print_gate_table(packet)
    if out_path:
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(packet, indent=indent) + "\n", encoding="utf-8")
        print(f"\nwrote {dest}")
    return 0


def print_gate_table(packet: dict[str, Any], stream: TextIO | None = None) -> None:
    out = stream or sys.stdout
    summary = packet.get("summary") or {}
    daily = packet.get("daily") or {}
    out.write(
        f"slate {packet.get('slate_id')}  as_of {packet.get('as_of')}  "
        f"writer={packet.get('writer')}  soccer={packet.get('soccer')}\n"
    )
    out.write(
        f"CLEAR {summary.get('CLEAR', 0)}  FILL {summary.get('FILL', 0)}  "
        f"HOLD {summary.get('HOLD', 0)}  "
        f"units {daily.get('clear_u', 0)}/{daily.get('cap_u', 0)}\n"
    )
    out.write(
        f"{'STATUS':<6} {'SPORT':<5} {'MKT':<6} {'SIDE':<8} {'AMER':>6} "
        f"{'MODEL':>6} {'EDGE':>6} {'U':>4}  REASONS\n"
    )
    out.write("-" * 92 + "\n")
    for cand in packet.get("candidates") or []:
        reasons = cand.get("reject_codes") or []
        if not reasons:
            reasons = ["CLEAR"]
        side = str(cand.get("side") or "")
        line = cand.get("line")
        if line is not None and cand.get("market") in {"SPREAD", "ATS"}:
            try:
                side_disp = f"{side} {float(line):+g}"
            except (TypeError, ValueError):
                side_disp = side
        else:
            side_disp = side
        model = cand.get("model_pct")
        edge = cand.get("edge")
        american = cand.get("american")
        out.write(
            f"{cand.get('status', '?'):<6} "
            f"{str(cand.get('sport') or ''):<5} "
            f"{str(cand.get('market') or ''):<6} "
            f"{side_disp:<8} "
            f"{_fmt_num(american, 6, as_int=True)} "
            f"{_fmt_num(model, 6)} "
            f"{_fmt_num(edge, 6)} "
            f"{_fmt_num(cand.get('units'), 4)}  "
            f"{', '.join(reasons)}\n"
        )
        extra = cand.get("reasons") or []
        for line_reason in extra:
            out.write(f"       {line_reason}\n")


def _fmt_num(value: Any, width: int, *, as_int: bool = False) -> str:
    if value is None:
        return f"{'—':>{width}}"
    try:
        if as_int:
            return f"{int(round(float(value))):>{width}d}"
        return f"{float(value):>{width}.1f}"
    except (TypeError, ValueError):
        return f"{str(value):>{width}}"


if __name__ == "__main__":
    raise SystemExit(main())
