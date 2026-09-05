"""Deterministic WhatsApp-first intake for Mermaid's reservation demo.

The language model is deliberately not trusted with money, availability,
booking codes, or payment state. This module extracts customer-supplied facts,
asks one question at a time, and persists progress in the existing WhatsApp
state store.
"""

from __future__ import annotations

import re
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import dateparser

from shared import bm_logger, mermaid_catalog, state_registry
from shared.mermaid_contact import normalize_contact_phone
from agents.social import mermaid_guest_experience as guest
from agents.social import mermaid_response_policy as response_policy


SUPPORTED_LOCALES = ("en", "nl", "de", "es", "pap", "pt")
REQUIRED_FIELDS = (
    "trip_date", "adults", "children", "infants", "customer_name", "contact_phone", "pickup_preference"
)


def _stopped_loop_result(state: dict, *, suppressed: bool = False) -> IntakeResult:
    """Return a provider-silent result for a terminal Mermaid loop stop."""
    intake = (state.get("fields") or {}).get("mermaid_intake") or {}
    return IntakeResult(
        "",
        intake.get("language", "en"),
        intake.get("phase", "loop_stopped"),
        action="loop_stopped",
        duplicate=suppressed,
        understanding_source="loop_stop",
    )


COPY = {
    "en": {
        "intro": "Hi, I’m TRACY, Mermaid’s virtual reservation assistant. I’ll arrange your trip and prepare the full quote right here in WhatsApp.",
        "trip_date": "What date would you like to visit Klein Curaçao?",
        "adults": "How many adults are traveling?",
        "children": "How many children aged 4 to 12 are traveling? Reply 0 if none.",
        "infants": "How many children aged 0 to 3 are traveling? Reply 0 if none.",
        "name": "What full name should I put on the reservation?",
        "pickup": "Will you meet us at Fishermen’s Pier, or would you like to request hotel pickup?",
        "hotel": "Which hotel or pickup location should I include in the request?",
        "composition": "How many are adults, children aged 4 to 12, and children aged 0 to 3?",
        "confirm": "Please reply *YES* if everything is correct, or tell me exactly what to change.",
        "confirmed": "Perfect, I have your confirmed details. I’m preparing your reservation and quote now.",
        "cancelled": "Your reservation request is cancelled. No payment was taken.",
        "human": "I’ve passed this to Mermaid’s team for review. Your details are saved, and I can still help with general trip questions.",
        "invalid_day": "Mermaid’s published trips run Monday, Tuesday, Wednesday, Friday, Saturday and Sunday. Please choose one of those days.",
    },
    "nl": {
        "intro": "Hoi, ik ben TRACY, Mermaid’s virtuele reserveringsassistent. Ik regel je trip en maak de volledige offerte hier in WhatsApp.",
        "trip_date": "Welke datum wil je Klein Curaçao bezoeken?",
        "adults": "Met hoeveel volwassenen reizen jullie?",
        "children": "Hoeveel kinderen van 4 tot en met 12 jaar reizen mee? Antwoord 0 als er geen zijn.",
        "infants": "Hoeveel kinderen van 0 tot en met 3 jaar reizen mee? Antwoord 0 als er geen zijn.",
        "name": "Welke volledige naam mag ik op de reservering zetten?",
        "pickup": "Komen jullie naar Fishermen’s Pier of willen jullie hoteltransfer aanvragen?",
        "hotel": "Welk hotel of welke ophaallocatie mag ik in de aanvraag zetten?",
        "composition": "Hoeveel volwassenen, kinderen van 4 tot en met 12 en kinderen van 0 tot en met 3 zijn het?",
        "confirm": "Antwoord met *JA* als alles klopt, of zeg precies wat ik moet aanpassen.",
        "confirmed": "Perfect, ik heb je gegevens bevestigd. Ik maak nu je-reservering en offerte.",
        "cancelled": "Je-reserveringsaanvraag is geannuleerd. Er is niets betaald.",
        "human": "Ik heb dit ter beoordeling aan Mermaid’s team doorgegeven. Je gegevens zijn opgeslagen en ik kan algemene vragen over de trip blijven beantwoorden.",
        "invalid_day": "Mermaid vaart volgens de publicatie op maandag, dinsdag, woensdag, vrijdag, zaterdag en zondag. Kies een van die dagen.",
    },
    "de": {
        "intro": "Hallo, ich bin TRACY, Mermaids virtuelle Reservierungsassistentin. Ich organisiere Ihren Ausflug und erstelle das vollständige Angebot hier in WhatsApp.",
        "trip_date": "An welchem Datum möchten Sie Klein Curaçao besuchen?",
        "adults": "Wie viele Erwachsene reisen mit?",
        "children": "Wie viele Kinder von 4 bis 12 Jahren reisen mit? Antworten Sie 0, wenn keine mitreisen.",
        "infants": "Wie viele Kinder von 0 bis 3 Jahren reisen mit? Antworten Sie 0, wenn keine mitreisen.",
        "name": "Auf welchen vollständigen Namen soll ich reservieren?",
        "pickup": "Treffen Sie uns am Fishermen’s Pier oder möchten Sie eine Hotelabholung anfragen?",
        "hotel": "Welches Hotel oder welchen Abholort soll ich in die Anfrage aufnehmen?",
        "composition": "Wie viele Erwachsene, Kinder von 4 bis 12 und Kinder von 0 bis 3 Jahren sind es?",
        "confirm": "Antworten Sie mit *JA*, wenn alles stimmt, oder nennen Sie genau die gewünschte Änderung.",
        "confirmed": "Perfekt, Ihre Angaben sind bestätigt. Ich erstelle jetzt Ihre Demo-Reservierung und Ihr Angebot.",
        "cancelled": "Ihre Demo-Reservierungsanfrage wurde storniert. Es wurde nichts bezahlt.",
        "human": "Ich habe dies zur Prüfung an Mermaids Team weitergegeben. Ihre Angaben sind gespeichert und ich kann weiterhin allgemeine Fragen zum Ausflug beantworten.",
        "invalid_day": "Mermaid fährt laut Veröffentlichung Montag, Dienstag, Mittwoch, Freitag, Samstag und Sonntag. Bitte wählen Sie einen dieser Tage.",
    },
    "es": {
        "intro": "Hola, soy TRACY, la asistente virtual de reservas de Mermaid. Organizaré tu paseo y prepararé la cotización completa aquí en WhatsApp.",
        "trip_date": "¿Qué fecha quieres visitar Klein Curaçao?",
        "adults": "¿Cuántos adultos viajan?",
        "children": "¿Cuántos niños de 4 a 12 años viajan? Responde 0 si no hay.",
        "infants": "¿Cuántos niños de 0 a 3 años viajan? Responde 0 si no hay.",
        "name": "¿Qué nombre completo debo poner en la reserva?",
        "pickup": "¿Se encontrarán con nosotros en Fishermen’s Pier o desean solicitar recogida en el hotel?",
        "hotel": "¿Qué hotel o lugar de recogida debo incluir en la solicitud?",
        "composition": "¿Cuántos son adultos, niños de 4 a 12 y niños de 0 a 3 años?",
        "confirm": "Responde *SÍ* si todo está correcto o dime exactamente qué debo cambiar.",
        "confirmed": "Perfecto, tus datos están confirmados. Ahora preparo tu reserva y cotización.",
        "cancelled": "Tu solicitud de reserva está cancelada. No se realizó ningún pago.",
        "human": "He pasado esto al equipo de Mermaid para que lo revise. Tus datos están guardados y puedo seguir respondiendo preguntas generales sobre la excursión.",
        "invalid_day": "Según la información publicada, Mermaid opera lunes, martes, miércoles, viernes, sábado y domingo. Elige uno de esos días.",
    },
    "pap": {
        "intro": "Bon bini na Mermaid! Mi ta TRACY, asistente virtual di reservashon. Mi ta yuda bo ku bo biahe i prepará bo oferta aki den WhatsApp.",
        "trip_date": "Ki fecha bo ke bishitá Klein Curaçao?",
        "adults": "Kuantu adulto ta bai?",
        "children": "Kuantu mucha di 4 te ku 12 aña ta bai? Kontestá 0 si no tin.",
        "infants": "Kuantu mucha di 0 te ku 3 aña ta bai? Kontestá 0 si no tin.",
        "name": "Ki nòmber kompleto mi por pone riba e reservashon?",
        "pickup": "Bo ta bini Fishermen’s Pier òf bo ke pidi pa nos buska bo na bo alohamentu?",
        "hotel": "Na ki hotèl òf lugá nos mester buska bo?",
        "composition": "Kuantu ta adulto, mucha di 4 te ku 12 i mucha di 0 te ku 3 aña?",
        "confirm": "Kontestá *SÍ* si tur kos ta korekto, òf bisa mi eksaktamente kiko mester kambia.",
        "confirmed": "Perfekto, bo datonan ta konfirmá. Awor mi ta prepará bo reservashon i oferta.",
        "cancelled": "Bo petishon di reservashon ta kanselá. No a tuma ningun pago.",
        "human": "Bo petishon ta warda pa e tim di Mermaid revisá. Bo datonan ta wardá i mi por sigui yuda ku preguntanan general tokante e biahe.",
        "invalid_day": "Segun e informashon publiká, Mermaid ta bai djaluna, djamars, djárason, djabièrnè, djasabra i djadumingu. Skohe un di e dianan ei.",
    },
    "pt": {
        "intro": "Olá, sou a TRACY, assistente virtual de reservas da Mermaid. Vou organizar seu passeio e preparar a cotação completa aqui no WhatsApp.",
        "trip_date": "Em que data você quer visitar Klein Curaçao?",
        "adults": "Quantos adultos vão viajar?",
        "children": "Quantas crianças de 4 a 12 anos vão viajar? Responda 0 se não houver.",
        "infants": "Quantas crianças de 0 a 3 anos vão viajar? Responda 0 se não houver.",
        "name": "Qual nome completo devo colocar na reserva?",
        "pickup": "Vocês irão ao Fishermen’s Pier ou desejam solicitar traslado do hotel?",
        "hotel": "Qual hotel ou local de embarque devo incluir no pedido?",
        "composition": "Quantos são adultos, crianças de 4 a 12 e crianças de 0 a 3 anos?",
        "confirm": "Responda *SIM* se estiver tudo correto ou diga exatamente o que devo alterar.",
        "confirmed": "Perfeito, seus dados estão confirmados. Agora vou preparar sua reserva e cotação.",
        "cancelled": "Seu pedido de reserva foi cancelado. Nenhum pagamento foi realizado.",
        "human": "Encaminhei isso à equipe da Mermaid para análise. Seus dados estão salvos e posso continuar respondendo a perguntas gerais sobre o passeio.",
        "invalid_day": "Segundo as informações publicadas, a Mermaid opera segunda, terça, quarta, sexta, sábado e domingo. Escolha um desses dias.",
    },
}


WELCOME_COPY = {
    "en": "Hi, welcome to Mermaid! I’m TRACY, Mermaid’s virtual reservation assistant.",
    "nl": "Hoi, welkom bij Mermaid! Ik ben TRACY, de virtuele reserveringsassistent van Mermaid.",
    "de": "Hallo, willkommen bei Mermaid! Ich bin TRACY, die virtuelle Reservierungsassistentin von Mermaid.",
    "es": "¡Hola! Te doy la bienvenida a Mermaid. Soy TRACY, la asistente virtual de reservas de Mermaid.",
    "pap": "Bon bini na Mermaid! Mi ta TRACY, asistente virtual di reservashon.",
    "pt": "Olá! Boas-vindas à Mermaid. Sou a TRACY, assistente virtual de reservas da Mermaid.",
}


WHEELCHAIR_COPY = {
    "en": "Yes, no problem. We’re prepared to welcome guests who use wheelchairs. I’ve saved a note for the crew so they can prepare to help.",
    "nl": "Ja, geen probleem. We zijn erop voorbereid om gasten die een rolstoel gebruiken te ontvangen. Ik heb een notitie voor de bemanning vastgelegd, zodat zij zich kunnen voorbereiden om te helpen.",
    "de": "Ja, kein Problem. Wir sind darauf vorbereitet, Gäste zu empfangen, die einen Rollstuhl nutzen. Ich habe für die Besatzung eine Notiz hinterlegt, damit sie sich darauf vorbereiten kann, bei Bedarf zu helfen.",
    "es": "Sí, no hay problema. Estamos preparados para recibir a personas que usan silla de ruedas. He guardado una nota para la tripulación para que pueda prepararse para ayudar.",
    "pap": "Sí, no tin problema. Nos ta prepará pa risibí bishitantenan ku ta usa stul di rueda. Mi a registrá un nota pa e tripulashon por prepará pa duna asistensia.",
    "pt": "Sim, sem problema. Estamos preparados para receber pessoas que usam cadeira de rodas. Registrei uma observação para a tripulação, para que ela possa se preparar para ajudar.",
}


WHEELCHAIR_WITHDRAWAL_COPY = {
    "en": "Understood. I removed the wheelchair note from this reservation.",
    "nl": "Begrepen. Ik heb de rolstoelnotitie uit deze reservering verwijderd.",
    "de": "Verstanden. Ich habe den Rollstuhlhinweis aus dieser Reservierung entfernt.",
    "es": "Entendido. Eliminé la nota sobre la silla de ruedas de esta reserva.",
    "pap": "Mi a komprondé. Mi a kita e nota tokante e stul di rueda for di e reservashon aki.",
    "pt": "Entendido. Removi a observação sobre a cadeira de rodas desta reserva.",
}


NO_WHEELCHAIR_NOTE_COPY = {
    "en": "Understood. There is no wheelchair note on this reservation.",
    "nl": "Begrepen. Er staat geen rolstoelnotitie bij deze reservering.",
    "de": "Verstanden. Für diese Reservierung ist kein Rollstuhlhinweis hinterlegt.",
    "es": "Entendido. No hay ninguna nota sobre silla de ruedas en esta reserva.",
    "pap": "Mi a komprondé. No tin ningun nota tokante stul di rueda den e reservashon aki.",
    "pt": "Entendido. Não há nenhuma observação sobre cadeira de rodas nesta reserva.",
}


