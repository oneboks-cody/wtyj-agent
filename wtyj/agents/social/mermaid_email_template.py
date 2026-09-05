"""Premium transactional email, rendered from a recorded Mermaid reservation."""
from __future__ import annotations

import html
import json
from decimal import Decimal
from pathlib import Path

from shared import config_loader, mermaid_catalog
from agents.social import mermaid_guest_experience as guest


def settings() -> dict:
    configured = Path(config_loader._CONFIG_PATH).with_name("reservation_email.json")
    bundled = Path(__file__).with_name("mermaid_reservation_email.json")
    source = Path(__file__).resolve().parents[3] / "clients/mermaid/config/reservation_email.json"
    path = next((p for p in (configured, bundled, source) if p.is_file()), configured)
    return json.loads(path.read_text(encoding="utf-8"))


def copy_for(locale: str) -> dict:
    copies = settings()["copies"]
    return copies.get(locale, copies["en"])


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _esc(value: object) -> str:
    return html.escape(_clean(value), quote=True)


def _amount(currency: object, value: object) -> str:
    return f"{_clean(currency)} {Decimal(str(value)):,.2f}"


def render_email(reservation: dict, payment: dict, recipient: str, *,
                 receipt_url: str, hero_url: str) -> dict:
    """Return subject/text/HTML without sending, querying state, or changing prices.

    The caller authorizes the recipient and supplies validated public HTTPS URLs.
    Only the guest-facing intake fields below are included; operator notes and
    workflow metadata never enter the email.
    """
    locale = reservation.get("language", "en")
    copy = copy_for(locale)
    intake = reservation["intake"]
    money = reservation["monetary_snapshot"]
    raw = config_loader.get_raw() or {}
    brand = raw["business"]["name"]
    contact = raw.get("contact_methods") or {}
    brand_phone = contact.get("published_phone") or raw["business"].get("phone", "")
    brand_email = contact.get("email") or raw["business"].get("email", "")
    name = _clean(reservation.get("customer_name") or intake.get("customer_name"))
    date = guest.guest_date(intake["trip_date"], locale)
    party = guest.party_text(intake, locale)
    code = guest.display_reference(reservation["booking_code"])
    paid = _amount(payment["currency"], payment["amount"])
    transport = guest.transport_text(intake, locale, money)
    departure = guest.guest_copy(locale)["island_departure"].format(
        time=mermaid_catalog.get_catalog()["service"]["island_departure_time"])
    greeting = copy["greeting"].format(name=name.split()[0] if name else copy["guest"])
    subject = _clean(copy["subject"].format(date=date, code=code))
    rows = [(copy["booking_code"], code), (copy["guest_label"], name),
            (copy["guests_label"], party)]
    if intake.get("contact_phone"):
        rows.append((copy["phone_label"], intake["contact_phone"]))
    rows.append((copy["email_label"], recipient))
    if payment.get("payment_reference"):
        rows.append((copy["payment_reference"], guest.display_reference(payment["payment_reference"])))

    notes = [(copy[key], intake[key]) for key in
             ("dietary_requirements", "accessibility_notes", "special_requests")
             if isinstance(intake.get(key), str) and intake[key].strip()]
    price_lines = []
    for item in money.get("items", []):
        if not item.get("quantity"):
            continue
        label = guest.pickup_label(money, locale) if item["key"] == "pickup" else copy["items"][item["key"]]
        price_lines.append((f"{item['quantity']} × {label}", _amount(money["currency"], item["line_total"])))

    policies = settings()["policy_links"]
    text_parts = [brand, copy["title"], greeting, copy["intro"], date,
                  "\n".join(f"{_clean(k)}: {_clean(v)}" for k, v in rows),
                  copy["transport_title"] + "\n" + transport + "\n" + departure,
                  copy["payment_title"] + "\n" + "\n".join(f"{label}: {value}" for label, value in price_lines)
                  + "\n" + copy["paid"] + ": " + paid,
                  copy["receipt_button"] + ": " + receipt_url, copy["receipt_note"]]
    if notes:
        text_parts.append(copy["requests_title"] + "\n" + "\n".join(f"{label}: {_clean(value)}" for label, value in notes))
    for title, content in (("included_title", "included"), ("activities_title", "activities"),
                           ("bring_title", "bring"), ("practical_title", "practical"),
                           ("policies_title", "policy_summary")):
        text_parts.append(copy[title] + "\n" + copy[content])
    text_parts += ["\n".join(copy[key] + ": " + policies[key] for key in
                              ("general_conditions", "privacy", "cancellation")),
                   copy["thanks"], brand, " · ".join(x for x in (brand_phone, brand_email) if x)]

    paragraph = "margin:0 0 14px;font-size:15px;line-height:1.65;color:#435b64;"
    def p(value):
        return f'<p style="{paragraph}">{_esc(value)}</p>'

    def section(title, content):
        return (f'<h2 style="margin:26px 0 9px;font-size:18px;line-height:1.35;'
                f'font-weight:700;color:#083e54;">{_esc(title)}</h2>' + p(content))

    details = "".join(
        '<tr><td valign="top" width="34%" style="padding:9px 10px 9px 0;'
        'border-bottom:1px solid #dbe8eb;font-size:13px;line-height:1.55;color:#55707b;">'
        f'{_esc(label)}</td><td valign="top" style="padding:9px 0;border-bottom:1px solid #dbe8eb;'
        f'font-size:14px;line-height:1.55;color:#123e4e;overflow-wrap:anywhere;word-break:break-word;">{_esc(value)}</td></tr>'
        for label, value in rows)
    charges = "".join(
        '<tr><td style="padding:5px 12px 5px 0;font-size:14px;line-height:1.5;color:#435b64;">'
        f'{_esc(label)}</td><td align="right" style="padding:5px 0;font-size:14px;'
        f'line-height:1.5;white-space:nowrap;color:#123e4e;">{_esc(value)}</td></tr>'
        for label, value in price_lines)
    request_html = ""
    if notes:
        request_html = section(copy["requests_title"], copy["requests_note"])
        request_html += "".join(p(f"{label}: {_clean(value)}") for label, value in notes)
    policy_html = '<br>'.join(
        f'<a href="{_esc(policies[key])}" style="color:#007d9b;text-decoration:underline;'
        f'font-size:14px;line-height:2;">{_esc(copy[key])}</a>'
        for key in ("general_conditions", "privacy", "cancellation"))

    body = f'''<!DOCTYPE html>
<html lang="{_esc(locale)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{_esc(subject)}</title></head>
<body style="margin:0;padding:0;background-color:#eef4f5;font-family:Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;mso-hide:all;">{_esc(copy['preheader'])}</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#eef4f5;"><tr><td align="center" style="padding:26px 12px;">
<!--[if mso]><table role="presentation" width="640" align="center"><tr><td><![endif]-->
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="width:100%;max-width:640px;background-color:#ffffff;border-radius:14px;overflow:hidden;">
<tr><td style="padding:22px 28px 19px;background-color:#ffffff;border-top:5px solid #0c839e;">
<p style="margin:0;color:#075069;font-size:17px;line-height:1.35;font-weight:700;letter-spacing:1px;">{_esc(brand)}</p>
<p style="margin:5px 0 0;color:#64818a;font-size:11px;line-height:1.5;letter-spacing:2px;text-transform:uppercase;">{_esc(copy['eyebrow'])}</p></td></tr>
<tr><td><img src="{_esc(hero_url)}" width="640" alt="{_esc(copy['hero_alt'])}" border="0" style="display:block;width:100%;max-width:640px;height:auto;color:#075069;font-size:16px;"></td></tr>
<tr><td style="padding:28px 28px 14px;">
<h1 style="margin:0 0 17px;color:#083e54;font-size:30px;line-height:1.15;font-weight:700;">{_esc(copy['title'])}</h1>
<p style="margin:0 0 8px;color:#123e4e;font-size:18px;line-height:1.5;font-weight:700;">{_esc(greeting)}</p>{p(copy['intro'])}
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#eef8fa;border-radius:10px;"><tr><td style="padding:20px;">
<p style="margin:0 0 6px;color:#4a7380;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;">{_esc(copy['date_label'])}</p>
<p style="margin:0 0 13px;color:#083e54;font-size:22px;line-height:1.4;font-weight:700;">{_esc(date)}</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">{details}</table>
</td></tr></table>
{section(copy['transport_title'], transport)}{p(departure)}
<h2 style="margin:25px 0 10px;font-size:18px;color:#083e54;">{_esc(copy['payment_title'])}</h2>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">{charges}
<tr><td style="padding:13px 0;border-top:1px solid #dbe8eb;color:#083e54;font-size:16px;font-weight:700;">{_esc(copy['paid'])}</td><td align="right" style="padding:13px 0;border-top:1px solid #dbe8eb;color:#083e54;font-size:23px;font-weight:700;white-space:nowrap;">{_esc(paid)}</td></tr></table>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:16px 0 12px;"><tr><td align="center" bgcolor="#007d9b" style="border-radius:7px;mso-padding-alt:15px 25px;">
<a href="{_esc(receipt_url)}" style="display:inline-block;padding:15px 25px;color:#ffffff;font-size:15px;line-height:1.25;font-weight:700;text-decoration:none;border:1px solid #007d9b;border-radius:7px;">{_esc(copy['receipt_button'])}</a></td></tr></table>
<p style="margin:0;color:#64818a;font-size:12px;line-height:1.6;">{_esc(copy['receipt_note'])}</p>
{request_html}
{section(copy['included_title'], copy['included'])}
{section(copy['activities_title'], copy['activities'])}
{section(copy['bring_title'], copy['bring'])}
</td></tr>
<tr><td style="padding:0 28px 24px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#f7f4eb;border-radius:10px;"><tr><td style="padding:0 20px 17px;">
{section(copy['practical_title'], copy['practical'])}
{section(copy['policies_title'], copy['policy_summary'])}
{policy_html}</td></tr></table></td></tr>
<tr><td style="padding:0 28px 30px;">{p(copy['thanks'])}<p style="margin:0;color:#075069;font-size:15px;font-weight:700;line-height:1.5;">{_esc(raw['business'].get('agent_name', ''))}<br>{_esc(brand)}</p></td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
<p style="margin:18px 0 0;font-size:12px;line-height:1.8;color:#6d848c;">{_esc(brand)}<br>{_esc(' · '.join(x for x in (brand_phone, brand_email) if x))}</p>
</td></tr></table></body></html>'''
    return {"subject": subject, "text": "\n\n".join(text_parts), "html": body}
