"""Canonical quote projection shared by WhatsApp, PDF and operator reads.

Only immutable application snapshots supply business facts; no LLM or network.
"""
from io import BytesIO
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
import threading

# Human translation review is a release task; identifiers/names remain source text.
COPY = {
 'en': ['Itinerary summary', 'Demo quote', 'Guest', 'Guests / ages', 'Pickup / meeting point', 'Departure', 'Check-in', 'Item', 'Quantity', 'Unit price', 'Amount', 'Total', 'Version', 'Confirm details', 'Approve quote', 'Details confirmed. Review the quote before approving.', 'Quote approved. No payment or real reservation has been made.', 'DEMO - No real reservation or payment. Availability is assumed.', 'DEMO SAMPLE RULES - Not supplier-confirmed.', 'Source', 'Document language', 'Correct any details before continuing.', 'Approval requires the current reply button. Questions do not approve a quote.', 'Page'],
 'nl': ['Overzicht reisplan', 'Demo-offerte', 'Gast', 'Gasten / leeftijden', 'Ophalen / ontmoetingspunt', 'Vertrek', 'Inchecken', 'Onderdeel', 'Aantal', 'Prijs per eenheid', 'Bedrag', 'Totaal', 'Versie', 'Gegevens bevestigen', 'Offerte goedkeuren', 'Gegevens bevestigd. Controleer de offerte voordat je deze goedkeurt.', 'Offerte goedgekeurd. Er is niets betaald of echt gereserveerd.', 'DEMO - Geen echte reservering of betaling. Beschikbaarheid wordt aangenomen.', 'DEMO-VOORBEELDREGELS - Niet door de aanbieder bevestigd.', 'Bron', 'Documenttaal', 'Corrigeer de gegevens voordat je doorgaat.', 'Goedkeuring vereist de huidige antwoordknop. Vragen keuren geen offerte goed.', 'Pagina'],
 'de': ['Reiseplanübersicht', 'Demo-Angebot', 'Gast', 'Gäste / Alter', 'Abholung / Treffpunkt', 'Abfahrt', 'Check-in', 'Position', 'Anzahl', 'Einzelpreis', 'Betrag', 'Gesamt', 'Version', 'Angaben bestätigen', 'Angebot annehmen', 'Angaben bestätigt. Prüfe das Angebot vor der Annahme.', 'Angebot angenommen. Keine Zahlung oder echte Buchung erfolgt.', 'DEMO - Keine echte Buchung oder Zahlung. Verfügbarkeit wird angenommen.', 'DEMO-BEISPIELREGELN - Nicht vom Anbieter bestätigt.', 'Quelle', 'Dokumentsprache', 'Korrigiere die Angaben, bevor du fortfährst.', 'Die aktuelle Antwortschaltfläche ist zur Annahme erforderlich. Fragen sind keine Annahme.', 'Seite'],
 'es': ['Resumen del itinerario', 'Presupuesto demo', 'Huésped', 'Huéspedes / edades', 'Recogida / punto de encuentro', 'Salida', 'Registro', 'Concepto', 'Cantidad', 'Precio unitario', 'Importe', 'Total', 'Versión', 'Confirmar datos', 'Aprobar presupuesto', 'Datos confirmados. Revisa el presupuesto antes de aprobarlo.', 'Presupuesto aprobado. No se realizó ningún pago ni reserva real.', 'DEMO - Sin reserva ni pago real. Se supone disponibilidad.', 'REGLAS DE EJEMPLO DEMO - Sin confirmación del proveedor.', 'Fuente', 'Idioma del documento', 'Corrige los datos antes de continuar.', 'Se requiere el botón de respuesta actual para aprobar. Las preguntas no aprueban un presupuesto.', 'Página'],
 'pt': ['Resumo do itinerário', 'Orçamento demo', 'Hóspede', 'Hóspedes / idades', 'Recolha / ponto de encontro', 'Partida', 'Check-in', 'Item', 'Quantidade', 'Preço unitário', 'Valor', 'Total', 'Versão', 'Confirmar dados', 'Aprovar orçamento', 'Dados confirmados. Reveja o orçamento antes de aprovar.', 'Orçamento aprovado. Sem pagamento ou reserva real.', 'DEMO - Sem reserva ou pagamento real. Disponibilidade presumida.', 'REGRAS DE EXEMPLO DEMO - Sem confirmação do fornecedor.', 'Fonte', 'Idioma do documento', 'Corrija os dados antes de continuar.', 'A aprovação requer o botão de resposta atual. Perguntas não aprovam um orçamento.', 'Página'],
 'pap': ['Resumen di itinerario', 'Oferta demo', 'Huéspet', 'Huéspetnan / edatnan', 'Rekohida / punto di enkuentro', 'Salida', 'Check-in', 'Parti', 'Kantidat', 'Prijs pa unidat', 'Monto', 'Total', 'Vershon', 'Konfirmá datonan', 'Aprobá oferta', 'Datonan konfirmá. Kontrolá e oferta promé ku aprobá.', 'Oferta aprobá. No a hasi pago ni reservashon real.', 'DEMO - Sin reservashon ni pago real. Disponibilidat ta asumí.', 'REGLANAN DI EHEMPEL DEMO - No ta konfirmá pa proveedor.', 'Fuente', 'Idioma di dokumento', 'Koregí e datonan promé ku sigui.', 'Aprobashon ta rekerí e boton di kontesta aktual. Preguntanan no ta aprobá un oferta.', 'Página'],
}