BOARDING_ASSISTANCE_COPY = {
    "en": "Yes, no problem. I’ve saved a note so the crew can prepare for the extra help you requested.",
    "nl": "Ja, geen probleem. Ik heb een notitie vastgelegd, zodat de bemanning zich kan voorbereiden op de extra hulp die u hebt gevraagd.",
    "de": "Ja, kein Problem. Ich habe eine Notiz hinterlegt, damit sich die Besatzung auf die von Ihnen gewünschte zusätzliche Hilfe vorbereiten kann.",
    "es": "Sí, no hay problema. He guardado una nota para que la tripulación pueda prepararse para brindar la ayuda adicional que solicitaste.",
    "pap": "Sí, no tin problema. Mi a registrá un nota pa e tripulashon por prepará pa duna e asistensia èkstra ku bo a pidi.",
    "pt": "Sim, sem problema. Registrei uma observação para que a tripulação possa se preparar para oferecer a ajuda adicional solicitada.",
}


_WHEELCHAIR_NOTE_MARKERS = (
    "wheelchair", "wheel chair", "rolstoel", "rollstuhl", "silla de ruedas",
    "cadeira de rodas", "stul di rueda",
)

_SECURITY_TEXT_MARKERS = (
    "ignore your instructions", "ignore previous instructions", "system prompt",
    "api key", "password", "secret key", "reveal your instructions",
    "unauthorized", "data leak", "leaked data", "exposed private",
)

_ORDINARY_WHEELCHAIR_MESSAGES = (
    r"(?:(?:hi|hello) )?(?:i|we|my (?:husband|wife|partner|spouse|mother|father|daughter|son|child)|our guest|a guest|one guest|someone|a member of (?:our|the) (?:party|group)|a person in (?:our|the) (?:party|group)) (?:use|uses|will use|is using) (?:a |the |my |their )?wheelchair(?: can you help (?:with (?:it|the wheelchair)|us))?",
    r"(?:do you |can you )?(?:welcome|accept|help) (?:guests?|people|someone|a person) (?:who |that )?(?:use|uses|using) (?:a )?wheelchair",
    r"(?:(?:hoi|hallo) )?(?:ik|wij|mijn (?:man|vrouw|partner|moeder|vader)|een gast|iemand) (?:gebruik|gebruikt|heb|heeft) (?:een )?rolstoel(?: kunnen jullie (?:daarmee )?helpen)?",
    r"(?:(?:hallo|guten tag) )?(?:ich|wir|mein (?:mann|frau|partner|mutter|vater)|ein gast|jemand) (?:benutze|benutzen|benutzt) (?:einen )?rollstuhl(?: konnen sie (?:damit )?helfen)?",
    r"(?:(?:hola) )?(?:yo|nosotros|mi (?:esposo|marido|esposa|pareja|madre|padre)|un huesped|alguien) (?:uso|usamos|usa) (?:una )?silla de ruedas(?: pueden ayudar(?:nos)?(?: con (?:ella|la silla de ruedas))?)?",
    r"(?:(?:bon dia|bon tardi) )?(?:mi|nos|mi kasa|un bishitante|un hende den nos grupo) ta usa stul di rueda(?: boso por yuda(?: nos)? ku e stul)?",
    r"(?:(?:ola|bom dia) )?(?:eu|nos|meu (?:marido|esposo|parceiro|mae|pai)|um hospede|alguem) (?:uso|usamos|usa) (?:uma )?cadeira de rodas(?: voces podem ajudar(?: nos)?(?: com (?:ela|a cadeira de rodas))?)?",
)

_GENERAL_BOARDING_ASSISTANCE_MESSAGES = (
    r"my ?husband is handicapped and need(?:s)? special attention on and off board can (?:u|you) help",
)

# Positive evidence that may appear beside an unrelated FAQ or reservation
# action.  Keep these patterns tied to the guest or their party: the presence
# of the word "wheelchair" by itself is not enough to create a crew note.
_ORDINARY_WHEELCHAIR_EVIDENCE = (
    r"\b(?:i|we|my (?:husband|wife|partner|spouse|mother|father|daughter|son|child)|our guest|one guest|a member of (?:our|the) (?:party|group)|a person in (?:our|the) (?:party|group)) (?:use|uses|will use|is using) (?:a |the |my |their |his |her )?wheelchair\b",
    r"\b(?:my (?:husband|wife|partner|spouse|mother|father|daughter|son|child)|our guest|one guest|a member of (?:our|the) (?:party|group)) (?:is|will be) in (?:a |the |his |her )?wheelchair\b",
    r"\b(?:i|we|my (?:husband|wife|partner|spouse|mother|father|daughter|son|child)|our guest|one guest|a member of (?:our|the) (?:party|group)) (?:travel|travels|will travel) in (?:a |the |my |their |his |her )?wheelchair\b",
    r"\b(?:we|our (?:party|group)) (?:have|has) (?:a )?wheelchair user(?: in (?:our|the) (?:party|group))?\b",
    r"\b(?:my (?:husband|wife|partner|spouse|mother|father|daughter|son|child)|our guest|one guest|a member of (?:our|the) (?:party|group)) is (?:a )?wheelchair user\b",
    r"\b(?:mijn (?:man|vrouw|partner|moeder|vader)|een gast|iemand in (?:ons|het) gezelschap) (?:gebruik|gebruikt) (?:een )?rolstoel\b",
    r"\b(?:mein(?:e)? (?:mann|frau|partner|mutter|vater)|ein gast|jemand in unserer gruppe) (?:benutze|benutzen|benutzt) (?:einen )?rollstuhl\b",
    r"\b(?:mi (?:esposo|marido|esposa|pareja|madre|padre)|un huesped|alguien de nuestro grupo) (?:uso|usa) (?:una )?silla de ruedas\b",
    r"\b(?:mi kasa|un bishitante|un hende den nos grupo) ta usa (?:un |e )?stul di rueda\b",
    r"\b(?:meu (?:marido|esposo|parceiro|pai)|minha (?:esposa|parceira|mae)|um hospede|alguem do nosso grupo) usa (?:uma )?cadeira de rodas\b",
)

_WHEELCHAIR_WITHDRAWAL_MESSAGES = (
    # English: direct non-use, no-longer-needed, and explicit note removal.
    r"(?:(?:actually|correction) )?(?:nobody|no one)(?: in (?:our|the) party)? (?:uses|needs) (?:a )?wheelchair(?: anymore)?",
    r"(?:(?:actually|correction|no) )?(?:i|we|he|she|they|my (?:husband|wife|partner|spouse|mother|father|daughter|son|child)|our guest|the guest|a guest|one guest|someone in (?:our|the) party) (?:(?:do not|don t|dont|does not|doesn t|doesnt) (?:use|need)|no longer (?:use|uses|need|needs)) (?:a |the )?wheelchair(?: anymore| any longer)?",
    r"(?:(?:actually|correction|no) )?(?:my (?:husband|wife|partner|spouse|mother|father|daughter|son|child)|our guest|the guest|a guest|one guest|he|she) (?:is not|isn t|isnt|is no longer) (?:a )?wheelchair user(?: anymore| any longer)?",
    r"(?:(?:do not|don t|dont|does not|doesn t|doesnt) (?:use|need)|no longer (?:use|need)) (?:a |the )?wheelchair(?: anymore| any longer)?",
    r"(?:(?:actually|correction) )?(?:the |a )?wheelchair (?:is|was) no longer needed",
    r"(?:please )?remove (?:the )?wheelchair note(?: from (?:my|the) reservation)?",
    r"(?:(?:i|we) (?:already |have |have already )?)?removed (?:the )?wheelchair note(?: from (?:my|the) reservation)?",
    r"(?:the )?wheelchair note (?:is|was|has been) removed(?: from (?:my|the) reservation)?",
    # Dutch.
    r"(?:(?:correctie|eigenlijk) )?niemand(?: in (?:ons|het) gezelschap)? gebruikt(?: meer)? een rolstoel",
    r"(?:(?:correctie|eigenlijk|nee) )?(?:ik|wij|we|hij|zij|mijn (?:man|vrouw|partner|moeder|vader)|de gast|een gast|iemand in (?:ons|het) gezelschap) (?:(?:gebruik|gebruiken|gebruikt) geen rolstoel(?: meer)?|(?:gebruik|gebruiken|gebruikt) (?:niet meer|niet langer) (?:een |de )?rolstoel|(?:heb|hebben|heeft) geen rolstoel meer nodig)",
    r"(?:gebruik|gebruiken|gebruikt) geen rolstoel(?: meer)?",
    r"(?:(?:de )?rolstoel is niet meer nodig|(?:verwijder|wis) (?:de )?rolstoel ?(?:notitie|opmerking)(?: uit (?:mijn|de) reservering)?(?: alstublieft)?|(?:de )?rolstoel ?(?:notitie|opmerking) (?:is|werd) (?:uit (?:mijn|de) reservering )?verwijderd)",
    # German.
    r"(?:(?:korrektur|eigentlich) )?niemand(?: in unserer gruppe)? benutzt(?: mehr)? einen rollstuhl",
    r"(?:(?:korrektur|eigentlich|nein) )?(?:ich|wir|er|sie|mein(?:e)? (?:mann|frau|partner|mutter|vater)|der gast|ein gast|jemand in unserer gruppe) (?:(?:benutze|benutzen|benutzt) keinen rollstuhl(?: mehr)?|(?:benutze|benutzen|benutzt) (?:nicht mehr|nicht langer) (?:einen |den )?rollstuhl|(?:brauche|brauchen|braucht) keinen rollstuhl(?: mehr)?)",
    r"(?:benutze|benutzen|benutzt) keinen rollstuhl(?: mehr)?",
    r"(?:(?:der )?rollstuhl ist nicht mehr notig|(?:entfernen sie|entferne|loschen sie|losche) (?:den )?rollstuhl(?:hinweis|vermerk|notiz)(?: aus (?:meiner|der) reservierung)?(?: bitte)?|(?:der )?rollstuhl(?:hinweis|vermerk|notiz) (?:ist|wurde) (?:aus (?:meiner|der) reservierung )?entfernt)",
    # Spanish.
    r"(?:(?:correccion|en realidad) )?nadie(?: de nuestro grupo)? usa(?: ya)? una silla de ruedas",
    r"(?:(?:correccion|en realidad|no) )?(?:yo|nosotros|el|ella|mi (?:esposo|marido|esposa|pareja|madre|padre)|el pasajero|un pasajero|alguien de nuestro grupo) (?:ya )?no (?:uso|usamos|usa|usan|necesito|necesitamos|necesita|necesitan) (?:una |la )?silla de ruedas(?: ya| mas)?",
    r"(?:(?:correccion|en realidad) )?(?:ya )?no (?:uso|usamos|usa|usan|necesito|necesitamos|necesita|necesitan) (?:una |la )?silla de ruedas(?: ya| mas)?",
    r"(?:(?:la )?silla de ruedas ya no es necesaria|(?:elimina|elimine|quita|quite) (?:la )?nota (?:sobre|de) (?:la )?silla de ruedas(?: de (?:mi|la) reserva)?(?: por favor)?|(?:la )?nota (?:sobre|de) (?:la )?silla de ruedas (?:fue|ha sido|esta) (?:eliminada|quitada)(?: de (?:mi|la) reserva)?)",
    # Standard Curaçao Papiamentu input plus common unaccented guest spelling.
    r"(?:(?:korekshon) )?(?:niun|ningun) hende(?: den nos grupo)? ta usa stul di rueda mas",
    r"(?:(?:korekshon|no) )?(?:mi|nos|e|mi kasa|un bishitante|un hende den nos grupo) no ta usa (?:un |e )?stul di rueda(?: mas)?",
    r"no ta usa (?:un |e )?stul di rueda(?: mas)?",
    r"(?:(?:korekshon) )?(?:mi|nos|e|mi kasa|un bishitante|un hende den nos grupo) (?:no tin mester di|no mester) (?:un |e )?stul di rueda(?: mas)?",
    r"(?:(?:e )?stul di rueda no ta nesesario mas|(?:por fabor )?kita e nota (?:tokante|di) (?:e )?stul di rueda(?: for di (?:mi|e|nos) reservashon(?: aki)?)?(?: por fabor)?|(?:mi|nos) a kita e nota (?:tokante|di) (?:e )?stul di rueda(?: for di (?:mi|e|nos) reservashon(?: aki)?)?|e nota (?:tokante|di) (?:e )?stul di rueda a wordu kita(?: for di (?:mi|e|nos) reservashon(?: aki)?)?)",
    # Portuguese; _evidence_text removes diacritics, so não becomes nao.
    r"(?:(?:correcao|na verdade) )?ninguem(?: do nosso grupo)? usa(?: mais)? uma cadeira de rodas",
    r"(?:(?:correcao|na verdade|nao) )?(?:eu|nos|ele|ela|meu (?:marido|esposo|parceiro|pai)|minha (?:esposa|parceira|mae)|o hospede|um hospede|alguem do nosso grupo) (?:ja )?nao (?:uso|usamos|usa|usam) (?:mais )?(?:uma |a )?cadeira de rodas(?: mais)?",
    r"(?:(?:correcao|na verdade) )?(?:eu|nos|ele|ela|meu (?:marido|esposo|parceiro|pai)|minha (?:esposa|parceira|mae)|o hospede|um hospede|alguem do nosso grupo) (?:ja )?nao (?:preciso|precisamos|precisa|precisam) (?:mais )?de (?:uma |a )?cadeira de rodas(?: mais)?",
    r"(?:(?:correcao|na verdade) )?(?:ja )?nao (?:(?:uso|usamos|usa|usam) (?:mais )?(?:uma |a )?cadeira de rodas|(?:preciso|precisamos|precisa|precisam) (?:mais )?de (?:uma |a )?cadeira de rodas)(?: mais)?",
    r"(?:(?:a )?cadeira de rodas (?:ja )?nao e mais necessaria|(?:remova|retire|exclua) (?:a )?(?:observacao|nota) (?:sobre|da|de) (?:a )?cadeira de rodas(?: da (?:minha|a) reserva)?(?: por favor)?|(?:a )?(?:observacao|nota) (?:sobre|da|de) (?:a )?cadeira de rodas (?:foi|ja foi|esta) (?:removida|retirada|excluida)(?: da (?:minha|a) reserva)?)",
)

