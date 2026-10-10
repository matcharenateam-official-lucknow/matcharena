"""OTP delivery providers: WhatsApp Cloud API, SMTP email, and a dev console fallback."""
import smtplib
import ssl
from email.mime.text import MIMEText

import requests

import config

WA_GRAPH = "https://graph.facebook.com/v22.0"


class ProviderError(Exception):
    pass


def _dev_sink(channel: str, target: str, code: str, expire_min: int) -> str:
    print(
        "\n" + "=" * 58 +
        f"\n  DEV OTP  ->  {channel}:{target}\n"
        f"  CODE: {code}   (valid {expire_min} min)\n"
        + "=" * 58 + "\n",
        flush=True,
    )
    return "dev"


def send_whatsapp_otp(phone_e164: str, code: str, expire_min: int) -> str:
    if not config.DEV_MODE and (not config.WA_TOKEN or not config.WA_PHONE_NUMBER_ID):
        raise ProviderError("WhatsApp is not configured (WA_TOKEN / WA_PHONE_NUMBER_ID missing)")
    if config.DEV_MODE and not config.WA_TOKEN:
        return _dev_sink("whatsapp", phone_e164, code, expire_min)

    payload = {
        "messaging_product": "whatsapp",
        "to": phone_e164.lstrip("+"),
        "type": "template",
        "template": {
            "name": config.WA_TEMPLATE,
            "language": {"code": config.WA_TEMPLATE_LANG},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": code},
                        {"type": "text", "text": str(expire_min)},
                    ],
                }
            ],
        },
    }
    resp = requests.post(
        f"{WA_GRAPH}/{config.WA_PHONE_NUMBER_ID}/messages",
        headers={
            "Authorization": f"Bearer {config.WA_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        try:
            err = resp.json().get("error", {})
            detail = f"{err.get('code')}: {err.get('message')}"
        except Exception:
            detail = resp.text[:200]
        raise ProviderError(f"WhatsApp API {resp.status_code} — {detail}")
    if config.DEV_MODE:
        return _dev_sink("whatsapp", phone_e164, code, expire_min)
    return "whatsapp"


def send_email_otp(email: str, code: str, expire_min: int) -> str:
    if not config.DEV_MODE and not (config.SMTP_USER and config.SMTP_PASS):
        raise ProviderError("Email is not configured (SMTP_USER / SMTP_PASS missing)")
    if config.DEV_MODE and not (config.SMTP_USER and config.SMTP_PASS):
        return _dev_sink("email", email, code, expire_min)

    body = (
        f"Your MatchArena verification code is: {code}\n\n"
        f"It is valid for {expire_min} minutes. If you did not request it, ignore this email."
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"{code} is your MatchArena code"
    msg["From"] = config.MAIL_FROM
    msg["To"] = email
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=context, timeout=20) as server:
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.send_message(msg)
    if config.DEV_MODE:
        return _dev_sink("email", email, code, expire_min)
    return "email"


def send_otp(channel: str, target: str, code: str, expire_min: int) -> str:
    """Returns how the code was delivered: 'whatsapp' | 'email' | 'dev'."""
    if channel == "whatsapp":
        return send_whatsapp_otp(target, code, expire_min)
    if channel == "email":
        return send_email_otp(target, code, expire_min)
    raise ProviderError(f"unknown channel {channel!r}")
