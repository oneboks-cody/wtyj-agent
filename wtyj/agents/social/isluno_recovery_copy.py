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