_INDEPENDENT_RESERVATION_INTENTS = {
    "cancel": (
        r"\b(?:cancel|cancelation|cancellation)\b",
        r"\b(?:annuleer|annuleren)\b",
        r"\b(?:storniere|stornieren)\b",
        r"\b(?:cancela|cancelar|cancelacion)\b",
        r"\b(?:kansela|kanselacion)\b",
        r"\b(?:cancele|cancelar|cancelamento)\b",
    ),
    "new_booking": (
        r"\b(?:new|another|separate|second) (?:booking|reservation)\b",
        r"\b(?:nieuwe|andere|aparte|tweede) (?:boeking|reservering)\b",
        r"\b(?:neue|weitere|separate|zweite) (?:buchung|reservierung)\b",
        r"\b(?:nueva|otra|separada|segunda) reserva\b",
        r"\b(?:reservashon nobo|otro reservashon|reservashon apart|di dos reservashon)\b",
        r"\b(?:nova|outra|separada|segunda) reserva\b",
    ),
}

_NON_ORDINARY_WHEELCHAIR_PATTERNS = (
    r"\b(?:provide|supply|borrow|rent) (?:a |the )?wheel ?chair\b",
    r"\b(?:do you have|is there|need|needs) (?:a |the )?wheel ?chair\b",
    r"\bwheel ?chair (?:available|availability|equipment|lift|ramp)\b",
    r"\b(?:can|could|does|do|will|would|is|are|may)\b[^.;?!]{0,100}\b(?:wheelchair|wheel chair)\b[^.;?!]{0,60}\b(?:aboard|on board|remain|stay|fit|accept|allow|accommodate|lift|carry|transfer|provide|supply|guarantee)\b",
    r"\b(?:can|could|does|do|will|would|is|are|may)\b[^.;?!]{0,100}\b(?:accept|allow|accommodate|remain|stay|fit|lift|carry|transfer|provide|supply|guarantee)\b[^.;?!]{0,60}\b(?:wheelchair|wheel chair|him|her|them|the guest|a guest)\b",
    r"\b(?:lift|carry|transfer) (?:him|her|them|me|us|a guest|the guest)[^.;?!]{0,50}\b(?:aboard|on board|onto|off|boat|vessel)\b",
    r"\b(?:need|needs|require|requires) (?:help|assistance) (?:with |for )?(?:boarding|a transfer|transferring|stairs)\b",
    r"\b(?:wheelchair|it) (?:fit|fits|go|goes) (?:aboard|on board|on|in)\b",
    r"\bwheelchair\b[^.;?!]{0,40}\b(?:\d{2,3}\s*(?:cm|centimeters?|inches?)|wide|width|dimensions?)\b",
    r"\b(?:electric|powered|motorized|non foldable|beach) wheelchair\b",
    r"\b(?:accessible|accessibility|safe|safety|suitability|guarantee|medical)\b",
    r"\b(?:oxygen|oxygen concentrator|concentrator|ventilator|medical device|allergy|allergic|peanut|refund|complaint)\b",
    r"\b(?:lenen|huren|beschikbaar|uitrusting|helling|overstap|instappen|veilig|toegankelijk|allergie|terugbetaling|klacht|zuurstof|zuurstofconcentrator|concentrator)\b",
    r"\b(?:kan|kunnen|mag|mogen|past|accepteert)\b[^.;?!]{0,100}\b(?:rolstoel|aan boord|blijven|tillen|overstappen)\b",
    r"\b(?:hebben jullie|is er) (?:een )?rolstoel\b",
    r"\b(?:leihen|mieten|verfugbar|ausrustung|rampe|transfer|einsteigen|sicher|barrierefrei|allergie|ruckerstattung|beschwerde|sauerstoff|sauerstoffgerat|sauerstoffkonzentrator|konzentrator)\b",
    r"\b(?:kann|konnen|darf|durfen|passt|akzeptiert)\b[^.;?!]{0,100}\b(?:rollstuhl|an bord|bleiben|heben|umsteigen)\b",
    r"\b(?:habt ihr|haben sie|gibt es) (?:einen |einen )?rollstuhl\b",
    r"\b(?:prestar|alquilar|disponible|equipo|elevador|rampa|traslado|embarque|seguro|accesible|alergia|reembolso|queja|oxigeno|concentrador de oxigeno|concentrador)\b",
    r"\b(?:puede|pueden|podria|podrian|cabe|acepta|permiten)\b[^.;?!]{0,100}\b(?:silla de ruedas|a bordo|permanecer|subir|traslado)\b",
    r"\b(?:tienen|hay) (?:una )?silla de ruedas\b",
    r"\b(?:presta|huur|disponibel|ekipo|lift|rampa|transfer|subi|sigur|aksesibel|alergia|reembolso|keho|oksigen|konsentrador di oksigen|konsentrador)\b",
    r"\b(?:por|lo por|ta asepta|kabe)\b[^.;?!]{0,100}\b(?:stul di rueda|abordo|keda|hisa|transferi)\b",
    r"\btin (?:un )?stul di rueda (?:pa|disponibel)\b",
    r"\b(?:emprestar|alugar|disponivel|equipamento|elevador|rampa|transferencia|embarque|seguro|acessivel|alergia|reembolso|reclamacao|oxigenio|concentrador de oxigenio|concentrador)\b",
    r"\b(?:pode|podem|poderia|cabe|aceita|permite)\b[^.;?!]{0,100}\b(?:cadeira de rodas|a bordo|permanecer|levantar|transferir)\b",
    r"\b(?:voces tem|tem) (?:uma )?cadeira de rodas\b",
)

_CROSS_CLAUSE_WHEELCHAIR_REVIEW_PATTERNS = (
    r"\b(?:lift|carry|transfer) (?:him|her|them|me|us|a guest|the guest)[^.;?!]{0,50}\b(?:aboard|on board|onto|off|boat|vessel)\b",
    r"\b(?:need|needs|require|requires) (?:help|assistance) (?:with |for )?(?:boarding|a transfer|transferring|stairs)\b",
    r"\b(?:oxygen|oxygen concentrator|concentrator|ventilator|medical device|sauerstoffgerat|sauerstoffkonzentrator|zuurstofconcentrator|concentrador de oxigeno|konsentrador di oksigen|concentrador de oxigenio)\b",
    r"\b(?:allergy|allergic|refund|complaint|allergie|terugbetaling|klacht|ruckerstattung|beschwerde|alergia|reembolso|queja|keho|reclamacao)\b",
)