REVIEW_READY = {
 'en': 'Your updated quote is ready. Ask to review the itinerary to continue.',
 'nl': 'Je bijgewerkte offerte is klaar. Vraag om het reisplan te bekijken om verder te gaan.',
 'de': 'Dein aktualisiertes Angebot ist bereit. Bitte um die Reiseplanübersicht, um fortzufahren.',
 'es': 'Tu presupuesto actualizado está listo. Pide revisar el itinerario para continuar.',
 'pt': 'O orçamento atualizado está pronto. Peça para rever o itinerário para continuar.',
 'pap': 'Bo oferta aktualisá ta kla. Pidi pa kontrolá e itinerario pa sigui.',
}


def money(amount, currency, exponent):
    scale = 10 ** exponent
    return currency + ' ' + str(amount // scale) + (('.' + str(amount % scale).zfill(exponent)) if exponent else '')


def projection(snapshot):
    itinerary = snapshot['itinerary']
    from shared.isluno_pricing import check
    check(sum(i['total_minor'] for i in itinerary['items']) == itinerary['totals']['total_minor'], 'quote_total_mismatch')
    check(all(sum(line['amount_minor'] for line in i['lines']) == i['total_minor'] and
              all(line['quantity'] * line['unit_minor'] == line['amount_minor'] for line in i['lines']) and
              i['currency'] == itinerary['totals']['currency'] and i['currency_exponent'] == itinerary['totals']['currency_exponent']
              for i in itinerary['items']), 'quote_line_mismatch')
    result = {'quote_id': snapshot['id'], 'version': snapshot['version'],
              'itinerary_id': itinerary['id'], 'itinerary_revision': itinerary['revision'],
              'document_language': snapshot['document_language'], 'guest': snapshot['guest'],
              'brand': itinerary['brand_snapshot'], 'totals': itinerary['totals'], 'demo_only': True, 'items': []}
    for item in itinerary['items']:
        rules = item['pricing_snapshot']['rules']
        detail = snapshot['item_details'].get(item['id'], {})
        result['items'].append({
            'id': item['id'], 'name': item['product']['name'], 'guest_name': detail.get('guest_name') or snapshot['guest']['name'],
            'guest_ages': item['selection']['guest_ages'], 'date': item['selection']['date'],
            'starts_at': item['starts_at'], 'check_in_at': item['check_in_at'], 'timezone': item['timezone'],
            'pickup': item['selection']['pickup'],
            'pickup_location': detail.get('pickup_location') if item['selection']['pickup'] else rules['pickup'].get('meeting_point'),
            'lines': item['lines'], 'total_minor': item['total_minor'], 'currency': item['currency'],
            'currency_exponent': item['currency_exponent'], 'source': item['product']['source'],
            'pricing_mode': item['pricing_snapshot']['pricing_mode'], 'rule_label': item['pricing_snapshot']['label'],
            'catalog_revision': item['catalog_revision'], 'rules': rules,
        })
    return result


def departure(value):
    return datetime.fromisoformat(value).strftime('%Y-%m-%d %H:%M')


def line_label(item, line, words):
    if line['kind'] == 'base':
        band = next((b for b in item['rules']['price_rules'].get('age_bands', []) if b['id'] == line['key']), None)
        if band:
            return words[3].split('/')[-1].strip() + f" {band['minimum_age']}-{band['maximum_age']}"
        return item['name']
    option = next((o for o in item['rules']['options'] if o['id'] == line['key']), None)
    return option.get('name', line['key']) if option else line['key']


def text_sections(snapshot, language=None):
    data = projection(snapshot)
    w = COPY[language or data['document_language']]
    sections = [f"Isluno | {w[0]} | {w[12]} {data['version']}\n{w[17]}\n{w[2]}: {data['guest']['name']}\n{w[20]}: {data['document_language']}"]
    for index, item in enumerate(data['items'], 1):
        unit = lambda amount: money(amount, item['currency'], item['currency_exponent'])
        rows = [f"{index}. {item['name']}", f"{w[2]}: {item['guest_name']}",
                f"{w[3]}: {', '.join(map(str, item['guest_ages']))}",
                f"{w[5]}: {departure(item['starts_at'])} ({item['timezone']})", f"{w[6]}: {departure(item['check_in_at'])}",
                f"{w[4]}: {item['pickup_location'] or '-'}"]
        rows += [f"{line_label(item, line, w)}: {line['quantity']} x {unit(line['unit_minor'])} = {unit(line['amount_minor'])}" for line in item['lines']]
        rows += [f"{w[11]}: {unit(item['total_minor'])}", item['rule_label']]
        if item['pricing_mode'] == 'demo_sample': rows.append(w[18])
        sections.append('\n'.join(rows))
    total = data['totals']
    sections.append(f"{w[11]}: {money(total['total_minor'], total['currency'], total['currency_exponent'])}\n{w[17]}\n{w[21]}")
    return sections


_FONT_LOCK = threading.Lock()


def render_pdf(snapshot, *, kind="quote", reference=None, item_id=None):
    """Wrap all cells/long names, repeat table headings, and mark every page DEMO."""
    import copy
    if kind != 'quote':
        from agents.social.isluno_payment_copy import COPY as PAID_COPY
        snapshot = copy.deepcopy(snapshot)
        if kind == 'ticket':
            snapshot['itinerary']['items'] = [i for i in snapshot['itinerary']['items'] if i['id'] == item_id]
            if len(snapshot['itinerary']['items']) != 1:
                raise ValueError('ticket_item_missing')
            snapshot['itinerary']['totals']['total_minor'] = snapshot['itinerary']['items'][0]['total_minor']
            snapshot['guest']['name'] = snapshot['item_details'].get(item_id, {}).get('guest_name') or snapshot['guest']['name']
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    from reportlab.lib.pagesizes import A4
    with _FONT_LOCK:
        if 'IslunoVera' not in pdfmetrics.getRegisteredFontNames():
            root = Path(reportlab.__file__).parent / 'fonts'
            pdfmetrics.registerFont(TTFont('IslunoVera', str(root / 'Vera.ttf')))
            pdfmetrics.registerFont(TTFont('IslunoVeraBold', str(root / 'VeraBd.ttf')))
    data = projection(snapshot)
    w = list(COPY[data['document_language']])
    if kind != 'quote':
        paid_words = PAID_COPY[data['document_language']]
        w[1], w[17] = paid_words[1 if kind == 'ticket' else 0], paid_words[2]
    normal = ParagraphStyle('isluno', fontName='IslunoVera', fontSize=9, leading=13, spaceAfter=5, wordWrap='CJK')
    heading = ParagraphStyle('isluno-heading', parent=normal, fontName='IslunoVeraBold', fontSize=13, leading=18, textColor=colors.HexColor('#07535B'), spaceBefore=10)
    small = ParagraphStyle('isluno-small', parent=normal, fontSize=8, leading=11)
    right = ParagraphStyle('isluno-right', parent=small, alignment=TA_RIGHT)
    def p(text, style=normal): return Paragraph(escape(str(text)), style)
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=53, bottomMargin=65, title='Isluno - ' + w[1], author='Isluno', invariant=1)
    story = [p(w[1], heading), p(f"{w[12]} {data['version']} | {data['quote_id']}"), p(w[17]),
             p(f"{w[2]}: {data['guest']['name']}"), p(f"{w[20]}: {data['document_language']}")]
    if reference: story.append(p(reference, small))
    widths = [205, 70, 116, 124]
    for index, item in enumerate(data['items'], 1):
        unit = lambda amount: money(amount, item['currency'], item['currency_exponent'])
        item_story = [p(f"{index}. {item['name']}", heading), p(f"{w[2]}: {item['guest_name']}")]
        if kind != 'quote': item_story.append(p(paid_words[1] + ': ' + snapshot['ticket_ids'][item['id']], small))
        for label, value in [(w[3], ', '.join(map(str, item['guest_ages']))), (w[5], departure(item['starts_at']) + ' (' + item['timezone'] + ')'),
                             (w[6], departure(item['check_in_at'])), (w[4], item['pickup_location'] or '-')]:
            item_story.append(p(label + ': ' + value))
        rows = [[p(w[7], small), p(w[8], small), p(w[9], right), p(w[10], right)]]
        for line in item['lines']:
            rows.append([p(line_label(item, line, w), small), p(line['quantity'], small), p(unit(line['unit_minor']), right), p(unit(line['amount_minor']), right)])
        rows.append([p(w[11], small), p(''), p(''), p(unit(item['total_minor']), right)])
        table = Table(rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E4F2F0')), ('VALIGN',(0,0),(-1,-1),'TOP'),
                                  ('BOTTOMPADDING',(0,0),(-1,-1),7), ('TOPPADDING',(0,0),(-1,-1),7),
                                  ('LINEBELOW',(0,-1),(-1,-1),0.5,colors.HexColor('#07535B'))]))
        item_story += [table, Spacer(1,8), p(item['rule_label'], small)]
        if item['pricing_mode'] == 'demo_sample': item_story.append(p(w[18], small))
        item_story.append(p(w[19] + ': ' + item['source']['url'], small))
        story.append(KeepTogether(item_story))
    total = data['totals']
    story.append(KeepTogether([Spacer(1,12), p(w[11] + ': ' + money(total['total_minor'], total['currency'], total['currency_exponent']), heading), p(w[17])]))
    def page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor('#07535B'))
        canvas.setFont('IslunoVeraBold', 16)
        canvas.drawString(40, A4[1] - 32, 'Isluno')
        canvas.setFont('IslunoVera', 7)
        # Wrapped footer instead of an unbounded drawString for localized notice.
        footer = p(w[17], small)
        _, height = footer.wrap(450, 40)
        footer.drawOn(canvas, 40, 25)
        canvas.drawRightString(A4[0]-40, 17, w[23] + ' ' + str(doc.page))
        canvas.restoreState()
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return output.getvalue()
