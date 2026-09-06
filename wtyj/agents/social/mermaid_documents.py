"""Localized Mermaid demo PDFs, signed downloads, and delivery evidence."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi.responses import FileResponse, Response
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, TableStyle

from agents.social.mermaid_document_copy import DOCUMENT_LANGUAGES, DOCUMENT_NOTICES
from agents.social.mermaid_pdf_structure import (
    HRFlowable, Image, Paragraph, Structure, Table, canvas_for,
)

from agents.social import mermaid_reservation_store
from shared import state_registry, mermaid_catalog
from agents.social import mermaid_guest_experience as guest


TEAL = colors.HexColor("#007F86")
DEEP = colors.HexColor("#063B46")
CORAL = colors.HexColor("#F36C5B")
MARKER_TEXT = colors.HexColor("#082B32")
PALE = colors.HexColor("#EAF8F8")
MUTED = colors.HexColor("#4D6870")
HERO_IMAGE = Path(__file__).resolve().parents[2] / "assets" / "mermaid-tropical-hero.png"


class _HeroImage(Image):
    """Clip the decorative banner to its layout box without stretching the photo."""

    def __init__(self, width_mm: float, height_mm: float | None = None):
        super().__init__(str(HERO_IMAGE), width=width_mm * mm, height=width_mm * mm / 3)
        self.viewport_height = (height_mm if height_mm is not None else width_mm / 3) * mm

    def wrap(self, available_width, available_height):
        return self.drawWidth, self.viewport_height

    def draw(self):
        self.canv.saveState()
        try:
            clip = self.canv.beginPath()
            clip.rect(0, 0, self.drawWidth, self.viewport_height)
            self.canv.clipPath(clip, stroke=0, fill=0)
            # Favor the upper part so the boat stays whole in the quote's shallow banner.
            self.canv.translate(0, (self.viewport_height - self.drawHeight) * 0.8)
            super().draw()
        finally:
            self.canv.restoreState()


def _hero(width_mm: float, height_mm: float | None = None) -> Image:
    """Bundled tropical artwork; provenance is recorded beside the asset."""
    return _HeroImage(width_mm, height_mm)

LABELS = {
    "en": {"title": "Your Klein Curaçao quote", "quote": "Quote", "customer": "Guest", "date": "Trip date", "guests": "Guests", "transport": "Transport", "charges": "Itemized price", "description": "Description", "qty": "Qty", "unit": "Unit", "amount": "Amount", "total": "Total", "included": "Everything included", "schedule": "Your day", "bring": "Bring with you", "rules": "Rules and important notes", "payment": "Next step", "payment_text": "Use the payment link sent in WhatsApp to complete your booking.", "arrival": "Arrive at Fishermen's Pier at 06:45. The published island departure is approximately 15:20.", "pickup": "Hotel pickup requested; location and price require confirmation.", "pier": "Meet at Fishermen's Pier", "valid": "This quote is valid for 60 minutes.", "available": ""},
    "nl": {
    "title": "Uw offerte voor Klein Curaçao",
    "quote": "Offerte",
    "customer": "Gast",
    "date": "Datum",
    "guests": "Gasten",
    "transport": "Vervoer",
    "charges": "Kostenoverzicht",
    "description": "Omschrijving",
    "qty": "Aantal",
    "unit": "Per stuk",
    "amount": "Bedrag",
    "total": "Totaal",
    "included": "Dit is inbegrepen",
    "schedule": "Dagprogramma",
    "bring": "Wat neemt u mee?",
    "rules": "Praktische informatie en voorwaarden",
    "payment": "Volgende stap",
    "payment_text": "Rond uw boeking af via de betaallink in WhatsApp.",
    "arrival": "We verwachten u om 06:45 bij Fishermen's Pier. De terugvaart vanaf Klein Curaçao is gepland rond 15:20.",
    "pickup": "Hoteltransfer aangevraagd; locatie en prijs moeten worden bevestigd.",
    "pier": "Ontmoeting bij Fishermen's Pier",
    "valid": "Deze offerte is 60 minuten geldig.",
    "available": ""
},
    "de": {"title": "Ihr Angebot für Klein Curaçao", "quote": "Angebot", "customer": "Gast", "date": "Ausflugsdatum", "guests": "Gäste", "transport": "Transport", "charges": "Preisübersicht", "description": "Beschreibung", "qty": "Anzahl", "unit": "Einzelpreis", "amount": "Betrag", "total": "Gesamt", "included": "Alles inklusive", "schedule": "Ihr Tag", "bring": "Bitte mitbringen", "rules": "Regeln und wichtige Hinweise", "payment": "Nächster Schritt", "payment_text": "Schließen Sie Ihre Buchung über den Zahlungslink in WhatsApp ab.", "arrival": "Seien Sie um 06:45 am Fishermen's Pier. Die veröffentlichte Abfahrt von der Insel ist ungefähr um 15:20.", "pickup": "Hotelabholung angefragt; Ort und Preis müssen bestätigt werden.", "pier": "Treffpunkt Fishermen's Pier", "valid": "Dieses Angebot ist 60 Minuten gültig.", "available": ""},
    "es": {"title": "Su cotización para Klein Curaçao", "quote": "Cotización", "customer": "Pasajero", "date": "Fecha", "guests": "Pasajeros", "transport": "Transporte", "charges": "Precio detallado", "description": "Descripción", "qty": "Cant.", "unit": "Unidad", "amount": "Importe", "total": "Total", "included": "Todo incluido", "schedule": "Su día", "bring": "Qué llevar", "rules": "Reglas e información importante", "payment": "Siguiente paso", "payment_text": "Complete su reserva con el enlace de pago enviado por WhatsApp.", "arrival": "Llegue a Fishermen's Pier a las 06:45. La salida publicada de la isla es aproximadamente a las 15:20.", "pickup": "Recogida en hotel solicitada; ubicación y precio requieren confirmación.", "pier": "Encuentro en Fishermen's Pier", "valid": "Esta cotización es válida por 60 minutos.", "available": ""},
    "pap": {"title": "Bo oferta pa Klein Curaçao", "quote": "Oferta", "customer": "Bishitante", "date": "Fecha di biahe", "guests": "Bishitantenan", "transport": "Transporte", "charges": "Detaye di preis", "description": "Deskripshon", "qty": "Kant.", "unit": "Unidat", "amount": "Montante", "total": "Total", "included": "Tur kos inkluí", "schedule": "Bo dia", "bring": "Hiba ku bo", "rules": "Reglanan i informashon importante", "payment": "Siguiente paso", "payment_text": "Kompletá bo reservashon ku e link di pago mandá den WhatsApp.", "arrival": "Yega Fishermen's Pier pa 06:45. E salida publiká for di e isla ta mas o ménos 15:20.", "pickup": "E servisio pa buska bo na bo alohamentu ta pidi; lugá i preis mester wòrdu konfirmá.", "pier": "Topa na Fishermen's Pier", "valid": "E oferta aki ta válido pa 60 minüt.", "available": ""},
    "pt": {"title": "Sua cotação para Klein Curaçao", "quote": "Cotação", "customer": "Passageiro", "date": "Data", "guests": "Passageiros", "transport": "Transporte", "charges": "Preço detalhado", "description": "Descrição", "qty": "Qtd.", "unit": "Unidade", "amount": "Valor", "total": "Total", "included": "Tudo incluído", "schedule": "Seu dia", "bring": "O que levar", "rules": "Regras e informações importantes", "payment": "Próximo passo", "payment_text": "Conclua sua reserva pelo link de pagamento enviado no WhatsApp.", "arrival": "Chegue ao Fishermen's Pier às 06:45. A saída publicada da ilha é aproximadamente às 15:20.", "pickup": "Traslado do hotel solicitado; local e preço exigem confirmação.", "pier": "Encontro no Fishermen's Pier", "valid": "Esta cotação é válida por 60 minutos.", "available": ""},
}

DOCUMENT_COPY = {
    "en": {
        "tagline": "Klein Curaçao, good vibes included", "party": "{adults} adults, {children} children 4-12, {infants} children 0-3", "catalog": "Catalog",
        "items": {"adult": "Adult", "child_4_12": "Child age 4-12", "infant_0_3": "Child age 0-3"},
        "included_items": ["Breakfast", "Soft drinks and juices", "BBQ lunch", "Mermaid beach house", "Restrooms and fresh-water shower", "Snorkeling masks", "Beach chairs"],
        "bring_items": ["Towel", "Sunscreen", "Swimwear", "Personal medication", "Hat or cap"],
        "cancellation": "Cancellation policy: https://www.mermaidboattrips.com/Contact/Cancellation-Policy/",
        "safety": "General conditions: https://www.mermaidboattrips.com/Contact/General-Conditions/",
        "insurance": "Privacy statement: https://www.mermaidboattrips.com/Contact/Privacy-Statement/",
        "protocol_title": "Trip protocol", "protocol": "Arrive on time, follow captain and crew instructions, supervise children, use safety equipment as directed, and tell the crew about relevant mobility or dietary requests. Wildlife and exact sea conditions are never guaranteed.",
        "closing": "Bring towels and sunscreen. Mermaid takes care of the rest of your included tropical day.",
        "receipt_title": "Payment receipt", "booking_code": "Booking code", "payment_reference": "Payment reference", "payment_time": "Payment time (UTC)",
        "receipt_disclaimer": "",
        "receipt_arrival": "Arrive at Fishermen's Pier at 06:45. Bring towels and sunscreen; Mermaid takes care of the rest of your included tropical day.",
    },
    "nl": {
    "tagline": "Een dag naar Klein Curaçao",
    "party": "{adults} volwassenen, {children} kinderen 4-12, {infants} kinderen 0-3",
    "catalog": "Catalogus",
    "items": {
        "adult": "Volwassene",
        "child_4_12": "Kind 4-12 jaar",
        "infant_0_3": "Kind 0-3 jaar"
    },
    "included_items": [
        "Ontbijt en barbecuelunch",
        "Frisdranken en vruchtensappen",
        "Snorkels en strandstoelen",
        "Strandhuis met toiletten en een douche met zoet water"
    ],
    "bring_items": [
        "Handdoek",
        "Zonnebrandcrème",
        "Zwemkleding",
        "Eventuele medicijnen",
        "Hoed of pet"
    ],
    "cancellation": "Annuleringsvoorwaarden: https://www.mermaidboattrips.com/Contact/Cancellation-Policy/",
    "safety": "Algemene voorwaarden: https://www.mermaidboattrips.com/Contact/General-Conditions/",
    "insurance": "Privacyverklaring: https://www.mermaidboattrips.com/Contact/Privacy-Statement/",
    "protocol_title": "Goed om te weten",
    "protocol": "Volg de aanwijzingen van de kapitein en bemanning en houd toezicht op kinderen. Geef dieetwensen of benodigde begeleiding vooraf door. De overtocht hangt af van de omstandigheden op zee; het zien van zeeschildpadden is niet gegarandeerd. Bier en wijn zijn tegen betaling verkrijgbaar. Zwemvinnen zijn niet inbegrepen.",
    "closing": "We kijken ernaar uit u aan boord te verwelkomen.",
    "receipt_title": "Betalingsbewijs",
    "booking_code": "Boekingscode",
    "payment_reference": "Betalingsreferentie",
    "payment_time": "Betaald op (UTC)",
    "receipt_disclaimer": "",
    "receipt_arrival": "We verwachten u om 06:45 bij Fishermen's Pier. Neem zwemkleding, een handdoek en zonnebrandcrème mee.",
    "items_plural": {
        "adult": "Volwassenen",
        "child_4_12": "Kinderen 4-12 jaar",
        "infant_0_3": "Kinderen 0-3 jaar"
    }
},
    "de": {
        "tagline": "Klein Curaçao, gute Stimmung inklusive", "party": "{adults} Erwachsene, {children} Kinder 4-12, {infants} Kinder 0-3", "catalog": "Katalog",
        "items": {"adult": "Erwachsene", "child_4_12": "Kind 4-12 Jahre", "infant_0_3": "Kind 0-3 Jahre"},
        "included_items": ["Frühstück", "Alkoholfreie Getränke und Säfte", "BBQ-Mittagessen", "Mermaid-Strandhaus", "Toiletten und Süßwasserdusche", "Schnorchelmasken", "Strandstühle"],
        "bring_items": ["Handtuch", "Sonnencreme", "Badesachen", "Persönliche Medikamente", "Hut oder Kappe"],
        "cancellation": "Stornierungsbedingungen: https://www.mermaidboattrips.com/Contact/Cancellation-Policy/",
        "safety": "Allgemeine Bedingungen: https://www.mermaidboattrips.com/Contact/General-Conditions/",
        "insurance": "Datenschutzerklärung: https://www.mermaidboattrips.com/Contact/Privacy-Statement/",
        "protocol_title": "Ausflugsprotokoll", "protocol": "Kommen Sie pünktlich, befolgen Sie die Anweisungen von Kapitän und Crew, beaufsichtigen Sie Kinder, nutzen Sie Sicherheitsausrüstung wie angewiesen und melden Sie relevante Mobilitäts- oder Ernährungswünsche. Tiere und genaue Seebedingungen werden nie garantiert.",
        "closing": "Bringen Sie Handtücher und Sonnencreme mit. Mermaid kümmert sich um den Rest Ihres inkludierten Tropentags.",
        "receipt_title": "Zahlungsbeleg", "booking_code": "Buchungscode", "payment_reference": "Zahlungsreferenz", "payment_time": "Zahlungszeit (UTC)",
        "receipt_disclaimer": "",
        "receipt_arrival": "Seien Sie um 06:45 am Fishermen's Pier. Bringen Sie Handtücher und Sonnencreme mit; Mermaid kümmert sich um den Rest.",
    },
    "es": {
        "tagline": "Klein Curaçao, buenas vibras incluidas", "party": "{adults} adultos, {children} niños de 4-12, {infants} niños de 0-3", "catalog": "Catálogo",
        "items": {"adult": "Adulto", "child_4_12": "Niño de 4-12 años", "infant_0_3": "Niño de 0-3 años"},
        "included_items": ["Desayuno", "Refrescos y jugos", "Almuerzo BBQ", "Casa de playa Mermaid", "Baños y ducha de agua dulce", "Máscaras de snorkel", "Sillas de playa"],
        "bring_items": ["Toalla", "Protector solar", "Traje de baño", "Medicamentos personales", "Sombrero o gorra"],
        "cancellation": "Política de cancelación: https://www.mermaidboattrips.com/Contact/Cancellation-Policy/",
        "safety": "Condiciones generales: https://www.mermaidboattrips.com/Contact/General-Conditions/",
        "insurance": "Declaración de privacidad: https://www.mermaidboattrips.com/Contact/Privacy-Statement/",
        "protocol_title": "Protocolo del paseo", "protocol": "Llegue a tiempo, siga las instrucciones del capitán y la tripulación, supervise a los niños, use el equipo de seguridad como se indique e informe necesidades de movilidad o alimentación. La fauna y las condiciones exactas del mar nunca están garantizadas.",
        "closing": "Traiga toallas y protector solar. Mermaid se encarga del resto de su día tropical incluido.",
        "receipt_title": "Recibo de pago", "booking_code": "Código de reserva", "payment_reference": "Referencia de pago", "payment_time": "Hora del pago (UTC)",
        "receipt_disclaimer": "",
        "receipt_arrival": "Llegue a Fishermen's Pier a las 06:45. Traiga toallas y protector solar; Mermaid se encarga del resto.",
    },
    "pap": {
        "tagline": "Klein Curaçao, bon ambiente inkluí", "party": "{adults} adulto, {children} mucha di 4-12, {infants} mucha di 0-3", "catalog": "Katalòk",
        "items": {"adult": "Adulto", "child_4_12": "Mucha di 4-12 aña", "infant_0_3": "Mucha di 0-3 aña"},
        "included_items": ["Desayuno", "Refresko i djus", "Almuerso di barbekiú", "Kas di playa di Mermaid", "Baño i ducha di awa dushi", "Máskara di snòrkel", "Stul di playa"],
        "bring_items": ["Toaya", "Krema solar", "Paña di landa", "Remedi personal", "Sombré òf pèchi"],
        "cancellation": "Reglanan di kanselashon: https://www.mermaidboattrips.com/Contact/Cancellation-Policy/",
        "safety": "Kondishonnan general: https://www.mermaidboattrips.com/Contact/General-Conditions/",
        "insurance": "Deklarashon di privasidat: https://www.mermaidboattrips.com/Contact/Privacy-Statement/",
        "protocol_title": "Protokòl di biahe", "protocol": "Yega na tempu, sigui instrukshon di kapitan i tripulashon, tene bista riba muchanan, usa ekiponan di siguridat manera indiká i bisa nos di nesesidat di mobilidat òf dieta. Animalnan i kondishon eksakto di laman nunka ta garantisá.",
        "closing": "Hiba toaya i krema solar. Mermaid ta sòru pa tur loke ta inkluí den bo dia tropikal.",
        "receipt_title": "Resibu di pago", "booking_code": "Kódigo di reservashon", "payment_reference": "Referensia di pago", "payment_time": "Ora di pago (UTC)",
        "receipt_disclaimer": "",
        "receipt_arrival": "Yega Fishermen's Pier pa 06:45. Hiba toaya i krema solar; Mermaid ta sòru pa tur loke ta inkluí.",
    },
    "pt": {
        "tagline": "Klein Curaçao, boas vibrações incluídas", "party": "{adults} adultos, {children} crianças de 4-12, {infants} crianças de 0-3", "catalog": "Catálogo",
        "items": {"adult": "Adulto", "child_4_12": "Criança de 4-12 anos", "infant_0_3": "Criança de 0-3 anos"},
        "included_items": ["Café da manhã", "Refrigerantes e sucos", "Almoço BBQ", "Casa de praia Mermaid", "Banheiros e ducha de água doce", "Máscaras de snorkel", "Cadeiras de praia"],
        "bring_items": ["Toalha", "Protetor solar", "Roupa de banho", "Medicamentos pessoais", "Chapéu ou boné"],
        "cancellation": "Política de cancelamento: https://www.mermaidboattrips.com/Contact/Cancellation-Policy/",
        "safety": "Condições gerais: https://www.mermaidboattrips.com/Contact/General-Conditions/",
        "insurance": "Declaração de privacidade: https://www.mermaidboattrips.com/Contact/Privacy-Statement/",
        "protocol_title": "Protocolo do passeio", "protocol": "Chegue no horário, siga as instruções do capitão e da tripulação, supervisione as crianças, use os equipamentos de segurança conforme orientado e informe necessidades de mobilidade ou alimentação. A fauna e as condições exatas do mar nunca são garantidas.",
        "closing": "Leve toalhas e protetor solar. A Mermaid cuida do restante do seu dia tropical incluído.",
        "receipt_title": "Recibo de pagamento", "booking_code": "Código da reserva", "payment_reference": "Referência do pagamento", "payment_time": "Hora do pagamento (UTC)",
        "receipt_disclaimer": "",
        "receipt_arrival": "Chegue ao Fishermen's Pier às 06:45. Leve toalhas e protetor solar; a Mermaid cuida do restante.",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: object, limit: int = 500) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    return html.escape(" ".join(text.split())[:limit])


def _safe_lines(value: object) -> str:
    return '<br/>'.join(_safe(line) for line in str(value or '')[:500].split('\n'))


def _root() -> Path:
    return Path(os.environ.get("MERMAID_DOCUMENT_ROOT", "/app/data/mermaid-documents")).resolve()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(state_registry.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mermaid_documents (
            public_id TEXT PRIMARY KEY,
            tenant_slug TEXT NOT NULL CHECK (tenant_slug='mermaid'),
            reservation_public_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('quote','receipt')),
            locale TEXT NOT NULL,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            content_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (tenant_slug, reservation_public_id, kind)
        );
        CREATE TABLE IF NOT EXISTS mermaid_delivery_jobs (
            public_id TEXT PRIMARY KEY,
            tenant_slug TEXT NOT NULL CHECK (tenant_slug='mermaid'),
            reservation_public_id TEXT NOT NULL,
            document_public_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    _ensure_document_versions(conn)
    return conn


def _ensure_document_versions(conn):
    if "document_revision" in {r[1] for r in conn.execute("PRAGMA table_info(mermaid_documents)")}:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if "document_revision" not in {r[1] for r in conn.execute("PRAGMA table_info(mermaid_documents)")}:
            schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='mermaid_documents'").fetchone()[0]
            schema = schema.replace("mermaid_documents", "mermaid_documents_versioned", 1)
            schema = schema.replace("UNIQUE (tenant_slug, reservation_public_id, kind)", "document_revision INTEGER NOT NULL DEFAULT 1, UNIQUE (tenant_slug, reservation_public_id, kind, document_revision)")
            conn.execute(schema)
            columns = ",".join(r[1] for r in conn.execute("PRAGMA table_info(mermaid_documents)"))
            conn.execute(f"INSERT INTO mermaid_documents_versioned ({columns}) SELECT {columns} FROM mermaid_documents")
            conn.execute("DROP TABLE mermaid_documents")
            conn.execute("ALTER TABLE mermaid_documents_versioned RENAME TO mermaid_documents")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _money(currency: str, amount: int) -> str:
    return f"{currency} {amount:,.2f}"


def _price_table(money: dict, locale: str, body: ParagraphStyle, width_mm: float) -> Table:
    labels, copy = LABELS[locale], DOCUMENT_COPY[locale]
    table_structure = Structure("Table")
    header_row = Structure("TR", table_structure)
    header_style = ParagraphStyle("PriceHeader", parent=body, fontName="Helvetica-Bold", textColor=colors.white)
    right_header = ParagraphStyle("PriceHeaderRight", parent=header_style, alignment=TA_RIGHT)
    right = ParagraphStyle("PriceRight", parent=body, alignment=TA_RIGHT)
    right_bold = ParagraphStyle("PriceAmount", parent=right, fontName="Helvetica-Bold")
    rows = [[Paragraph(_safe(labels[key]), header_style if index == 0 else right_header,
                       role="TH", structure=header_row, scope="Column")
             for index, key in enumerate(("description", "qty", "unit", "amount"))]]
    for item in money["items"]:
        if not item["quantity"]:
            continue
        row = Structure("TR", table_structure)
        item_label = guest.pickup_label(money, locale) if item["key"] == "pickup" else copy["items"][item["key"]]
        if item['quantity'] != 1:
            item_label = copy.get('items_plural', {}).get(item['key'], item_label)
        values = (item_label, str(item["quantity"]), _money(money["currency"], item["unit_amount"]),
                  _money(money["currency"], item["line_total"]))
        rows.append([Paragraph(_safe(value), body if index == 0 else right_bold if index == 3 else right,
                               role="TD", structure=row) for index, value in enumerate(values)])
    price_table = Table(rows, colWidths=[n / 180 * width_mm * mm for n in (78, 22, 38, 42)], repeatRows=1)
    price_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#B7DCDD")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return price_table


def render_quote_pdf(reservation: dict, target: Path) -> str:
    """Render a compact quote using only snapshotted monetary values."""
    locale = reservation["language"] if reservation["language"] in LABELS else "en"
    labels = LABELS[locale]
    copy = DOCUMENT_COPY[locale]
    notices = DOCUMENT_NOTICES[locale]
    intake = reservation["intake"]
    money = reservation["monetary_snapshot"]
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    styles = getSampleStyleSheet()
    body = ParagraphStyle("QuoteBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=11.5, textColor=DEEP, spaceBefore=0, spaceAfter=0)
    small = ParagraphStyle("QuoteSmall", parent=body, fontSize=8, leading=10, textColor=MUTED)
    brand = ParagraphStyle("QuoteBrand", parent=body, fontName="Helvetica-Bold", fontSize=12, leading=15)
    heading = ParagraphStyle("QuoteTitle", parent=brand, fontSize=15, leading=18, spaceAfter=3 * mm)
    section = ParagraphStyle("QuoteSection", parent=body, fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=TEAL, spaceBefore=2 * mm, spaceAfter=1.5 * mm, keepWithNext=True)
    marker = ParagraphStyle("QuoteMarker", parent=small, fontName="Helvetica-Bold", textColor=MARKER_TEXT, alignment=TA_CENTER)
    total = ParagraphStyle("QuoteTotal", parent=body, fontName="Helvetica-Bold", fontSize=15, leading=18, alignment=TA_RIGHT)
    doc = SimpleDocTemplate(
        str(target), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"Mermaid - {labels['title']} - {reservation['public_id'][-10:].upper()}", author="Mermaid Boat Trips Curaçao",
    )
    header = Table([[
        Paragraph("MERMAID BOAT TRIPS · CURAÇAO", brand),
        Paragraph(_safe(copy["tagline"]), small),
    ]], colWidths=[105 * mm, 75 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story = [_hero(180, 34), Spacer(1, 2 * mm), header, Spacer(1, 2 * mm),
        Table([[Paragraph(_safe(notices["quote_banner"]), marker)]], colWidths=[180 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CORAL),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])), Spacer(1, 3 * mm), Paragraph(_safe(labels["title"]), heading),
    ]

    def detail(label, value):
        return Paragraph(f"<b>{_safe(label)}</b><br/>{_safe(value)}", body)

    customer_detail = detail(labels["customer"], reservation["customer_name"])
    if intake.get("contact_phone"):
        customer_detail = Paragraph(
            f"<b>{_safe(labels['customer'])}</b><br/>{_safe(reservation['customer_name'])}"
            f"<br/><b>{_safe(guest.guest_copy(locale)['contact_phone_label'])}:</b> {_safe(intake['contact_phone'])}", body)
    detail_rows = [
        [customer_detail, detail(labels["quote"], reservation["public_id"][-10:].upper())],
        [detail(labels["date"], guest.guest_date(intake["trip_date"], locale)), detail(labels["guests"], guest.party_text(intake, locale))],
    ]
    details = Table(detail_rows, colWidths=[90 * mm, 90 * mm])
    details.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.3, colors.HexColor("#B7DCDD")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    transport = guest.transport_text(intake, locale, money)
    departure = guest.guest_copy(locale)["island_departure"].format(time=mermaid_catalog.get_catalog()["service"]["island_departure_time"])
    story.extend([details, Paragraph(labels["transport"], section),
                  Paragraph(_safe_lines(transport), body), Paragraph(_safe(departure), body),
                  Paragraph(labels["charges"], section)])
    price_table = _price_table(money, locale, body, 180)
    story.extend([price_table, Spacer(1, 2 * mm),
                  Paragraph(_safe(guest.price_text(money, intake, locale)), total),
                  Spacer(1, 1.5 * mm), Paragraph(_safe(labels["available"]), small)])
    lists = Table([[
        [Paragraph(_safe(labels["included"]), section), Paragraph(" · ".join(_safe(x, 120) for x in copy["included_items"]), body)],
        [Paragraph(_safe(labels["bring"]), section), Paragraph(" · ".join(_safe(x, 100) for x in copy["bring_items"]), body)],
    ]], colWidths=[108 * mm, 72 * mm])
    lists.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 12), ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.extend([lists, Paragraph(labels["rules"], section)])
    for key in ("cancellation", "safety", "insurance"):
        story.extend([Paragraph(_policy_link(copy[key]), small), Spacer(1, 1.5 * mm)])
    story.extend([
        Paragraph(f"<b>{_safe(copy['protocol_title'])}:</b> {_safe(copy['protocol'])}", small),
        Paragraph(labels["payment"], section), Paragraph(_safe(labels["payment_text"]), body),
        Paragraph(_safe(labels["valid"]), small), Spacer(1, 3 * mm),
        HRFlowable(color=CORAL, thickness=0.8), Spacer(1, 2 * mm), Paragraph(_safe(copy["closing"]), small),
    ])
    doc.build(story, canvasmaker=canvas_for(DOCUMENT_LANGUAGES[locale]))
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _doc_id(reservation_id: str, kind: str) -> str:
    return "mdoc_" + hashlib.sha256(f"{reservation_id}:{kind}".encode()).hexdigest()[:24]


def _document(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def create_quote(reservation: dict) -> tuple[dict, dict]:
    """Create one stable quote and one pending idempotent delivery job."""
    public_id = _doc_id(reservation["public_id"], "quote")
    filename = f"Mermaid - Trip Quote - {reservation['public_id'][-10:].upper()}.pdf"
    target = _root() / reservation["public_id"] / filename
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT * FROM mermaid_documents WHERE tenant_slug='mermaid' AND reservation_public_id=? AND kind='quote'",
            (reservation["public_id"],),
        ).fetchone()
        if existing is None:
            digest = render_quote_pdf(reservation, target)
            now = _now()
            conn.execute(
                "INSERT INTO mermaid_documents (public_id, tenant_slug, reservation_public_id, kind, locale, "
                "filename, path, sha256, content_type, created_at) VALUES (?, 'mermaid', ?, 'quote', ?, ?, ?, ?, 'application/pdf', ?)",
                (public_id, reservation["public_id"], reservation["language"], filename, str(target), digest, now),
            )
            conn.commit()
            existing = conn.execute("SELECT * FROM mermaid_documents WHERE public_id=?", (public_id,)).fetchone()
        now = _now()
        job_id = "mjob_" + hashlib.sha256(f"quote:{reservation['public_id']}".encode()).hexdigest()[:24]
        conn.execute(
            "INSERT OR IGNORE INTO mermaid_delivery_jobs (public_id, tenant_slug, reservation_public_id, "
            "document_public_id, conversation_id, kind, status, idempotency_key, created_at, updated_at) "
            "VALUES (?, 'mermaid', ?, ?, ?, 'quote', 'pending', ?, ?, ?)",
            (job_id, reservation["public_id"], public_id, reservation["conversation_id"],
             f"mermaid-quote:{reservation['public_id']}", now, now),
        )
        conn.commit()
        job = conn.execute("SELECT * FROM mermaid_delivery_jobs WHERE public_id=?", (job_id,)).fetchone()
        return _document(existing), dict(job)
    finally:
        conn.close()


def render_receipt_pdf(reservation: dict, payment: dict, target: Path) -> str:
    """Render a compact simulated receipt from persisted booking/payment facts."""
    locale = reservation["language"] if reservation["language"] in LABELS else "en"
    labels, copy, notices = LABELS[locale], DOCUMENT_COPY[locale], DOCUMENT_NOTICES[locale]
    intake, money = reservation["intake"], reservation["monetary_snapshot"]
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("ReceiptBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=DEEP)
    brand = ParagraphStyle("ReceiptBrand", parent=body, fontName="Helvetica-Bold", fontSize=12, leading=15)
    heading = ParagraphStyle("ReceiptHeading", parent=brand, fontSize=20, leading=24)
    section = ParagraphStyle("QuoteSection", parent=body, fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=TEAL, keepWithNext=True)
    marker = ParagraphStyle("ReceiptMarker", parent=body, fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=MARKER_TEXT, alignment=TA_CENTER)
    total = ParagraphStyle("ReceiptTotal", parent=body, fontName="Helvetica-Bold", fontSize=16, leading=20, alignment=TA_RIGHT)
    doc = SimpleDocTemplate(
        str(target), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Mermaid - {copy['receipt_title']} - {guest.display_reference(reservation['booking_code'])}",
        author="Mermaid Boat Trips Curaçao",
    )
    header = Table([[
        Paragraph("MERMAID BOAT TRIPS · CURAÇAO", brand),
        Paragraph(_safe(notices["receipt_subtitle"]), body),
    ]], colWidths=[104 * mm, 70 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    # Numeric UTC timestamp is unambiguous in every supported language; no English month names.
    paid_at = datetime.fromisoformat(payment["paid_at"])
    if paid_at.tzinfo is None:
        paid_at = paid_at.replace(tzinfo=timezone.utc)
    rows = [
        [copy["booking_code"], guest.display_reference(reservation["booking_code"])],
        [copy["payment_reference"], guest.display_reference(payment["payment_reference"])],
        [labels["customer"], reservation["customer_name"]],
        [labels["date"], guest.guest_date(intake["trip_date"], locale)],
        [labels["guests"], guest.party_text(intake, locale)],
        [copy["payment_time"], paid_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
        [labels["transport"], guest.transport_text(intake, locale, money)],
    ]
    details_structure = Structure("Table")
    detail_rows = []
    for label, value in rows:
        row = Structure("TR", details_structure)
        detail_rows.append([
            Paragraph(f"<b>{_safe(label)}</b>", body, role="TH", structure=row, scope="Row"),
            Paragraph(_safe_lines(value) if label == labels['transport'] else _safe(value), body, role="TD", structure=row),
        ])
    table = Table(detail_rows, colWidths=[52 * mm, 122 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B7DCDD")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story = [
        _hero(174, 48 if locale == "nl" else None), Spacer(1, 3 * mm), header, Spacer(1, 4 * mm),
        Table([[Paragraph(_safe(notices["receipt_banner"]), marker)]], colWidths=[174 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CORAL),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])),
        Spacer(1, 5 * mm), Paragraph(_safe(copy["receipt_title"]), heading), Spacer(1, 3 * mm), table,
        Spacer(1, 4 * mm), Paragraph(_safe(labels["charges"]), section), Spacer(1, 2 * mm),
        _price_table(money, locale, body, 174), Spacer(1, 4 * mm),
        Paragraph(_safe(guest.price_text({**money, "currency": payment["currency"], "total": payment["amount"]}, intake, locale)), total),
        Spacer(1, 4 * mm), HRFlowable(color=TEAL, thickness=1.3), Spacer(1, 4 * mm),
        *[Paragraph(_policy_link(copy[key]), body) for key in ("cancellation", "safety", "insurance")],
    ]
    doc.build(story, canvasmaker=canvas_for(DOCUMENT_LANGUAGES[locale]))
    return hashlib.sha256(target.read_bytes()).hexdigest()


def create_receipt(reservation: dict, payment: dict) -> tuple[dict, dict]:
    """Create one stable receipt and its idempotent delivery job."""
    public_id = _doc_id(reservation["public_id"], "receipt")
    filename = f"Mermaid - Payment Receipt - {guest.display_reference(reservation['booking_code'])}.pdf"
    target = _root() / reservation["public_id"] / filename
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT * FROM mermaid_documents WHERE tenant_slug='mermaid' "
            "AND reservation_public_id=? AND kind='receipt' AND document_revision=1",
            (reservation["public_id"],),
        ).fetchone()
        if existing is None:
            digest = render_receipt_pdf(reservation, payment, target)
            now = _now()
            conn.execute(
                "INSERT INTO mermaid_documents (public_id, tenant_slug, reservation_public_id, kind, locale, "
                "filename, path, sha256, content_type, created_at) VALUES (?, 'mermaid', ?, 'receipt', ?, ?, ?, ?, 'application/pdf', ?)",
                (public_id, reservation["public_id"], reservation["language"], filename, str(target), digest, now),
            )
            conn.commit()
            existing = conn.execute("SELECT * FROM mermaid_documents WHERE public_id=?", (public_id,)).fetchone()
        now = _now()
        job_id = "mjob_" + hashlib.sha256(f"receipt:{reservation['public_id']}".encode()).hexdigest()[:24]
        conn.execute(
            "INSERT OR IGNORE INTO mermaid_delivery_jobs (public_id, tenant_slug, reservation_public_id, "
            "document_public_id, conversation_id, kind, status, idempotency_key, created_at, updated_at) "
            "VALUES (?, 'mermaid', ?, ?, ?, 'receipt', 'pending', ?, ?, ?)",
            (job_id, reservation["public_id"], public_id, reservation["conversation_id"],
             f"mermaid-receipt:{reservation['public_id']}", now, now),
        )
        conn.commit()
        job = conn.execute("SELECT * FROM mermaid_delivery_jobs WHERE public_id=?", (job_id,)).fetchone()
        return _document(existing), dict(job)
    finally:
        conn.close()


def claim_initial_delivery(job_id: str) -> bool:
    """Reserve the initial receipt send before I/O, including concurrent callbacks."""
    conn = _conn()
    try:
        result = conn.execute(
            "UPDATE mermaid_delivery_jobs SET attempts=1, status='pending', updated_at=? "
            "WHERE tenant_slug='mermaid' AND public_id=? AND attempts=0 AND status='pending'",
            (_now(), job_id),
        )
        conn.commit()
        return result.rowcount == 1
    finally:
        conn.close()


def mark_delivery(job_id: str, delivered: bool, error: str = "", *, count_attempt: bool = True) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE mermaid_delivery_jobs SET status=?, attempts=attempts+?, last_error=?, updated_at=? "
            "WHERE tenant_slug='mermaid' AND public_id=? AND status!='delivered'",
            ("delivered" if delivered else "pending", int(count_attempt), str(error or "")[:240], _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def delivery_job(job_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM mermaid_delivery_jobs WHERE tenant_slug='mermaid' AND public_id=?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def documents_for_reservation(reservation_public_id: str) -> list[dict]:
    conn = _conn()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT d.public_id, d.kind, d.locale, d.filename, d.sha256, d.content_type, "
            "d.created_at, j.public_id AS delivery_job_id, j.status AS delivery_status, "
            "j.attempts AS delivery_attempts, j.last_error AS delivery_error "
            "FROM mermaid_documents d LEFT JOIN mermaid_delivery_jobs j "
            "ON j.document_public_id=d.public_id AND j.tenant_slug='mermaid' "
            "WHERE d.tenant_slug='mermaid' AND d.reservation_public_id=? ORDER BY d.created_at",
            (reservation_public_id,),
        ).fetchall()]
    finally:
        conn.close()


def sign_download(public_id: str, expires: int, secret: str) -> str:
    return hmac.new(secret.encode(), f"mermaid:{public_id}:{int(expires)}".encode(), hashlib.sha256).hexdigest()


def build_signed_url(base_url: str, public_id: str, secret: str, now: int | None = None) -> str:
    if not base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")) or not secret:
        raise ValueError("Mermaid signed-download configuration is missing")
    expires = int(time.time() if now is None else now) + 3600
    signature = sign_download(public_id, expires, secret)
    return f"{base_url.rstrip('/')}/api/public/mermaid-document/{public_id}?{urlencode({'expires': expires, 'signature': signature})}"


def verify_download(public_id: str, expires: int, signature: str, secret: str, now: int | None = None) -> bool:
    current = int(time.time() if now is None else now)
    return bool(secret and current <= int(expires) <= current + 3600 and hmac.compare_digest(sign_download(public_id, expires, secret), str(signature or "")))


def document_response(public_id: str, expires: int, signature: str):
    secret = os.environ.get("MERMAID_DEMO_SIGNING_SECRET", "")
    if not verify_download(public_id, expires, signature, secret):
        return Response(status_code=404)
    return stored_document_response(public_id)


def stored_document_response(public_id: str):
    """Serve a verified stored PDF; caller must enforce authorization."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM mermaid_documents WHERE tenant_slug='mermaid' AND public_id=?", (public_id,)).fetchone()
    finally:
        conn.close()
    doc = _document(row)
    if not doc:
        return Response(status_code=404)
    path = Path(doc["path"]).resolve()
    try:
        path.relative_to(_root())
    except ValueError:
        return Response(status_code=404)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != doc["sha256"]:
        return Response(status_code=404)
    return FileResponse(
        str(path), media_type="application/pdf", filename=doc["filename"],
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


def quote_message(reservation: dict) -> str:
    from agents.social import mermaid_document_cards as cards
    if cards.enabled():
        return cards.quote_text(reservation)
    locale = reservation["language"]
    return "\n\n".join([
        guest.guest_copy(locale)["quote_ready"],
        guest.price_text(reservation["monetary_snapshot"], reservation["intake"], locale),
        guest.transport_text(reservation["intake"], locale, reservation["monetary_snapshot"]),
    ])


def _policy_link(value):
    label, url = value.split(": https://", 1)
    return f'<link href="https://{html.escape(url, quote=True)}" color="#007F86">{html.escape(label)}</link>'