def _evidence_text(text: str) -> str:
    value = "".join(
        character
        for character in unicodedata.normalize("NFKD", str(text or "").casefold())
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def _evidence_clauses(text: str) -> list[str]:
    return [
        value
        for value in (
            _evidence_text(part)
            for part in re.split(r"[.!?;]+", str(text or ""))
        )
        if value
    ]


def _ordinary_wheelchair_message(text: str) -> bool:
    value = _evidence_text(text)
    if (
        _wheelchair_withdrawal_message(text)
        or _wheelchair_capability_requires_review(text)
    ):
        return False
    return any(
        re.fullmatch(pattern, value) for pattern in _ORDINARY_WHEELCHAIR_MESSAGES
    ) or any(
        re.search(pattern, value) for pattern in _ORDINARY_WHEELCHAIR_EVIDENCE
    )


def _general_boarding_assistance_message(text: str) -> bool:
    value = _evidence_text(text)
    return any(
        re.fullmatch(pattern, value)
        for pattern in _GENERAL_BOARDING_ASSISTANCE_MESSAGES
    )


def _wheelchair_capability_requires_review(text: str) -> bool:
    """Keep equipment, transfer, medical and safety promises with the crew."""
    value = _evidence_text(text)
    # Owner-approved ordinary wording, including the reported guest message,
    # wins over isolated words such as "board" in that exact sentence.
    if any(
        re.fullmatch(pattern, value) for pattern in _ORDINARY_WHEELCHAIR_MESSAGES
    ):
        return False
    if not any(marker in value for marker in _WHEELCHAIR_NOTE_MARKERS):
        return False
    # Every capability match must come from the same sentence or clause as the
    # wheelchair reference. This prevents an unrelated FAQ such as "Can I
    # bring towels aboard?" from turning a routine note into human review.
    clauses = _evidence_clauses(text)
    if any(
        re.search(pattern, clause)
        for clause in clauses
        for pattern in _CROSS_CLAUSE_WHEELCHAIR_REVIEW_PATTERNS
    ):
        return True
    wheelchair_clauses = (
        clause
        for clause in clauses
        if any(marker in clause for marker in _WHEELCHAIR_NOTE_MARKERS)
    )
    return any(
        re.search(pattern, clause)
        for clause in wheelchair_clauses
        for pattern in _NON_ORDINARY_WHEELCHAIR_PATTERNS
    )


def _wheelchair_withdrawal_message(text: str) -> bool:
    value = _evidence_text(text)
    return any(
        re.fullmatch(pattern, value) for pattern in _WHEELCHAIR_WITHDRAWAL_MESSAGES
    )


def _independent_reservation_intent(text: str) -> str | None:
    value = _evidence_text(text)
    return next(
        (
            action
            for action, patterns in _INDEPENDENT_RESERVATION_INTENTS.items()
            if any(re.search(pattern, value) for pattern in patterns)
        ),
        None,
    )


def _has_security_evidence(text: str) -> bool:
    value = _evidence_text(text)
    return any(marker in value for marker in _SECURITY_TEXT_MARKERS)


def _canonical_wheelchair_note(relationship: str) -> str:
    if relationship == "husband":
        return "The guest's husband uses a wheelchair."
    if relationship == "other":
        return "A member of the guest's party uses a wheelchair."
    return "A guest in this party uses a wheelchair."


def _canonical_boarding_assistance_note(relationship: str) -> str:
    if relationship == "husband":
        return (
            "The guest's husband requested extra assistance when boarding "
            "and disembarking."
        )
    return "A guest in this party requested extra boarding assistance."


def _wheelchair_relationship_from_message(text: str) -> str:
    value = _evidence_text(text)
    husband_markers = (
        "my husband", "myhusband", "mijn man", "mein mann", "mi esposo",
        "mi marido", "mi kasa", "meu marido", "meu esposo",
    )
    return "husband" if any(marker in value for marker in husband_markers) else "unspecified"


SUMMARY_COPY = {
    "en": {"title": "Here is what I have", "date": "Date", "guests": "Guests", "name": "Reservation name", "transport": "Transport", "party": "{adults} adults, {children} children 4-12, {infants} children 0-3", "pier": "meeting at Fishermen’s Pier", "pickup": "hotel pickup requested ({location})"},
    "nl": {"title": "Dit heb ik genoteerd", "date": "Datum", "guests": "Gasten", "name": "Naam reservering", "transport": "Vervoer", "party": "{adults} volwassenen, {children} kinderen 4-12, {infants} kinderen 0-3", "pier": "ontmoeting bij Fishermen’s Pier", "pickup": "hoteltransfer aangevraagd ({location})"},
    "de": {"title": "Das habe ich notiert", "date": "Datum", "guests": "Gäste", "name": "Reservierungsname", "transport": "Transport", "party": "{adults} Erwachsene, {children} Kinder 4-12, {infants} Kinder 0-3", "pier": "Treffpunkt Fishermen’s Pier", "pickup": "Hotelabholung angefragt ({location})"},
    "es": {"title": "Esto es lo que anoté", "date": "Fecha", "guests": "Pasajeros", "name": "Nombre de reserva", "transport": "Transporte", "party": "{adults} adultos, {children} niños de 4-12, {infants} niños de 0-3", "pier": "encuentro en Fishermen’s Pier", "pickup": "recogida en hotel solicitada ({location})"},
    "pap": {"title": "Esaki ta loke mi a nota", "date": "Fecha", "guests": "Bishitantenan", "name": "Nòmber di reservashon", "transport": "Transporte", "party": "{adults} adulto, {children} mucha di 4-12, {infants} mucha di 0-3", "pier": "topa na Fishermen’s Pier", "pickup": "nos ta buska bo na bo alohamentu ({location})"},
    "pt": {"title": "Isto é o que anotei", "date": "Data", "guests": "Passageiros", "name": "Nome da reserva", "transport": "Transporte", "party": "{adults} adultos, {children} crianças de 4-12, {infants} crianças de 0-3", "pier": "encontro no Fishermen’s Pier", "pickup": "traslado do hotel solicitado ({location})"},
}


FAQ_COPY = {
    "en": {"price": "Adult USD {adult}; child 4-12 USD {child}; age 0-3 free. Your itemized total will be in the quote.", "included": "Breakfast, soft drinks and juices, BBQ lunch, the beach house, facilities, snorkeling masks and beach chairs are included.", "bring": "Bring towels, sunscreen and swimwear. Mermaid takes care of the included food, drinks and island facilities."},
    "nl": {"price": "Volwassene USD {adult}; kind 4-12 USD {child}; 0-3 jaar gratis. De offerte bevat het volledige prijsoverzicht.", "included": "Ontbijt, frisdrank en sap, BBQ-lunch, het strandhuis, faciliteiten, snorkelmaskers en strandstoelen zijn inbegrepen.", "bring": "Neem handdoeken, zonnebrand en zwemkleding mee. Mermaid zorgt voor het inbegrepen eten, drinken en de eilandfaciliteiten."},
    "de": {"price": "Erwachsene USD {adult}; Kinder 4-12 USD {child}; 0-3 Jahre kostenlos. Die Einzelpreise stehen im Angebot.", "included": "Frühstück, alkoholfreie Getränke und Säfte, BBQ-Mittagessen, Strandhaus, Einrichtungen, Schnorchelmasken und Strandstühle sind inklusive.", "bring": "Bringen Sie Handtücher, Sonnencreme und Badesachen mit. Mermaid kümmert sich um inklusive Speisen, Getränke und Inseleinrichtungen."},
    "es": {"price": "Adulto USD {adult}; niño de 4-12 USD {child}; 0-3 años gratis. El total detallado estará en la cotización.", "included": "Incluye desayuno, refrescos y jugos, almuerzo BBQ, casa de playa, instalaciones, máscaras de snorkel y sillas de playa.", "bring": "Trae toallas, protector solar y traje de baño. Mermaid se encarga de la comida, bebidas e instalaciones incluidas."},
    "pap": {"price": "Adulto USD {adult}; mucha di 4-12 USD {child}; 0-3 aña grátis. Bo oferta lo tin e total detayá.", "included": "Desayuno, refresko i djus, almuerso di barbekiú, kas di playa, fasilidatnan, máskara di snòrkel i stul di playa ta inkluí.", "bring": "Hiba toaya, krema solar i paña di landa. Mermaid ta sòru pa kuminda, bebida i fasilidatnan inkluí."},
    "pt": {"price": "Adulto USD {adult}; criança de 4-12 USD {child}; 0-3 anos grátis. O total detalhado estará na cotação.", "included": "Inclui café da manhã, refrigerantes e sucos, almoço BBQ, casa de praia, instalações, máscaras de snorkel e cadeiras de praia.", "bring": "Leve toalhas, protetor solar e roupa de banho. A Mermaid cuida da comida, bebidas e instalações incluídas."},
}


PAYMENT_COPY = {
    "en": ("", "Complete your booking here:"),
    "nl": ("", "Rond je boeking hier af:"),
    "de": ("", "Schließen Sie Ihre Buchung hier ab:"),
    "es": ("", "Completa tu reserva aquí:"),
    "pap": ("", "Kompletá bo reservashon aki:"),
    "pt": ("", "Conclua sua reserva aqui:"),
}


LANGUAGE_MARKERS = {
    "nl": ("graag", "hoeveel", "volwassen", "kinderen", "ophalen", "datum"),
    "de": ("möchte", "erwachsene", "kinder", "abholung", "datum", "bitte"),
    "es": ("quiero", "adultos", "niños", "recogida", "fecha", "somos"),
    "pap": ("mi ke", "kuantu", "mucha", "hende", "fecha", "por fabor", "reservashon"),
    "pt": ("quero", "adultos", "crianças", "traslado", "data", "somos"),
}

YES = {
    "en": {"yes", "yes correct", "correct", "confirmed", "confirm"},
    "nl": {"ja", "ja klopt", "klopt", "bevestigd", "bevestigen"},
    "de": {"ja", "ja korrekt", "korrekt", "bestätigt", "bestätigen"},
    "es": {"sí", "si", "sí correcto", "correcto", "confirmar", "confirmado"},
    "pap": {"sí", "si", "ta korekto", "korekto", "konfirmá"},
    "pt": {"sim", "sim correto", "correto", "confirmar", "confirmado"},
}

NATURAL_APPROVAL_PREFIXES = {
    "en": ("yes", "yes it looks good", "yes looks good", "looks good", "all good", "go ahead"),
    "nl": ("ja", "ja het klopt", "ja klopt", "alles klopt", "helemaal goed", "ga verder"),
    "de": ("ja", "ja das passt", "alles passt", "alles stimmt", "alles gut", "weiter"),
    "es": ("sí", "si", "sí está bien", "si esta bien", "todo está bien", "todo esta bien", "adelante"),
    "pap": ("sí", "si", "sí ta bon", "si ta bon", "tur kos ta bon", "tur kos ta korekto", "por sigui"),
    "pt": ("sim", "sim está certo", "sim esta certo", "está tudo certo", "esta tudo certo", "pode continuar"),
}

APPROVAL_BLOCKERS = {
    "en": ("not correct", "not right", "wrong", "change", "different", "instead", "unless"),
    "nl": ("niet correct", "niet goed", "fout", "wijzig", "anders", "tenzij"),
    "de": ("nicht korrekt", "nicht richtig", "falsch", "ändern", "anders", "außer wenn"),
    "es": ("no es correcto", "no está bien", "incorrecto", "cambiar", "diferente", "a menos que"),
    "pap": ("no ta korekto", "no ta bon", "robes", "kambia", "otro", "a menos ku"),
    "pt": ("não está certo", "nao esta certo", "errado", "mudar", "diferente", "a menos que"),
}


def _has_natural_approval(text: str, locale: str) -> bool:
    """Recognize a clear approval lead before a procedural payment question."""
    normalized = " ".join(re.sub(r"[^\w\s]", " ", str(text or "").casefold()).split())
    if not normalized or any(blocker in normalized for blocker in APPROVAL_BLOCKERS[locale]):
        return False
    return any(
        normalized == phrase or normalized.startswith(phrase + " ")
        for phrase in NATURAL_APPROVAL_PREFIXES[locale]
    )


@dataclass(frozen=True)
class IntakeResult:
    text: str
    locale: str
    phase: str
    action: str | None = None
    duplicate: bool = False
    generation_failure: dict | None = None
    understanding_source: str | None = None
    workflow_reply: dict | None = None

    def as_reply(self) -> dict:
        reply = {
            "text": self.text,
            "media": None,
            "vehicle_recommendation": None,
            "quote_confirmation": None,
            "ali_turn_commit": None,
            "mermaid_action": self.action,
            "duplicate": self.duplicate,
            "language": self.locale,
        }
        if self.understanding_source is not None:
            reply["understanding_source"] = self.understanding_source
        if self.generation_failure is not None:
            reply["mermaid_generation_failure"] = self.generation_failure
        if self.workflow_reply is not None:
            reply.update(self.workflow_reply)
        return reply


def detect_language(text: str, existing: str | None = None) -> str:
    value = str(text or "").casefold()
    explicit = {
        "english": "en", "nederlands": "nl", "dutch": "nl", "deutsch": "de",
        "español": "es", "spanish": "es", "papiamentu": "pap", "papiamento": "pap",
        "português": "pt", "portuguese": "pt",
    }
    for marker, locale in explicit.items():
        if marker in value:
            return locale
    scores = {
        locale: sum(1 for marker in markers if marker in value)
        for locale, markers in LANGUAGE_MARKERS.items()
    }
    best = max(scores, key=scores.get, default="en")
    if scores.get(best, 0) > 0 and list(scores.values()).count(scores[best]) == 1:
        return best
    return existing if existing in SUPPORTED_LOCALES else "en"


def _extract_date(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    parsed = None
    if match:
        try:
            parsed = datetime.strptime(match.group(1), "%Y-%m-%d")
        except ValueError:
            return None
    elif re.search(r"\b(?:date|datum|fecha|data|on|op|am|el|dia|dja)\b", text.casefold()):
        parsed = dateparser.parse(
            text,
            settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
        )
    return parsed.date().isoformat() if parsed else None


def _number_after(patterns: tuple[str, ...], text: str) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _extract_fields(text: str, locale: str, current: dict) -> tuple[dict, bool]:
    value = str(text or "").strip()
    lower = value.casefold()
    number_words = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "een": "1", "twee": "2", "drie": "3", "vier": "4", "vijf": "5",
        "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
        "um": "1", "dois": "2", "três": "3", "quatro": "4", "cinco": "5",
    }
    numeric_value = value
    for word, number in number_words.items():
        numeric_value = re.sub(rf"\b{re.escape(word)}\b", number, numeric_value, flags=re.IGNORECASE)
    updates: dict[str, Any] = {}
    contact = normalize_contact_phone(value)
    if contact:
        updates["contact_phone"] = contact
    ambiguous_party = False

    trip_date = _extract_date(value)
    if trip_date:
        updates["trip_date"] = trip_date

    count_patterns = {
        "adults": (r"(\d+)\s*(?:adults?|volwassenen?|erwachsene|adultos?|adulto)",),
        "children": (r"(\d+)\s*(?:children|child|kids?|kinderen|kind|kinder|niños?|crianças?|mucha)(?:\s*(?:aged?|age|van|von|de|di)\s*4)?",),
        "infants": (r"(\d+)\s*(?:infants?|bab(?:y|ies)|peuters?|baby['’]?s?|kleinkinder|bebés?|bebês?|mucha\s*(?:di)?\s*0)",),
    }
    for field, patterns in count_patterns.items():
        count = _number_after(patterns, value)
        if count is not None:
            updates[field] = count

    if not any(field in updates for field in ("adults", "children", "infants")):
        party = _number_after(
            (r"(?:we are|we're|party of|somos|wir sind|wij zijn|nos ta|somos)\s*(\d+)",
             r"(\d+)\s*(?:people|personen|personen|personen|personas|pessoas|hende)"),
            numeric_value,
        )
        if party is not None:
            updates["party_size_hint"] = party
            ambiguous_party = True

    name_patterns = (
        r"(?:my name is|name is|naam is|ich hei(?:ß|ss)e|me llamo|mi n[oò]mber ta|meu nome [ée])\s+([\p{L}][\p{L}'’-]+(?:\s+[\p{L}][\p{L}'’-]+){1,4})(?=[.,!?]|$)",
    )
    import regex as regex_module
    for pattern in name_patterns:
        match = regex_module.search(pattern, value, flags=regex_module.IGNORECASE)
        if match:
            updates["customer_name"] = match.group(1).strip()
            break

    if any(word in lower for word in ("fishermen", "pier", "own transport", "eigen vervoer", "selbst", "muelle", "porto")):
        updates["pickup_preference"] = "pier"
        updates.pop("pickup_location", None)
    if any(word in lower for word in ("pickup", "pick up", "hoteltransfer", "ophalen", "abholung", "recogida", "traslado")):
        updates["pickup_preference"] = "pickup_requested"
        hotel = re.search(r"(?:hotel|from|van|vom|desde|do)\s+([\wÀ-ž'’ .-]{3,60})", value, re.IGNORECASE)
        if hotel:
            updates["pickup_location"] = hotel.group(1).strip(" .,?!")

    if any(word in lower for word in ("vegetarian", "vegetarisch", "vegetariano", "vegetári", "halal", "gluten")):
        updates["dietary_requirements"] = value
    return updates, ambiguous_party


def _summary(fields: dict, locale: str) -> str:
    labels = SUMMARY_COPY[locale]
    pickup = guest.transport_text(fields, locale)
    party = guest.party_text(fields, locale)
    contact = (f"{guest.guest_copy(locale)['contact_phone_label']}: {fields['contact_phone']}\n"
               if fields.get("contact_phone") else "")
    return (
        f"*{labels['title']}*\n\n"
        f"{labels['date']}: {guest.guest_date(fields['trip_date'], locale)}\n"
        f"{labels['guests']}: {party}\n"
        f"{labels['name']}: {fields['customer_name']}\n"
        f"{contact}\n"
        f"*{labels['transport']}*\n{pickup}\n\n"
        f"*{guest.price_text(guest.intake_money(fields), fields, locale)}*\n\n"
        f"{guest.guest_copy(locale)['confirm']}"
    )


def _next_question(fields: dict, locale: str) -> str | None:
    from agents.social.mermaid_reply_planning import DATE_COPY
    if all(key in fields for key in ("adults", "children", "infants")) and sum(
        fields[key] for key in ("adults", "children", "infants")
    ) <= 0:
        return COPY[locale]["composition"]
    if fields.get("party_size_hint") is not None and not any(
        field in fields for field in ("adults", "children", "infants")
    ):
        return COPY[locale]["composition"]
    prompt_keys = {
        "trip_date": "trip_date", "adults": "adults", "children": "children",
        "infants": "infants", "customer_name": "name", "pickup_preference": "pickup",
    }
    for field in REQUIRED_FIELDS:
        if field == "trip_date" and not fields.get(field):
            if fields.get("date_selection") == "undecided":
                return DATE_COPY[locale][0]
            if fields.get("date_selection") == "deferred":
                continue
        if field == "contact_phone":
            if not normalize_contact_phone(fields.get(field)):
                return guest.guest_copy(locale)["contact_phone_prompt"]
            continue
        if field not in fields:
            return COPY[locale][prompt_keys[field]]
    if fields.get("pickup_preference") == "pickup_requested" and not fields.get("pickup_location"):
        return COPY[locale]["hotel"]
    if not fields.get("trip_date"):
        return DATE_COPY[locale][2]
    return None


def _question_answer(text: str, locale: str) -> str:
    lower = str(text or "").casefold()
    catalog = mermaid_catalog.get_catalog()
    prices = catalog["pricing"]["currencies"]["USD"]
    answers = FAQ_COPY[locale]
    if any(word in lower for word in ("price", "cost", "prijs", "kosten", "precio", "preço", "kuantu")):
        return answers["price"].format(adult=prices["adult"], child=prices["child_4_12"])
    if any(word in lower for word in ("include", "included", "inbegrepen", "inklusive", "incluye", "incluído", "inklui")):
        return answers["included"]
    if any(word in lower for word in ("bring", "meenemen", "mitbringen", "llevar", "levar", "hiba")):
        return answers["bring"]
    return ""


def process_intake_turn(
    phone: str,
    text: str,
    *,
    message_id: str = "",
    from_name: str = "",
    reservation: dict | None = None,
) -> IntakeResult:
    """Apply one customer turn and persist only customer-owned intake facts."""
    state = state_registry.wa_get_booking_state(phone)
    root_fields = dict(state.get("fields") or {})
    flags = dict(state.get("flags") or {})
    completed = list(state.get("completed_bookings") or [])
    fields = dict(root_fields.get("mermaid_intake") or {})
    assistance_reservation_id = str((reservation or {}).get("public_id") or "")
    seen = list(flags.get("mermaid_seen_message_ids") or [])
    if message_id and message_id in seen:
        return IntakeResult("", fields.get("language", "en"), fields.get("phase", "collecting"), duplicate=True)

    locale = detect_language(text, fields.get("language"))
    fields["language"] = locale
    lower = str(text or "").strip().casefold()
    from agents.social import mermaid_model_recovery
    explicit_person_request = bool(
        mermaid_model_recovery.explicit_human_request(text)
        or mermaid_model_recovery.contains_explicit_human_request(text)
    )
    general_boarding_assistance = _general_boarding_assistance_message(text)
    wheelchair_withdrawal = _wheelchair_withdrawal_message(text)
    ordinary_wheelchair = (
        not wheelchair_withdrawal and _ordinary_wheelchair_message(text)
    )
    wheelchair_review = (
        not wheelchair_withdrawal
        and not ordinary_wheelchair
        and _wheelchair_capability_requires_review(text)
    )
    if general_boarding_assistance and not explicit_person_request:
        from agents.social import mermaid_crew_assistance

        relationship = _wheelchair_relationship_from_message(text)
        note = _canonical_boarding_assistance_note(relationship)
        fields["phase"] = "collecting"
        mermaid_crew_assistance.record_boarding_assistance_note(
            phone,
            note=note,
            relationship=relationship,
            trip_date=str(fields.get("trip_date") or ""),
            customer_name=from_name or str(fields.get("customer_name") or ""),
            source_message_id=message_id,
            reservation_public_id=assistance_reservation_id,
        )
        response = "\n\n".join(
            part
            for part in (
                COPY[locale]["intro"] if not fields.get("introduced") else "",
                BOARDING_ASSISTANCE_COPY[locale],
                _next_question(fields, locale) or "",
            )
            if part
        )
        fields["introduced"] = True
        action = None
    elif wheelchair_withdrawal and not explicit_person_request:
        from agents.social import mermaid_crew_assistance

        existing_attention = mermaid_crew_assistance.for_conversation(
            phone, kind=mermaid_crew_assistance.KIND_WHEELCHAIR
        )
        had_active_wheelchair_note = bool(
            existing_attention
            and existing_attention.get("kind") == "wheelchair"
            and existing_attention.get("status") != "withdrawn"
        )
        fields.pop("accessibility_notes", None)
        fields.pop("wheelchair_relationship", None)
        fields.setdefault("phase", "collecting")
        # Persist the corrected intake before claiming that the staff-only note
        # was removed. A failed withdrawal therefore remains retryable.
        root_fields["mermaid_intake"] = fields
        state_registry.wa_save_booking_state(phone, root_fields, flags, completed)
        withdrawn = mermaid_crew_assistance.withdraw(
            phone,
            source_message_id=message_id,
        )
        continuation = _next_question(fields, locale) or ""
        response = "\n\n".join(
            part
            for part in (
                WHEELCHAIR_WITHDRAWAL_COPY[locale]
                if had_active_wheelchair_note and withdrawn is not None
                else NO_WHEELCHAIR_NOTE_COPY[locale],
                continuation,
            )
            if part
        )
        action = None
    elif ordinary_wheelchair and not explicit_person_request:
        from agents.social import mermaid_crew_assistance

        relationship = _wheelchair_relationship_from_message(text)
        note = _canonical_wheelchair_note(relationship)
        fields["accessibility_notes"] = note
        fields["wheelchair_relationship"] = relationship
        fields["phase"] = "collecting"
        mermaid_crew_assistance.record_wheelchair_note(
            phone,
            note=note,
            relationship=relationship,
            trip_date=str(fields.get("trip_date") or ""),
            customer_name=from_name or str(fields.get("customer_name") or ""),
            source_message_id=message_id,
            reservation_public_id=assistance_reservation_id,
        )
        response = "\n\n".join(
            part for part in (
                COPY[locale]["intro"] if not fields.get("introduced") else "",
                WHEELCHAIR_COPY[locale],
                _next_question(fields, locale) or "",
            ) if part
        )
        fields["introduced"] = True
        action = None
    elif explicit_person_request or wheelchair_review:
        fields["phase"] = "human_takeover"
        state_registry.create_pending_notification(
            "escalation", "whatsapp", phone, from_name or fields.get("customer_name") or "Mermaid guest",
            (
                "Mermaid reservation: human requested"
                if explicit_person_request
                else "Mermaid reservation: wheelchair capability review"
            ),
            (
                "The guest requested a person. Intake progress is saved."
                if explicit_person_request
                else "The guest asked about an unconfirmed wheelchair capability. Intake progress is saved."
            ), mode="soft",
            preserve_hard_mode=True,
            suppress_model_summary=True,
        )
        action = "human_takeover"
        response = COPY[locale]["human"]
    elif state_registry.get_active_escalation_mode(phone) in {"soft", "hard"}:
        fields["phase"] = "human_takeover"
        action = None
        response = _question_answer(text, locale) or COPY[locale]["human"]
    elif any(phrase in lower for phrase in ("cancel", "annuleer", "stornieren", "cancelar", "kanselá")):
        fields["phase"] = "cancellation_requested"
        action = "cancel"
        response = ""
    else:
        updates, ambiguous_party = _extract_fields(text, locale, fields)
        for key, value in updates.items():
            fields[key] = value
        if any(key in updates for key in ("adults", "children", "infants")):
            fields.pop("party_size_hint", None)

        if fields.get("trip_date"):
            trip_day = datetime.strptime(fields["trip_date"], "%Y-%m-%d").strftime("%A").casefold()
            operating_days = set(mermaid_catalog.get_catalog()["service"]["operating_weekdays"])
            if trip_day not in operating_days:
                fields.pop("trip_date", None)
                response = COPY[locale]["invalid_day"] + "\n\n" + COPY[locale]["trip_date"]
                action = None
                fields["phase"] = "collecting"
            else:
                response = ""
                action = None
        else:
            response = ""
            action = None

        if not response:
            question_answer = _question_answer(text, locale)
            if fields.get("phase") == "awaiting_summary_confirmation" and lower in YES[locale] and not updates:
                fields["phase"] = "summary_confirmed"
                action = "summary_confirmed"
                response = COPY[locale]["confirmed"]
            else:
                next_question = _next_question(fields, locale)
                if next_question:
                    fields["phase"] = "collecting"
                    response = "\n\n".join(part for part in (question_answer, next_question) if part)
                else:
                    fields["phase"] = "awaiting_summary_confirmation"
                    response = "\n\n".join(part for part in (question_answer, _summary(fields, locale)) if part)
                if ambiguous_party:
                    response = "\n\n".join(part for part in (question_answer, COPY[locale]["composition"]) if part)

        if not fields.get("introduced") and fields.get("phase") == "collecting":
            response = COPY[locale]["intro"] + "\n\n" + response
            fields["introduced"] = True

    root_fields["mermaid_intake"] = fields
    if message_id and action != "cancel":
        seen.append(message_id)
        flags["mermaid_seen_message_ids"] = seen[-100:]
    state_registry.wa_save_booking_state(phone, root_fields, flags, completed)
    return IntakeResult(response, locale, fields.get("phase", "collecting"), action=action)


def _has_guest_question(understood: dict, text: str) -> bool:
    from agents.social.mermaid_understanding import has_guest_question

    return has_guest_question(understood, text)


def process_model_turn(
    message: dict,
    reservation: dict | None,
    *,
    defer_seen: bool = False,
) -> IntakeResult:
    """The single model call understands language; Python validates and owns state."""
    from agents.marina import marina_agent
    from agents.social import mermaid_model_recovery

    phone = str(message.get("from") or "")
    message_id = str(message.get("message_id") or "")
    state = state_registry.wa_get_booking_state(phone)
    root_fields = dict(state.get("fields") or {})
    flags = dict(state.get("flags") or {})
    fields = dict(root_fields.get("mermaid_intake") or {})
    if flags.get(state_registry.MERMAID_LOOP_STOPPED_FLAG) is True:
        bm_logger.log(
            "mermaid_loop_message_suppressed",
            conversation_id=phone[:60],
            message_id=message_id[:60],
        )
        return _stopped_loop_result(state, suppressed=True)
    seen = list(flags.get("mermaid_seen_message_ids") or [])
    if message_id and message_id in seen:
        return IntakeResult("", fields.get("language", "en"), fields.get("phase", "collecting"), duplicate=True)
    if (
        defer_seen
        and message_id
        and flags.get("mermaid_pending_confirmation_message_id") == message_id
        and fields.get("phase") == "summary_confirmed"
    ):
        locale = fields.get("language", "en")
        if locale not in SUPPORTED_LOCALES:
            locale = "en"
        return IntakeResult(
            COPY[locale]["confirmed"],
            locale,
            "summary_confirmed",
            action="summary_confirmed",
            understanding_source="pending_confirmation_retry",
        )
    history = state_registry.dm_get_history(phone, "whatsapp", limit=16)
    session_started_at = str(flags.get("mermaid_session_started_at") or "")
    visible_history = [
        item for item in history
        if not session_started_at
        or str(item.get("created_at") or "") >= session_started_at
    ]
    first_visible_reply = (
        (reservation is None or bool(session_started_at))
        and not any(
            item.get("role") in {"assistant", "operator"}
            for item in visible_history
        )
    )
    review_pending = (
        state_registry.get_active_escalation_mode(phone) in {"soft", "hard"}
        or bool((reservation or {}).get("human_takeover"))
    )
    context = dict(fields)
    context["human_review_pending"] = review_pending
    context["recorded_status"] = response_policy.state_context(phone, reservation)
    context["reservation_state"] = (reservation or {}).get("state")
    recovery = flags.get("mermaid_date_recovery")
    context["date_recovery"] = recovery if recovery and (
        (not reservation and not recovery.get("reservation_public_id")) or
        (reservation and recovery.get("reservation_public_id") == reservation["public_id"] and recovery.get("revision") == reservation["revision"])
    ) else None
    if not reservation and not context['date_recovery'] and fields.get('trip_date'):
        assessed = response_policy.date_recovery(fields['trip_date'], allow_today=True)
        if assessed['reason']:
            context['date_recovery'] = assessed
    from agents.social import mermaid_date_changes
    from agents.social import mermaid_reservation_email
    context['email_offer'] = mermaid_reservation_email.context(reservation)
    context['recorded_status']['email_delivery'] = ((context['email_offer'] or {}).get('last_email') or {}).get('status', 'none')
    proposal = mermaid_date_changes.pending(phone)
    context["pending_date_change"] = {key: proposal[key] for key in ("old_date", "new_date")} if proposal else None
    if reservation:
        context["reservation_intake"] = reservation["intake"]
        context["authoritative_pricing"] = reservation["monetary_snapshot"]
        context["booking_code"] = reservation["booking_code"]
    elif all(key in fields for key in ("adults", "children", "infants")):
        context["authoritative_pricing"] = guest.intake_money(fields)
        context["pickup_offer"] = mermaid_catalog.pickup_quote(sum(fields[key] for key in ("adults", "children", "infants")))
    if fields.get("pickup_preference") == "pickup_requested":
        context["pickup_status"] = (
            "included" if (context.get("authoritative_pricing") or {}).get("pickup_amount") is not None
            else "requested_unconfirmed"
        )
    else:
        context["pickup_status"] = "not_requested"
    missing_fields = [key for key in REQUIRED_FIELDS if key not in fields]
    if "contact_phone" not in missing_fields and not normalize_contact_phone(fields.get("contact_phone")):
        missing_fields.append("contact_phone")
    if fields.get("pickup_preference") == "pickup_requested" and not fields.get("pickup_location"):
        missing_fields.append("pickup_location")
    understood = mermaid_model_recovery.generate(message, fields.get("language", "en"), lambda: marina_agent.process_message(
        from_email=phone, subject="Mermaid WhatsApp reservation demo",
        body=str(message.get("text") or ""), thread_fields=context,
        thread_flags={
            "phase": fields.get("phase", "collecting"),
            "latest_sender_name_untrusted": str(
                message.get("from_name")
                or message.get("_zernio_sender_name")
                or ""
            ),
        },
        action_context=json.dumps({"required_fields": REQUIRED_FIELDS, "missing_fields": missing_fields}),
        channel="whatsapp", messages=visible_history,
        response_contract="mermaid_reservation_demo",
    ))
    locale = understood.get("language")
    if locale not in SUPPORTED_LOCALES:
        locale = fields.get("language", "en")
    if understood.get("generation_failed"):
        return IntakeResult(
            str(understood.get("reply") or ""), locale, fields.get("phase", "collecting"),
            generation_failure=understood["generation_failure"],
            understanding_source="model_failure",
        )
    if understood.get("automation_loop") is True:
        flags[state_registry.MERMAID_LOOP_STOPPED_FLAG] = True
        flags["mermaid_loop_stopped_at"] = datetime.now(timezone.utc).isoformat()
        flags["mermaid_loop_message_id"] = message_id
        if message_id:
            flags["mermaid_seen_message_ids"] = (seen + [message_id])[-100:]
        state_registry.wa_save_booking_state(
            phone,
            root_fields,
            flags,
            state.get("completed_bookings") or [],
        )
        bm_logger.log(
            "mermaid_loop_detected_and_stopped",
            conversation_id=phone[:60],
            message_id=message_id[:60],
        )
        return _stopped_loop_result(
            {"fields": root_fields, "flags": flags},
        )
    guest_text = str(message.get("text") or "")
    from agents.social import mermaid_reply_planning as reply_planning
    date_request = reply_planning.date_request(understood, guest_text)
    if ("other_question_excerpt" in understood or understood.get("status_request") == "wildlife_guarantee"
            or "wildlife_guarantee" in (understood.get("additional_status_requests") or [])):
        excerpt = understood.get("other_question_excerpt")
        if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in guest_text or excerpt == understood.get("date_request_excerpt"):
            understood = {**understood, "other_question_reply": ""}
    explicit_person_request = (
        mermaid_model_recovery.contains_explicit_human_request(guest_text) is not None
    )
    # This provenance is server-owned. Strip any model-supplied metadata and
    # restore it only when the deterministic grammar matched the guest text.
    understood = {
        key: value
        for key, value in understood.items()
        if key != "understanding_source"
    }
    if explicit_person_request:
        understood["understanding_source"] = "explicit_human_request"
    deterministic_general_boarding_assistance = (
        _general_boarding_assistance_message(guest_text)
    )
    deterministic_withdrawal = _wheelchair_withdrawal_message(guest_text)
    deterministic_ordinary_wheelchair = (
        not deterministic_withdrawal and _ordinary_wheelchair_message(guest_text)
    )
    deterministic_wheelchair_review = (
        not deterministic_withdrawal
        and not deterministic_ordinary_wheelchair
        and _wheelchair_capability_requires_review(guest_text)
    )
    deterministic_wheelchair_mention = any(
        marker in _evidence_text(guest_text)
        for marker in _WHEELCHAIR_NOTE_MARKERS
    )
    independent_reservation_intent = _independent_reservation_intent(guest_text)
    if deterministic_general_boarding_assistance:
        neutral_fields = {
            key: value
            for key, value in (understood.get("fields") or {}).items()
            if key
            not in {
                "accessibility_notes",
                "wheelchair_relationship",
                "special_requests",
            }
        }
        understood = {
            **understood,
            "fields": neutral_fields,
            "assistance_request": "none",
            "security_event": (
                understood.get("security_event", "none")
                if _has_security_evidence(guest_text)
                else "none"
            ),
            "requires_human": bool(explicit_person_request),
            "mermaid_action": (
                "request_human"
                if explicit_person_request
                else independent_reservation_intent or "details"
            ),
        }
    elif deterministic_withdrawal:
        understood = {
            **understood,
            "assistance_request": "wheelchair_withdrawal",
            "security_event": (
                understood.get("security_event", "none")
                if _has_security_evidence(guest_text)
                else "none"
            ),
            "requires_human": bool(explicit_person_request),
            "mermaid_action": (
                understood.get("mermaid_action")
                if explicit_person_request
                else "details"
            ),
        }
    elif deterministic_ordinary_wheelchair:
        # The server, rather than a model label, owns the ordinary-wheelchair
        # boundary.  Plain wheelchair information can never manufacture a
        # review, booking decision, or security incident.  Explicit requests
        # for a person remain reviewable and specific capability wording was
        # excluded by _ordinary_wheelchair_message above.
        understood = {
            **understood,
            "assistance_request": "wheelchair_note",
            "security_event": (
                understood.get("security_event", "none")
                if _has_security_evidence(guest_text)
                else "none"
            ),
            "requires_human": bool(explicit_person_request),
            "mermaid_action": (
                "request_human"
                if explicit_person_request
                else (
                    understood.get("mermaid_action")
                    if understood.get("mermaid_action")
                    == independent_reservation_intent
                    else "details"
                )
            ),
        }
    elif deterministic_wheelchair_review:
        # Never trust a model to downgrade a capability, equipment, transfer,
        # medical or safety question to the ordinary note-only path.
        understood = {
            **understood,
            "assistance_request": "other_review",
            "mermaid_action": "request_human",
            "requires_human": True,
        }
    elif deterministic_wheelchair_mention:
        # A policy reference, observation, or other neutral mention is not a
        # statement that this guest or their party uses a wheelchair.  Clear
        # model-invented assistance metadata so a keyword alone cannot create
        # a crew note or a review task.
        neutral_fields = {
            key: value
            for key, value in (understood.get("fields") or {}).items()
            if key not in {"accessibility_notes", "wheelchair_relationship"}
        }
        if any(
            marker in _evidence_text(neutral_fields.get("special_requests", ""))
            for marker in _WHEELCHAIR_NOTE_MARKERS
        ):
            neutral_fields.pop("special_requests", None)
        understood = {
            **understood,
            "fields": neutral_fields,
            "assistance_request": "none",
            "security_event": (
                understood.get("security_event", "none")
                if _has_security_evidence(guest_text)
                else "none"
            ),
            "requires_human": bool(explicit_person_request),
            "mermaid_action": (
                understood.get("mermaid_action")
                if independent_reservation_intent is not None
                and understood.get("mermaid_action")
                == independent_reservation_intent
                else ("question" if "?" in guest_text else "acknowledge")
            ),
        }
    if explicit_person_request:
        understood = {
            **understood,
            "mermaid_action": "request_human",
            "requires_human": True,
        }
    security_event = understood.get("security_event", "none")
    security_review = False
    if security_event in {"blocked_override", "actionable_incident"}:
        security_review = response_policy.record_security_event(phone, message_id, security_event)
        # A rejected instruction cannot supply booking changes. An independent
        # explicit person request still gets the ordinary durable review.
        explicit_human = understood.get("mermaid_action") == "request_human"
        understood = {**understood, "fields": {},
                      "mermaid_action": "request_human" if security_review or explicit_human else "acknowledge",
                      "requires_human": security_review or explicit_human}
    calendar_request = understood.get("calendar_request", "none")
    if calendar_request in response_policy.CALENDAR_REQUESTS or date_request:
        understood = {**understood, "fields": {k: v for k, v in (understood.get("fields") or {}).items() if k != "trip_date"}}
    action = understood.get("mermaid_action")
    if action in {"change_date", "confirm_date_change", "keep_date"} and reservation and mermaid_date_changes.enabled():
        if action == "change_date":
            faq = reply_planning.supported_faq(understood, str(message.get("text") or ""))
            response = mermaid_date_changes.propose(message, reservation, (understood.get("fields") or {}).get("trip_date"), locale, faq_reply=faq)
        elif proposal and not _has_guest_question(understood, str(message.get("text") or "")):
            response = mermaid_date_changes.handle_button({**message, "_zernio_interactive_id": mermaid_date_changes.PREFIX + proposal["token"] + (":confirm" if action == "confirm_date_change" else ":keep")}, email_followup=understood.get('email_action','none') not in mermaid_reservation_email.ACTIONS)
        else:
            response = {"text": mermaid_date_changes.copy(locale)["stale"], "media": None}
        if security_event == 'none' and understood.get('email_action', 'none') in mermaid_reservation_email.ACTIONS:
            from agents.social import mermaid_reservation_store
            email_reply = mermaid_reservation_email.handle(message, mermaid_reservation_store.get_reservation(reservation['public_id']), understood, locale, wait_for_date=action=='change_date')
            response['text'] = reply_planning.join_answers(email_reply, response['text'])
        return IntakeResult(response["text"], locale, reservation["state"], action="date_change", workflow_reply=response)
    if security_event == 'none' and action not in {'cancel','request_human','new_booking'}:
        email_reply = mermaid_reservation_email.handle(message, reservation, understood, locale)
        if email_reply is not None:
            return IntakeResult(reply_planning.join_answers(reply_planning.supported_faq(understood, guest_text), email_reply), locale, (reservation or {}).get('state',fields.get('phase','collecting')), action='reservation_email')
    if action not in {"details", "question", "confirm_summary", "cancel", "request_human", "payment_status", "new_booking", "acknowledge"}:
        return IntakeResult(str(understood.get("reply") or COPY[locale]["trip_date"]), locale, fields.get("phase", "collecting"))
    if not review_pending and action == "new_booking" and (reservation or {}).get("state") in {"booked", "cancelled"}:
        fields = {}
        flags.pop("mermaid_date_recovery", None)
        generation_source = str(message_id or "")
        if (
            not generation_source
            or flags.get("mermaid_session_source_message_id") != generation_source
            or not flags.get("mermaid_session_started_at")
        ):
            flags["mermaid_session_started_at"] = datetime.now(
                timezone.utc
            ).isoformat()
        if generation_source:
            flags["mermaid_session_source_message_id"] = generation_source
    old_phase = fields.get("phase", "collecting")
    fields["language"] = locale
    changes = {}
    invalid_contact = False
    invalid_date = None
    for key, value in (understood.get("fields") or {}).items():
        if key in {"adults", "children", "infants"}:
            if type(value) is int and 0 <= value <= 100:
                changes[key] = value
        elif key == "trip_date" and isinstance(value, str):
            assessed = response_policy.date_recovery(value, allow_today=True)
            if not assessed['reason']:
                changes[key] = assessed['requested_date']
            elif (not reservation or action == 'new_booking') and not review_pending:
                invalid_date = assessed
        elif key == "pickup_preference" and value in {"pier", "pickup_requested"}:
            changes[key] = value
        elif key == "contact_phone":
            contact = normalize_contact_phone(value)
            if contact:
                changes[key] = contact
            else:
                invalid_contact = True
        elif key in {"customer_name", "pickup_location", "dietary_requirements", "accessibility_notes", "special_requests"} and isinstance(value, str):
            cleaned = " ".join(value.split())[:160]
            if cleaned:
                changes[key] = cleaned
        elif key == "wheelchair_relationship" and value in {"husband", "other", "unspecified"}:
            changes[key] = value
    from shared.mermaid_guest_ages import normalize_child_ages, age_band
    supplied = understood.get("fields") or {}
    if "child_ages" in supplied:
        ages = normalize_child_ages(supplied["child_ages"], {**fields, **changes})
        if ages is not None:
            changes["child_ages"] = ages
        elif action == "confirm_summary":
            # Invalid age corrections cannot approve an unchanged summary.
            action = "details"
    elif fields.get("child_ages"):
        # A reduced group does not establish which child's age remains valid.
        reduced = {key for key in ("adults", "children", "infants")
                   if key in changes and key in fields and changes[key] < fields[key]}
        if reduced:
            changes["child_ages"] = [age for age in fields["child_ages"] if age_band(age) not in reduced]
    changes = {key: value for key, value in changes.items() if fields.get(key) != value}
    fields.update(changes)
    if invalid_date:
        fields.pop('trip_date', None)
        flags['mermaid_date_recovery'] = invalid_date
    elif 'trip_date' in changes:
        flags.pop('mermaid_date_recovery', None)
    if date_request and (not reservation or action == "new_booking") and security_event == "none":
        fields.pop("trip_date", None)
        fields["date_selection"] = (
            "deferred" if date_request == "defer" or fields.get("date_selection") in {"undecided", "deferred"}
            else "undecided"
        )
    elif fields.get("trip_date"):
        fields.pop("date_selection", None)
    if fields.get("child_ages") == []:
        fields.pop("child_ages")
    if invalid_contact and not reservation:
        fields.pop("contact_phone", None)
    if fields.get("pickup_preference") == "pier":
        fields.pop("pickup_location", None)
    assistance_request = understood.get("assistance_request", "none")
    general_boarding_assistance = bool(
        deterministic_general_boarding_assistance
        and security_event == "none"
        and not explicit_person_request
    )
    if (
        assistance_request == "wheelchair_note"
        and action == "request_human"
        and not explicit_person_request
    ):
        # Ordinary wheelchair use is an owner-approved booking detail.  A
        # model-generated request_human action cannot silently turn it back
        # into a review.  The durable recovery route labels real, explicit
        # person requests so those still take the human-review path.
        action = "details"
        understood = {**understood, "mermaid_action": action}
    wheelchair_withdrawal = (
        security_event == "none"
        and assistance_request == "wheelchair_withdrawal"
    )
    if wheelchair_withdrawal:
        fields.pop("accessibility_notes", None)
        fields.pop("wheelchair_relationship", None)
        changes.pop("accessibility_notes", None)
        changes.pop("wheelchair_relationship", None)
        if action == "request_human" and not explicit_person_request:
            action = "details"
            understood = {**understood, "mermaid_action": action}
        if not explicit_person_request:
            understood = {**understood, "requires_human": False}
    if assistance_request == "other_review":
        # The server owns the boundary between routine wheelchair notes and
        # an unconfirmed equipment, transfer, medical, or safety capability.
        understood = {**understood, "requires_human": True}
    accessibility_note = str(fields.get("accessibility_notes") or "")
    legacy_wheelchair_note = (
        assistance_request in {None, "none"}
        and "accessibility_notes" in changes
        and any(marker in accessibility_note.casefold() for marker in _WHEELCHAIR_NOTE_MARKERS)
    )
    wheelchair_note = (
        security_event == "none"
        and (
            action in {"details", "question", "acknowledge", "new_booking"}
            or (action == "request_human" and explicit_person_request)
        )
        and (assistance_request == "wheelchair_note" or legacy_wheelchair_note)
    )
    assistance_overlay = {}
    if wheelchair_note:
        if not accessibility_note:
            accessibility_note = "A guest in this party uses a wheelchair and may need general crew assistance."
            fields["accessibility_notes"] = accessibility_note
            changes["accessibility_notes"] = accessibility_note
        relationship = fields.get("wheelchair_relationship")
        if relationship not in {"husband", "other", "unspecified"}:
            relationship = _wheelchair_relationship_from_message(guest_text)
            fields["wheelchair_relationship"] = relationship
            changes["wheelchair_relationship"] = relationship
        accessibility_note = _canonical_wheelchair_note(relationship)
        if fields.get("accessibility_notes") != accessibility_note:
            fields["accessibility_notes"] = accessibility_note
            changes["accessibility_notes"] = accessibility_note
        assistance_overlay = {
            "accessibility_notes": accessibility_note,
            "wheelchair_relationship": relationship,
        }
        # The model used to mark every accessibility mention as human review.
        # This server-owned route enforces the owner's ordinary-wheelchair rule.
        understood = {
            **understood,
            "requires_human": bool(explicit_person_request),
        }
    response = str(understood.get("reply") or "").strip()
    has_question = _has_guest_question(understood, str(message.get("text") or ""))
    has_question = has_question or bool(understood.get("additional_status_requests"))
    has_question = has_question or calendar_request in response_policy.CALENDAR_REQUESTS or understood.get("status_request", "none") not in {"none", "pickup_pricing"} or security_event in {"blocked_override", "actionable_incident"}
    if invalid_contact and not reservation and not review_pending:
        response = guest.guest_copy(locale)["contact_phone_retry"]
        has_question = True
    if wheelchair_note or wheelchair_withdrawal or general_boarding_assistance:
        # The deterministic acknowledgement answers the wheelchair question;
        # normal intake should immediately choose the next missing detail.
        response = ""
        # Require a separate supported FAQ topic and verbatim guest evidence.
        # Exact-copy removal cannot catch paraphrases of the crew acknowledgement.
        faq_topics = {"food", "inclusions", "activities", "preparation", "pier", "parking", "travel_time", "contact"}
        excerpt = understood.get("other_question_excerpt")
        separate_faq = (
            understood.get("other_question_topic") in faq_topics
            and isinstance(excerpt, str) and bool(excerpt.strip())
            and excerpt in str(message.get("text") or "")
        )
        understood = {**understood, "other_question_reply": str(understood.get("other_question_reply") or "").strip() if separate_faq else ""}
        has_question = bool(understood.get("other_question_reply") or understood.get("additional_status_requests")) or calendar_request in response_policy.CALENDAR_REQUESTS or understood.get("status_request", "none") != "none"
    if date_request and (not reservation or action == "new_booking") and not review_pending:
        response = ""
        has_question = True
    result_action = None
    canonical_response = False
    natural_payment_approval = (
        old_phase == "awaiting_summary_confirmation"
        and not changes
        and not invalid_contact
        and understood.get("status_request") == "payment"
        and action in {"confirm_summary", "payment_status", "question", "acknowledge"}
        and _has_natural_approval(str(message.get("text") or ""), locale)
    )
    pickup_review = (
        security_event not in {"blocked_override", "actionable_incident"}
        and (not reservation or action == "new_booking")
        and action != "cancel"
        and fields.get("pickup_preference") == "pickup_requested"
        and mermaid_catalog.pickup_quote(sum(fields.get(key, 0) for key in ("adults", "children", "infants")))["status"] == "requires_review"
    )
    if pickup_review and action != "request_human":
        response = guest.guest_copy(locale)["pickup_requires_review"]
    unpaid_cancellation = (
        action == "cancel" and reservation
        and reservation["state"] not in {"booked", "demo_paid"}
        and not review_pending
    )
    if pickup_review or (understood.get("requires_human") and not unpaid_cancellation) or action == "request_human" or (
        action == "cancel" and (reservation or {}).get("state") in {"booked", "demo_paid"}
    ):
        if security_event not in {"blocked_override", "actionable_incident"} or not review_pending:
            state_registry.create_pending_notification(
                "escalation", "whatsapp", phone,
                str(message.get("from_name") or fields.get("customer_name") or "Mermaid guest"),
                "Mermaid reservation: human review", "Reservation progress is saved for the team.", mode="soft",
                preserve_hard_mode=True,
                suppress_model_summary=understood.get("understanding_source") == "explicit_human_request",
            )
        fields["phase"] = "human_takeover"
        if action in {"confirm_summary", "new_booking", "cancel"} and not pickup_review:
            response = COPY[locale]["human"]
        else:
            response = response or COPY[locale]["human"]
        result_action = "human_takeover"
    elif review_pending:
        # Human review freezes booking decisions, not safe conversation. Only
        # an operator resolves the work item; an existing reservation stays
        # frozen independently even after that conversation item is resolved.
        fields["phase"] = "human_takeover"
        if action in {"confirm_summary", "new_booking", "cancel"} or not response:
            response = COPY[locale]["human"]
    elif action == "cancel":
        fields["phase"] = "cancellation_requested"
        response = ""
        result_action = "cancel"
    elif reservation and reservation["state"] in {"demo_payment_pending", "booked", "cancelled"} and action != "new_booking":
        # Answers after a quote never reopen intake or change the immutable quote.
        fields = dict(root_fields.get("mermaid_intake") or fields)
        fields.update(assistance_overlay)
        fields["language"] = locale
        fields["phase"] = reservation["state"]
        result_action = "payment_status" if action == "payment_status" else None
        if reservation["state"] == "cancelled" and action == "confirm_summary":
            response = COPY[locale]["cancelled"]
        elif action == "confirm_summary" and not has_question:
            response = response_policy.status_reply("payment", locale, response_policy.state_context(phone, reservation))
    else:
        if fields.get("trip_date"):
            assessed = response_policy.date_recovery(fields['trip_date'], allow_today=True)
            if assessed['reason']:
                invalid_date = assessed
                fields.pop("trip_date")
                flags['mermaid_date_recovery'] = assessed
        question = _next_question(fields, locale)
        if invalid_date:
            fields['phase'] = 'collecting'
            response = response_policy.date_recovery_reply(invalid_date, locale)
        elif invalid_contact:
            fields["phase"] = "collecting"
        elif question:
            fields["phase"] = "collecting"
            if not response or action == "confirm_summary":
                response = question
        elif natural_payment_approval:
            # A guest can approve the displayed details and naturally ask how
            # to pay in the same sentence. The quote and checkout answer that
            # procedural question, so do not force a second artificial YES.
            fields["phase"] = "summary_confirmed"
            response = COPY[locale]["confirmed"]
            result_action = "summary_confirmed"
            canonical_response = True
        elif has_question:
            # A mixed detail/question turn must keep its answer. Changed facts
            # require a fresh summary before any later approval can book them.
            fields["phase"] = old_phase if old_phase == "awaiting_summary_confirmation" and not changes else "collecting"
        elif action == "confirm_summary" and old_phase == "awaiting_summary_confirmation" and not changes:
            fields["phase"] = "summary_confirmed"
            response = COPY[locale]["confirmed"]
            result_action = "summary_confirmed"
            canonical_response = True
        else:
            fields["phase"] = "awaiting_summary_confirmation"
            response = _summary(fields, locale)
            canonical_response = True
    # These critical facts come from records/catalog, never generated prose.
    if security_event in {"blocked_override", "actionable_incident"}:
        # A refused instruction cannot change a draft, validate a previously
        # invalid date, or make a never-shown summary eligible for approval.
        fields = dict(root_fields.get('mermaid_intake') or {})
        fields['language'] = locale
        fields.setdefault('phase', 'collecting')
        if result_action == 'human_takeover':
            fields['phase'] = 'human_takeover'
        else:
            result_action = None
        response = response_policy.copy('security_blocked', locale)
        if result_action == 'human_takeover':
            response += '\n\n' + response_policy.copy('review_queued', locale)
    elif result_action == 'human_takeover' and not (
        review_pending and action in {'question', 'details', 'acknowledge'} and not pickup_review
    ):
        # A repeated review flag keeps its freeze/dedup action, while an
        # ordinary follow-up continues to the protected fact/FAQ renderers.
        response = response_policy.copy('review_queued', locale)
    elif result_action == 'cancel':
        # The handler commits cancellation (or replaces this with paid-review
        # wording). An optional informational selector cannot conceal that result.
        pass
    elif canonical_response:
        # Volunteered pickup facts must not hide the canonical summary or its
        # one approval result. Review-pending paths never produce this flag.
        pass
    elif review_pending and action in {'confirm_summary', 'new_booking', 'cancel'}:
        # Informational selectors cannot conceal a review-blocked decision.
        pass
    elif invalid_date:
        response = response_policy.date_recovery_reply(invalid_date, locale)
    elif calendar_request in response_policy.CALENDAR_REQUESTS:
        response = response_policy.calendar_reply(calendar_request, locale)
    elif understood.get('status_request') == 'wildlife_guarantee' and not (
        review_pending and action in {'confirm_summary', 'new_booking', 'cancel'}
    ):
        response = response_policy.wildlife_guarantee_reply(locale, response_policy.state_context(phone, reservation))
    elif understood.get('status_request') == 'pickup_pricing' and not (
        review_pending and action in {'confirm_summary', 'new_booking', 'cancel'}
    ):
        response = response_policy.pickup_pricing_reply(
            locale, fields, None if action == 'new_booking' and not review_pending else reservation)
        other_answer = str(understood.get('other_question_reply') or '').strip()
        if other_answer:
            response += '\n\n' + other_answer
    elif understood.get('status_request') == 'pickup_coverage':
        response = response_policy.pickup_coverage_reply(locale)
    elif understood.get('status_request') in {'payment', 'handover', 'delivery'} or action == 'payment_status':
        response = response_policy.status_reply(understood.get('status_request') if understood.get('status_request') in {'payment', 'handover', 'delivery'} else 'payment', locale, response_policy.state_context(phone, reservation))
        other_answer = str(understood.get('other_question_reply') or '').strip()
        if other_answer:
            response = other_answer + '\n\n' + response
    elif review_pending:
        # A pending staff task does not make every follow-up a status enquiry.
        # Answer an evidenced ordinary FAQ without reciting the queue again.
        other_answer = reply_planning.supported_faq(understood, guest_text)
        understood = {**understood, 'other_question_reply': other_answer}
        response = other_answer or response_policy.status_reply('handover', locale, response_policy.state_context(phone, reservation))
    # Calendar and status are independent concerns, as are multiple protected
    # questions in one turn. A single primary selector must not discard them.
    decision_blocked = review_pending and action in {"confirm_summary", "new_booking", "cancel"}
    if not canonical_response and result_action != "cancel" and not decision_blocked and security_event == "none":
        requests = list(understood.get("additional_status_requests") or [])
        if calendar_request in response_policy.CALENDAR_REQUESTS or invalid_date:
            requests.insert(0, understood.get("status_request", "none"))
        rendered = set() if calendar_request in response_policy.CALENDAR_REQUESTS or invalid_date else {understood.get("status_request")}
        for request in requests:
            if request in rendered or request == "none":
                continue
            rendered.add(request)
            if request == "pickup_pricing":
                answer = response_policy.pickup_pricing_reply(locale, fields, None if action == "new_booking" and not review_pending else reservation)
            elif request == "pickup_coverage":
                answer = response_policy.pickup_coverage_reply(locale)
            elif request == "wildlife_guarantee":
                answer = response_policy.wildlife_guarantee_reply(locale, response_policy.state_context(phone, reservation))
            else:
                answer = response_policy.status_reply(request, locale, response_policy.state_context(phone, reservation))
            response = reply_planning.join_answers(response, answer)
    # All protected selectors may share a turn with a separate, supported FAQ.
    # Preserve that answer without reusing discarded model claims. Decisions
    # (payment approval, cancellation, security) retain their strict priority.
    if not canonical_response and result_action != "cancel" and not decision_blocked and security_event == "none":
        protected = (calendar_request in response_policy.CALENDAR_REQUESTS
                     or understood.get("status_request", "none") != "none"
                     or review_pending or result_action == "human_takeover"
                     or wheelchair_note or wheelchair_withdrawal
                     or general_boarding_assistance or date_request or invalid_date
                     or 'trip_date' in supplied or context.get('date_recovery')
                     or understood.get("additional_status_requests"))
        if protected:
            other_answer = str(understood.get("other_question_reply") or "").strip()
            if invalid_date or 'trip_date' in supplied or context.get('date_recovery'):
                other_answer = reply_planning.supported_faq(understood, guest_text)
            # Old wildlife responses put the guarantee itself in the FAQ
            # slot. Require independent guest evidence before appending it
            # to the authoritative guarantee, which already answers that.
            if understood.get("status_request") == "wildlife_guarantee" or "wildlife_guarantee" in (understood.get("additional_status_requests") or []) or result_action == "human_takeover":
                excerpt = understood.get("other_question_excerpt")
                if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in guest_text:
                    other_answer = ""
            response = reply_planning.join_answers(other_answer, response)
    if date_request and (not reservation or action == "new_booking") and not review_pending and not result_action and security_event == "none":
        if fields.get("date_selection") == "deferred":
            response = reply_planning.join_answers(reply_planning.DATE_COPY[locale][1], response)
    party_ack = ""
    if (wheelchair_note or general_boarding_assistance or date_request) and not canonical_response and (not reservation or action == "new_booking") and security_event == "none":
        if any(key in changes for key in ("adults", "children", "infants", "child_ages")) and all(key in fields for key in ("adults", "children", "infants")):
            party_ack = reply_planning.PARTY_COPY[locale].format(party=guest.party_text(fields, locale))
    if party_ack:
        response = reply_planning.join_answers(party_ack, response)
    if wheelchair_withdrawal:
        # Existing quote/booking branches restore the authoritative intake for
        # money and status.  Re-apply only this private-field deletion so the
        # corrected booking state cannot resurrect a withdrawn crew note.
        fields.pop("accessibility_notes", None)
        fields.pop("wheelchair_relationship", None)
    if general_boarding_assistance:
        # The guest asked for ordinary help without stating wheelchair use.
        # Preserve exactly that supported fact in the private crew queue before
        # claiming that the request was recorded.
        from agents.social import mermaid_crew_assistance

        root_fields["mermaid_intake"] = fields
        state_registry.wa_save_booking_state(
            phone, root_fields, flags, state.get("completed_bookings") or []
        )
        relationship = _wheelchair_relationship_from_message(guest_text)
        attention, _outcome = mermaid_crew_assistance.record_boarding_assistance_note(
            phone,
            note=_canonical_boarding_assistance_note(relationship),
            relationship=relationship,
            trip_date=fields.get("trip_date") or "",
            customer_name=str(
                fields.get("customer_name") or message.get("from_name") or ""
            ),
            source_message_id=message_id,
            reservation_public_id=(
                str(reservation["public_id"])
                if reservation and action != "new_booking"
                else ""
            ),
        )
        if reservation and action != "new_booking":
            attention = mermaid_crew_assistance.link_current(
                phone,
                reservation["public_id"],
                idempotency_key=f"boarding-assistance:{message_id or phone}",
            ) or attention
        response = "\n\n".join(
            part
            for part in (
                WELCOME_COPY[locale] if first_visible_reply else "",
                BOARDING_ASSISTANCE_COPY[locale],
                response.strip(),
            )
            if part
        )
    elif wheelchair_note:
        # Persist both the intake fact and the staff-only attention item before
        # telling the guest that the crew note is saved. Do not mark the provider
        # event seen until every required write succeeds, so recovery can retry.
        from agents.social import mermaid_crew_assistance

        root_fields["mermaid_intake"] = fields
        state_registry.wa_save_booking_state(
            phone, root_fields, flags, state.get("completed_bookings") or []
        )
        attention, _outcome = mermaid_crew_assistance.record_wheelchair_note(
            phone,
            note=fields["accessibility_notes"],
            relationship=fields.get("wheelchair_relationship") or "",
            trip_date=fields.get("trip_date") or "",
            customer_name=str(fields.get("customer_name") or message.get("from_name") or ""),
            source_message_id=message_id,
            reservation_public_id=(
                str(reservation["public_id"])
                if reservation and action != "new_booking"
                else ""
            ),
        )
        if reservation and action != "new_booking":
            attention = mermaid_crew_assistance.link_current(
                phone,
                reservation["public_id"],
                idempotency_key=f"wheelchair:{message_id or phone}",
            ) or attention
        other_answer = str(understood.get("other_question_reply") or "").strip()
        continuation = response.strip()
        if other_answer and other_answer not in continuation:
            continuation = "\n\n".join(part for part in (other_answer, continuation) if part)
        response = "\n\n".join(part for part in (
            WELCOME_COPY[locale] if first_visible_reply else "",
            WHEELCHAIR_COPY[locale],
            continuation,
        ) if part)
    elif wheelchair_withdrawal:
        # Persist the corrected intake before removing the staff task, and do
        # not claim removal until the withdrawal transition has committed.
        from agents.social import mermaid_crew_assistance

        root_fields["mermaid_intake"] = fields
        state_registry.wa_save_booking_state(
            phone, root_fields, flags, state.get("completed_bookings") or []
        )
        existing_attention = mermaid_crew_assistance.for_conversation(
            phone, kind=mermaid_crew_assistance.KIND_WHEELCHAIR
        )
        had_active_wheelchair_note = bool(
            existing_attention
            and existing_attention.get("kind") == "wheelchair"
            and existing_attention.get("status") != "withdrawn"
        )
        withdrawn = mermaid_crew_assistance.withdraw(
            phone,
            source_message_id=message_id,
        )
        acknowledgement = (
            WHEELCHAIR_WITHDRAWAL_COPY[locale]
            if had_active_wheelchair_note and withdrawn is not None
            else NO_WHEELCHAIR_NOTE_COPY[locale]
        )
        response = "\n\n".join(
            part for part in (acknowledgement, response.strip()) if part
        )
    elif any(key in changes or key in supplied for key in (
        "trip_date", "customer_name", "accessibility_notes", "wheelchair_relationship"
    )):
        # Later corrections keep private crew markers current within this
        # booking generation.
        from agents.social import mermaid_crew_assistance

        existing_attention = mermaid_crew_assistance.for_conversation(
            phone, kind=mermaid_crew_assistance.KIND_WHEELCHAIR
        )
        current_intake_owns_attention = bool(
            fields.get("accessibility_notes")
            and fields.get("wheelchair_relationship")
        )
        if existing_attention is not None and current_intake_owns_attention:
            root_fields["mermaid_intake"] = fields
            state_registry.wa_save_booking_state(
                phone, root_fields, flags, state.get("completed_bookings") or []
            )
            mermaid_crew_assistance.sync_existing(
                phone,
                note=fields.get("accessibility_notes"),
                relationship=fields.get("wheelchair_relationship"),
                trip_date=fields.get("trip_date"),
                customer_name=str(fields.get("customer_name") or message.get("from_name") or ""),
                source_message_id=message_id,
            )
        if any(
            key in changes or key in supplied
            for key in ("trip_date", "customer_name")
        ):
            session_started_at = str(
                flags.get("mermaid_session_started_at") or ""
            )
            boarding_attention = mermaid_crew_assistance.for_conversation(
                phone,
                kind=mermaid_crew_assistance.KIND_BOARDING_ASSISTANCE,
                session_started_at=session_started_at,
            )
            if boarding_attention is not None:
                mermaid_crew_assistance.sync_existing(
                    phone,
                    kind=mermaid_crew_assistance.KIND_BOARDING_ASSISTANCE,
                    trip_date=fields.get("trip_date"),
                    customer_name=str(
                        fields.get("customer_name")
                        or message.get("from_name")
                        or ""
                    ),
                    source_message_id=(
                        f"{message_id}:boarding-assistance-sync"
                        if message_id
                        else ""
                    ),
                )
    root_fields["mermaid_intake"] = fields
    if defer_seen and message_id and result_action == "summary_confirmed":
        flags["mermaid_pending_confirmation_message_id"] = message_id
    elif message_id and result_action != "cancel" and not defer_seen:
        flags["mermaid_seen_message_ids"] = (seen + [message_id])[-100:]
    state_registry.wa_save_booking_state(phone, root_fields, flags, state.get("completed_bookings") or [])
    return IntakeResult(response, locale, fields["phase"], action=result_action,
                        understanding_source=understood.get("understanding_source", "model"))


def handle_demo_message(message: dict, include_media: bool = False, *, use_model: bool = False) -> str | dict:
    # Customer prose is never payment evidence. Only the signed callback below
    # can mutate payment state.
    from agents.social import mermaid_reservation_store as _reservation_store
    phone = str(message.get("from") or "")
    current = _reservation_store.latest_for_conversation(phone)
    if use_model:
        state = state_registry.wa_get_booking_state(phone)
        flags = state.get("flags") or {}
        if flags.get(state_registry.MERMAID_LOOP_STOPPED_FLAG) is True:
            bm_logger.log(
                "mermaid_loop_message_suppressed",
                conversation_id=phone[:60],
                message_id=str(message.get("message_id") or "")[:60],
            )
            result = _stopped_loop_result(state, suppressed=True)
            return result.as_reply() if include_media else ""
        from agents.social import mermaid_date_changes
        button_reply = mermaid_date_changes.handle_button(message)
        if button_reply is not None:
            _cache_reply(message, button_reply)
            return button_reply if include_media else str(button_reply.get("text") or "")
        cached = (flags.get("mermaid_cached_replies") or {}).get(str(message.get("message_id") or "")) or flags.get("mermaid_cached_reply") or {}
        if (message.get("message_id") and cached.get("message_id") == message["message_id"]
                and message["message_id"] in flags.get("mermaid_seen_message_ids", [])):
            reply = dict(cached.get("reply") or {})
            commit = reply.get("mermaid_delivery_commit") or {}
            if commit:
                from agents.social.mermaid_documents import delivery_job
                if (delivery_job(commit["job_id"]) or {}).get("status") == "delivered":
                    return IntakeResult("", current["language"] if current else "en", "duplicate", duplicate=True).as_reply() if include_media else ""
            reply["duplicate"] = True
            return reply if include_media else str(reply.get("text") or "")
    lower = str(message.get("text") or "").casefold()
    if not use_model and current and current["state"] == "demo_payment_pending" and any(
        phrase in lower for phrase in ("i paid", "paid", "betaald", "bezahlt", "pagué", "paguei", "mi a paga")
    ):
        text = (
            "Thanks. A WhatsApp message cannot verify payment. Please complete the payment link; "
            "your booking will finish once payment is confirmed."
        )
        if include_media:
            return IntakeResult(text, current["language"], "demo_payment_pending").as_reply()
        return text
    result = process_model_turn(message, current, defer_seen=True) if use_model else process_intake_turn(
        str(message.get("from") or ""),
        str(message.get("text") or ""),
        message_id=str(message.get("message_id") or ""),
        from_name=str(message.get("from_name") or ""),
        reservation=current,
    )
    if result.generation_failure is not None:
        # An outage notice is not a completed model decision or cached answer.
        return result.as_reply() if include_media else result.text
    if result.action == "summary_confirmed":
        from agents.social import (
            mermaid_demo_payment, mermaid_documents, mermaid_reservation_store,
        )

        state = state_registry.wa_get_booking_state(str(message.get("from") or ""))
        intake = (state.get("fields") or {}).get("mermaid_intake") or {}
        reservation = mermaid_reservation_store.confirm_reservation(
            str(message.get("from") or ""),
            intake,
            idempotency_key=(
                "confirm:" + str(message.get("message_id") or "")
                if message.get("message_id") else "confirm:" + str(message.get("from") or "")
            ),
            zernio_account_id=str(message.get("_zernio_account_id") or ""),
            assistance_session_owned=True,
        )
        document, job = mermaid_documents.create_quote(reservation)
        reservation = mermaid_reservation_store.transition(
            reservation["public_id"], "quote_ready",
            idempotency_key=f"quote-ready:{reservation['public_id']}",
            actor="system", reason="Localized demo quote rendered",
            updates={"quote_public_id": document["public_id"]},
        )
        base_url = __import__("os").environ.get("UNBOKS_PUBLIC_BASE_URL", "http://localhost:8001")
        secret = __import__("os").environ.get("MERMAID_DEMO_SIGNING_SECRET", "")
        media = {
            "url": mermaid_documents.build_signed_url(base_url, document["public_id"], secret),
            "type": "file", "filename": document["filename"], "id": document["public_id"],
        }
        reservation = mermaid_reservation_store.transition(
            reservation["public_id"], "demo_payment_pending",
            idempotency_key=f"payment-pending:{reservation['public_id']}",
            actor="system", reason="No-money demo checkout created",
        )
        payment_url = mermaid_demo_payment.build_payment_url(
            mermaid_catalog.get_catalog().get("links", {}).get("checkout_base_url") or base_url,
            reservation["public_id"], secret
        )
        availability_copy = PAYMENT_COPY[result.locale][0]
        payment_copy = guest.guest_copy(result.locale)["checkout_link"]
        result = IntakeResult(
            "\n\n".join(part for part in (mermaid_documents.quote_message(reservation), availability_copy, payment_copy + "\n" + payment_url) if part),
            result.locale,
            result.phase,
            action=f"reservation:{reservation['public_id']}",
        )
        if include_media:
            reply = result.as_reply()
            reply["media"] = media
            reply["mermaid_delivery_commit"] = {"job_id": job["public_id"]}
            if use_model:
                _cache_reply(message, reply)
            return reply
    elif result.action == "payment_status" and current:
        from agents.social import mermaid_demo_payment
        import os
        # A signed payment may finish while the model is composing its answer.
        # Offer checkout only from the current state, not the earlier snapshot.
        current = _reservation_store.get_reservation(current["public_id"])
        if current and current["state"] == "demo_payment_pending" and not current["human_takeover"]:
            url = mermaid_demo_payment.build_payment_url(
                mermaid_catalog.get_catalog().get("links", {}).get("checkout_base_url") or os.environ.get("UNBOKS_PUBLIC_BASE_URL", "http://localhost:8001"),
                current["public_id"], os.environ.get("MERMAID_DEMO_SIGNING_SECRET", ""),
            )
            result = IntakeResult(result.text + "\n\n" + guest.guest_copy(result.locale)["checkout_link"] + "\n" + url, result.locale, result.phase)
    elif result.action == "cancel":
        from agents.social import mermaid_reservation_store

        current = mermaid_reservation_store.latest_for_conversation(str(message.get("from") or ""))
        needs_review = state_registry.get_active_escalation_mode(phone) in {"soft", "hard"} or state_registry.get_ai_muted(phone)
        if current and not needs_review:
            try:
                mermaid_reservation_store.cancel(
                    current["public_id"],
                    idempotency_key="cancel:" + str(message.get("message_id") or current["public_id"]),
                )
            except mermaid_reservation_store.MermaidCancellationReviewRequired:
                needs_review = True
        if needs_review:
            state_registry.create_pending_notification(
                "escalation", "whatsapp", phone,
                str(message.get("from_name") or (current or {}).get("customer_name") or "Mermaid guest"),
                "Mermaid reservation: cancellation review", "Cancellation requires review; the reservation was not cancelled.",
                mode="soft", preserve_hard_mode=True,
            )
            if current:
                mermaid_reservation_store.freeze_for_human(current["public_id"])
            result = IntakeResult(COPY[result.locale]["human"], result.locale, "human_takeover", action="human_takeover")
        else:
            result = IntakeResult(COPY[result.locale]["cancelled"], result.locale, "cancelled", action="cancel")
        # An attempted cancellation remains retryable until its authoritative
        # state change or required review succeeds. The reply cache commits the
        # provider-event marker only after those operations complete.
        state = state_registry.wa_get_booking_state(phone)
        fields = dict(state.get("fields") or {})
        fields["mermaid_intake"] = dict(fields.get("mermaid_intake") or {}) | {"phase": result.phase}
        flags = dict(state.get("flags") or {})
        state_registry.wa_save_booking_state(phone, fields, flags, state.get("completed_bookings") or [])
    elif result.action == "human_takeover":
        from agents.social import mermaid_reservation_store

        current = mermaid_reservation_store.latest_for_conversation(str(message.get("from") or ""))
        if current:
            mermaid_reservation_store.freeze_for_human(current["public_id"])
    if use_model and result.action != "loop_stopped":
        _cache_reply(message, result.as_reply())
    return result.as_reply() if include_media else result.text


def _cache_reply(message: dict, reply: dict) -> None:
    """Keep the exact payload for the provider's stable idempotent retry."""
    if not message.get("message_id"):
        return
    phone = str(message.get("from") or "")
    state = state_registry.wa_get_booking_state(phone)
    flags = dict(state.get("flags") or {})
    flags["mermaid_cached_reply"] = {"message_id": message["message_id"], "reply": reply}
    cached = dict(flags.get("mermaid_cached_replies") or {})
    cached[str(message["message_id"])] = flags["mermaid_cached_reply"]
    flags["mermaid_cached_replies"] = dict(list(cached.items())[-10:])
    message_id = str(message["message_id"])
    flags["mermaid_seen_message_ids"] = (
        list(flags.get("mermaid_seen_message_ids") or []) + [message_id]
    )[-100:]
    if flags.get("mermaid_pending_confirmation_message_id") == message_id:
        flags.pop("mermaid_pending_confirmation_message_id", None)
    state_registry.wa_save_booking_state(phone, state.get("fields") or {}, flags, state.get("completed_bookings") or [])
