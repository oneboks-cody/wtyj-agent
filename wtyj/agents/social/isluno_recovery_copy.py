"""Explicit reminder opt-out acknowledgement, not a booking-status claim."""
STOP={
'en':'Reminders are stopped.',
'nl':'Herinneringen zijn gestopt.',
'de':'Erinnerungen sind gestoppt.',
'es':'Los recordatorios están desactivados.',
'pt':'Os lembretes foram desativados.',
'pap':'Recordatorionan a wordu para.'}


# Explicit model/API-failure exception: no business facts or booking promises.
PROCESSING_FAILED = {
    'en': "I couldn’t process your message. Please send a new message to try again.",
    'nl': 'Ik kon je bericht niet verwerken. Stuur een nieuw bericht om het opnieuw te proberen.',
    'de': 'Ich konnte deine Nachricht nicht verarbeiten. Bitte sende eine neue Nachricht, um es erneut zu versuchen.',
    'es': 'No pude procesar tu mensaje. Envía un nuevo mensaje para volver a intentarlo.',
    'pt': 'Não consegui processar a tua mensagem. Envia uma nova mensagem para tentar novamente.',
    'pap': 'Mi no por a prosesá bo mensahe. Por fabor manda un mensahe nobo pa purba atrobe.',
}


# An action may have committed before presentation failed. Never assert that it
# did not happen or invite a blind booking/payment replay.
RESPONSE_FAILED = {
    'en': "I couldn’t finish this reply. Please ask me to check your itinerary before repeating a booking or payment action.",
    'nl': 'Ik kon dit antwoord niet afronden. Vraag me je reisplan te controleren voordat je een boeking of betaling herhaalt.',
    'de': 'Ich konnte diese Antwort nicht abschließen. Bitte lass mich deinen Reiseplan prüfen, bevor du eine Buchung oder Zahlung wiederholst.',
    'es': 'No pude terminar esta respuesta. Pídeme que revise tu itinerario antes de repetir una reserva o un pago.',
    'pt': 'Não consegui terminar esta resposta. Peça-me para verificar o itinerário antes de repetir uma reserva ou um pagamento.',
    'pap': 'Mi no por a kaba e kontesta aki. Puntra mi pa kontrolá bo itinerario promé ku ripití un reservashon òf pago.',
}

# Verified stale/expired control exception: no state change or booking claim.
STALE_CHOICE = {
    'en': 'That earlier choice is no longer current. What would you like to check or change in your itinerary?',
    'nl': 'Die eerdere keuze is niet meer actueel. Wat wil je controleren of wijzigen in je reisplan?',
    'de': 'Diese frühere Auswahl ist nicht mehr aktuell. Was möchtest du in deinem Reiseplan prüfen oder ändern?',
    'es': 'Esa opción anterior ya no está vigente. ¿Qué quieres revisar o cambiar en tu itinerario?',
    'pt': 'Essa opção anterior já não está atualizada. O que quer verificar ou alterar no seu itinerário?',
    'pap': 'E opshon anterior ei no ta aktual mas. Kiko bo ke kontrolá òf kambia den bo itinerario?',
}
