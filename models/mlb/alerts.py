"""
alerts.py — Send bet alert via Gmail (SMS gateway) or Twilio text.
"""

import smtplib
from email.mime.text import MIMEText
from config import (GMAIL_ADDRESS, GMAIL_PASSWORD, ALERT_TO_EMAIL,
                    TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO)


def format_bets_message(bets, today_str, bankroll):
    """Format bet recommendations as a clean text message."""
    if not bets:
        return f"MLB Model {today_str}: No +EV bets today. Sit tight."

    lines = [f"MLB Picks {today_str} | Bankroll: ${bankroll:.0f}"]
    lines.append("=" * 40)
    for i, b in enumerate(bets[:5], 1):  # max 5 bets per alert
        lines.append(
            f"#{i} {b['bet_team']} ({b['bet_side']})"
            f"  Odds: {b['best_odds']:+.0f} @ {b['best_book']}"
            f"  Edge: +{b['edge_pct']:.1f}%"
            f"  Bet: ${b['kelly_bet_$']:.0f}"
            f"  Model: {b['model_prob']:.1f}% vs Mkt: {b['market_prob']:.1f}%"
        )
    lines.append(f"Total bets: {len(bets)} | Showing top {min(len(bets),5)}")
    return "\n".join(lines)


def send_email_alert(message, subject="MLB Betting Picks"):
    """Send alert via Gmail to email/SMS gateway."""
    try:
        msg = MIMEText(message)
        msg['Subject'] = subject
        msg['From']    = GMAIL_ADDRESS
        msg['To']      = ALERT_TO_EMAIL
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
            server.send_message(msg)
        print(f"Email alert sent to {ALERT_TO_EMAIL}")
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


def send_twilio_sms(message):
    """Send alert via Twilio SMS (more reliable than email gateway)."""
    try:
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(body=message[:1600], from_=TWILIO_FROM, to=TWILIO_TO)
        print(f"SMS sent to {TWILIO_TO}")
        return True
    except Exception as e:
        print(f"Twilio error: {e}")
        return False


def send_alert(bets, today_str, bankroll, method='email'):
    """Send alert using configured method."""
    message = format_bets_message(bets, today_str, bankroll)
    print("\n--- ALERT MESSAGE ---")
    print(message)
    print("---------------------\n")
    if method == 'twilio':
        return send_twilio_sms(message)
    else:
        return send_email_alert(message)
