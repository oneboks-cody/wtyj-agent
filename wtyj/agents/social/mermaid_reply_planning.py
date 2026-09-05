"""Small conversation decisions shared by model context and protected replies."""

DATE_COPY = {
    "en": ("No problem if you haven't chosen a date yet. Is there a week or month you're considering?", "You can choose the date later.", "Your details are saved. Once you've chosen a date, we can finish the reservation."),
    "nl": ("Geen probleem als u nog geen datum heeft gekozen. Denkt u aan een bepaalde week of maand?", "U kunt de datum later kiezen.", "Uw gegevens zijn opgeslagen. Zodra u een datum heeft gekozen, kunnen we de reservering afronden."),
    "de": ("Kein Problem, wenn Sie noch kein Datum gewählt haben. Haben Sie eine bestimmte Woche oder einen Monat im Sinn?", "Sie können das Datum später wählen.", "Ihre Angaben sind gespeichert. Sobald Sie ein Datum gewählt haben, können wir die Reservierung abschließen."),
    "es": ("No hay problema si todavía no han elegido una fecha. ¿Tienen alguna semana o mes en mente?", "Pueden elegir la fecha más adelante.", "Sus datos están guardados. Cuando elijan una fecha, podremos completar la reserva."),
    "pap": ("No tin problema si boso no a skohe un fecha ainda. Den kua siman òf luna boso ta pensa di bai?", "Boso por skohe e fecha despues.", "Mi a registrá boso datonan. Ora boso a skohe un fecha, nos por kompletá e reservashon."),
    "pt": ("Sem problema se ainda não escolheram uma data. Têm alguma semana ou mês em mente?", "Podem escolher a data mais tarde.", "Os seus dados estão guardados. Quando escolherem uma data, poderemos concluir a reserva."),
}

PARTY_COPY = {
    "en": "I've noted your group: {party}.",
    "nl": "Ik heb uw gezelschap genoteerd: {party}.",
    "de": "Ich habe Ihre Gruppe notiert: {party}.",
    "es": "He anotado su grupo: {party}.",
    "pap": "Mi a registrá boso grupo: {party}.",
    "pt": "Registei o seu grupo: {party}.",
}


def supported_faq(understood, text):
    """Only an independently evidenced ordinary question supplies a FAQ body."""
    excerpt = understood.get('other_question_excerpt')
    if (understood.get('other_question_topic') in {'food', 'inclusions', 'activities', 'preparation', 'pier', 'parking', 'travel_time', 'contact'}
            and isinstance(excerpt, str) and excerpt.strip() and excerpt in text):
        return str(understood.get('other_question_reply') or '').strip()
    return ''


def date_request(understood, text):
    """Uncertainty must be evidenced in this guest turn, never invented."""
    request = understood.get("date_request")
    excerpt = understood.get("date_request_excerpt")
    if isinstance(request, str) and request in {"undecided", "defer"} and isinstance(excerpt, str) and excerpt.strip() and excerpt in text:
        return request
    return None


def join_answers(*parts):
    """Each independently answered concern appears once."""
    result = []
    for part in parts:
        value = str(part or "").strip()
        if value and not any(value in existing for existing in result):
            result = [existing for existing in result if existing not in value]
            result.append(value)
    return "\n\n".join(result)
