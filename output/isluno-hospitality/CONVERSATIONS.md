# Tracy hospitality: complete offline conversations

Synthetic catalog, scripted model responses, stub provider; no live model or guest receipt proof.

Source candidate: `3a5f5240f9d51e71fb47c47fbce8a16d1688f78e`. All names, facts, prices and provider outcomes below are synthetic fixtures. Model responses are scripted, not live generation. Provider acceptance is not guest receipt.

## test_01_calvin_introduction_and_month

### Turn 1

**Guest:** Hi, I’m Calvin and interested in activities in Curaçao. We’re arriving next week and staying for a month.

**Tracy (attempted outbound):**

Hi Calvin! Lovely to hear you're coming to Curaçao.

A month gives you time for days on the water, a little adventure and slower days to enjoy the island at your own pace.

Who's coming with you?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: welcome.

### Turn 2

**Guest:** My partner and I, just the two of us.

**Tracy (attempted outbound):**

Lovely, we can find something that suits you both.

Would you prefer a relaxed outing or something active?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_02_relaxed_couple

### Turn 1

**Guest:** We are a couple looking for a relaxed day.

**Tracy (attempted outbound):**

For the slower pace you're after, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour.

Would you like to see a few photos?

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** Yes please.

**Tracy (attempted outbound):**

Here is a photo of Synthetic Cruise.

Would you like more photos or trip details?

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: Add trip, More photos

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_03_family_volunteers_details

### Turn 1

**Guest:** We are two adults and two children, 6 and 10, here for a week. We like relaxed outings and need no pickup.

**Tracy (attempted outbound):**

For the relaxed outing your family wants, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour.

Would you like to explore this option?

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** Do they include lunch?

**Tracy (attempted outbound):**

Lunch is included.

Would you like to see the cruise photos?

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_04_specific_budget

### Turn 1

**Guest:** We have USD 250 for two people. How much is the cruise for us?

**Tracy (attempted outbound):**

I'll keep your total budget in mind. To work out the right price for your party, I need your ages first.

How old are both guests?

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** We are 35 and 34, October 15 morning, no pickup or extras.

**Tracy (attempted outbound):**

The demo estimate for your party is USD 200.00.

Would you like to add this cruise to your itinerary?

Demo: no real reservation or payment.

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 3

**Guest:** We need time to think.

**Tracy (attempted outbound):**

Of course, take your time. I've kept your preferences for when you're ready.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_05_direct_booking_known_trip

### Turn 1

**Guest:** Please add the cruise. I am Calvin, age 35.

**Tracy (attempted outbound):**

Let's put the details together for your demo itinerary.

Could you share the trip date?

Outcome: accepted. Model calls: 1. Pending items: 1. Stage: booking_preparation.

### Turn 2

**Guest:** October 15 please.

**Tracy (attempted outbound):**

Your choices are together in a demo draft:
Synthetic Cruise · 2026-10-15 · 09:00

The draft total is USD 100.00.

Would you like to review the full itinerary?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: booking_preparation.

Default provenance: The application selected the catalog sole 09:00 departure, meeting-point pickup=false, and no optional extras. Neither scripted update supplied slot, pickup or options. The draft exposes 09:00; the combined native review exposes arrangements before approval.

## test_06_interest_is_not_consent

### Turn 1

**Guest:** Tell me about the cruise.

**Tracy (attempted outbound):**

Synthetic Cruise is the trip you asked about. A relaxed three-hour cruise around the harbour.

Does that pace suit you?

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** That looks nice.

**Tracy (attempted outbound):**

It sounds like it appeals to you.

Would you like to add this cruise to your itinerary?

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_07_photos_more_and_ambiguous

### Turn 1

**Guest:** Show cruise photos.

**Tracy (attempted outbound):**

Here is Synthetic Cruise.

Would you like another photo?

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: Add trip, More photos

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** More photos please.

**Tracy (attempted outbound):**

Here is another view of Synthetic Cruise.

Would you like to keep browsing?

Attachment: image `https://example.invalid/cruise-photo-1.jpg`

Buttons: Add trip, More photos

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 3

**Guest:** Another please.

**Tracy (attempted outbound):**

Another view of Synthetic Cruise.

Attachment: image `https://example.invalid/cruise-photo-2.jpg`

Buttons: Add trip, More photos

Outcome: ambiguous. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 4

**Guest:** Show the next photo.

**Tracy (attempted outbound):**

Here is the next view of Synthetic Cruise.

Attachment: image `https://example.invalid/cruise-photo-3.jpg`

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_08_changed_preference

### Turn 1

**Guest:** We want something active.

**Tracy (attempted outbound):**

For the active outing you want, I'd start with Synthetic Walk. A two-hour active guided walk.

Does a walk appeal to you?

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** Actually, a relaxed outing would suit us better.

**Tracy (attempted outbound):**

Let's switch to the slower pace you now prefer. Synthetic Cruise could fit. A relaxed three-hour cruise around the harbour.

Would you like the cruise details?

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_09_conflicting_trips

### Turn 1

**Guest:** Please add the cruise for 2026-10-15. I am Calvin, age 35.

**Tracy (attempted outbound):**

Your choices are together in a demo draft:
Synthetic Cruise · 2026-10-15 · 09:00

The draft total is USD 100.00.

Would you like to review the full itinerary?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: booking_preparation.

### Turn 2

**Guest:** Add the walk on the same date, October 15.

**Tracy (attempted outbound):**

I couldn't complete that change.

Those trip times overlap. Choose a different day or a departure that does not conflict.
Synthetic Cruise · 2026-10-15 · 09:00
Demo total: USD 100.00

Would another day suit you?

Outcome: accepted. Model calls: 1. Pending items: 1. Stage: booking_preparation.

### Turn 3

**Guest:** Move the walk to October 16.

**Tracy (attempted outbound):**

Your choices are together in a demo draft:
Synthetic Cruise · 2026-10-15 · 09:00
Synthetic Walk · 2026-10-16 · 09:00

The draft total is USD 200.00.

Would you like to review the full itinerary?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: booking_preparation.

## test_10_correction_and_question

### Turn 1

**Guest:** Please add the cruise for 2026-10-15. I am Calvin, age 35.

**Tracy (attempted outbound):**

Your choices are together in a demo draft:
Synthetic Cruise · 2026-10-15 · 09:00

The draft total is USD 100.00.

Would you like to review the full itinerary?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: booking_preparation.

### Turn 2

**Guest:** Move the cruise to October 16, and is lunch included?

**Tracy (attempted outbound):**

Lunch is included.

Your choices are together in a demo draft:
Synthetic Cruise · 2026-10-16 · 09:00

The draft total is USD 100.00.

Would you like to review the full itinerary?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_11_missing_supplier_information

### Turn 1

**Guest:** Is the cruise wheelchair accessible?

**Tracy (attempted outbound):**

I don't have confirmed accessibility details for this cruise.

Would you like help from the team?

Buttons: Ask the team

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** No, just show me the photos for now.

**Tracy (attempted outbound):**

Here is Synthetic Cruise.

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: Add trip, More photos

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_12_declined_recommendation

### Turn 1

**Guest:** What is the cruise like?

**Tracy (attempted outbound):**

Synthetic Cruise offers this kind of outing: A relaxed three-hour cruise around the harbour.

Would that suit your plans?

Buttons: Add trip

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** No thanks, we do not want a boat trip.

**Tracy (attempted outbound):**

Thanks for telling me. We can leave boat trips out.

Would you prefer an activity on land?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_13_processing_failure

### Turn 1

**Guest:** Please add the cruise for 2026-10-15. I am Calvin, age 35.

**Tracy (attempted outbound):**

Your choices are together in a demo draft:
Synthetic Cruise · 2026-10-15 · 09:00

The draft total is USD 100.00.

Would you like to review the full itinerary?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: booking_preparation.

### Turn 2

**Guest:** Move it to October 16.

**Tracy (attempted outbound):**

I couldn’t process your message. Please send a new message to try again.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: booking_preparation.

### Turn 3

**Guest:** What is the current cruise description?

**Tracy (attempted outbound):**

A relaxed three-hour cruise around the harbour.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_14_explicit_human

### Turn 1

**Guest:** I would like to speak to a person.

**Tracy (attempted outbound):**

Of course, I understand you'd prefer help from the team.

Your request is recorded for operator review.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** Thank you.

**Tracy (attempted outbound):**

You're welcome.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_15_returning_guest_pending_and_booked

### Turn 1

**Guest:** Please add the cruise for 2026-10-15. I am Calvin, age 35.

**Tracy (attempted outbound):**

Your choices are together in a demo draft:
Synthetic Cruise · 2026-10-15 · 09:00

The draft total is USD 100.00.

Would you like to review the full itinerary?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: booking_preparation.

### Turn 2

**Guest:** Hi again, Calvin here.

**Tracy (attempted outbound):**

Welcome back, Calvin.

What would you like help with?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: after_booking.

### Turn 3

**Guest:** Please show the itinerary for review.

**Tracy (attempted outbound):**

Here's the review you asked for. Please check the details and use Confirm details when you're ready.

Demo: no real reservation or payment.

**Tracy (attempted outbound):**

Isluno | Itinerary summary | Version 1
DEMO - No real reservation or payment. Availability is assumed.
Guest: Calvin
Document language: en

**Tracy (attempted outbound):**

1. Synthetic Cruise
Guest: Calvin
Guests / ages: 35
Departure: 2026-10-15 09:00 (America/Curacao)
Check-in: 2026-10-15 09:00
Pickup / meeting point: Synthetic pier
ages 13-120: 1 x USD 100.00 = USD 100.00
Total: USD 100.00
Demo booking using catalog rules

**Tracy (attempted outbound):**

Total: USD 100.00
DEMO - No real reservation or payment. Availability is assumed.
Correct any details before continuing.

**Tracy (attempted outbound):**

Correct any details before continuing.

Buttons: Confirm details

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: review_approval.

### Turn 4

**Guest:** Show the review again, and is lunch included?

**Tracy (attempted outbound):**

Lunch is included.

Would you like to confirm the details in the review?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 5

**Guest:** My name is Calvin, just checking in.

**Tracy (attempted outbound):**

Good to hear from you.

What would you like to check?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 6

**Guest:** [Native: Confirm summary]

**Tracy (attempted outbound):**

Details confirmed. Review the quote before approving.

Attachment: file `https://example.invalid/documents/e746d7a15c687a5c885d17294cc41d30.pdf`

**Tracy (attempted outbound):**

Details confirmed. Review the quote before approving.

Buttons: Approve quote

Outcome: accepted. Model calls: 0. Pending items: 0. Stage: review_approval.

### Turn 7

**Guest:** [Native: Approve quote]

**Tracy (attempted outbound):**

Quote approved. No payment or real reservation has been made.

Buttons: Complete demo pay

Outcome: accepted. Model calls: 0. Pending items: 0. Stage: payment_confirmation.

### Turn 8

**Guest:** [Native: Complete demo payment]

**Tracy (attempted outbound):**

DEMO PAYMENT COMPLETED - No real money was charged. No supplier booking was made.

**Tracy (attempted outbound):**

Demo receipt / itinerary: 141b2e0265f2d087c65ba9bba4a32b22

Attachment: file `https://example.invalid/documents/f6dfa298b23b5cec6c25d4ff6159950b.pdf`

**Tracy (attempted outbound):**

Demo ticket: ISL-DEMO-0802E11F209FE771

Attachment: file `https://example.invalid/documents/40a1ba1995f6fbeaac289de033cedcfc.pdf`

**Tracy (attempted outbound):**

Send receipt by email - Please provide the email address for your receipt.

Outcome: accepted. Model calls: 0. Pending items: 0. Stage: after_booking.

### Turn 9

**Guest:** Hi, Calvin here again.

**Tracy (attempted outbound):**

Welcome back, Calvin.

What can I help you with?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: after_booking.

### Turn 10

**Guest:** Can I see photos of my cruise?

**Tracy (attempted outbound):**

You can browse the gallery for Synthetic Cruise.

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: More photos

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: after_booking.

## test_16_de

### Turn 1

**Guest:** Hallo, ich bin Calvin. Wir bleiben einen Monat.

**Tracy (attempted outbound):**

Hallo Calvin! Mit einem ganzen Monat könnt ihr die Insel in eurem Tempo entdecken.

Wer kommt mit?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: welcome.

### Turn 2

**Guest:** Wir sind zwei Erwachsene.

**Tracy (attempted outbound):**

Danke, dann suchen wir etwas Passendes für euch beide.

Möchtet ihr etwas Ruhiges oder Aktives?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 3

**Guest:** Wir möchten etwas Ruhiges. Was empfiehlst du?

**Tracy (attempted outbound):**

Für euren ruhigen Ausflug würde ich mit Synthetic Cruise beginnen. Eine entspannte dreistündige Hafenrundfahrt.

Möchtest du diesen Ausflug zu deinem Reiseplan hinzufügen?

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: Reise hinzufügen, Mehr Fotos

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 4

**Guest:** Ich möchte mit einer Person sprechen.

**Tracy (attempted outbound):**

Natürlich, ich verstehe, dass du lieber mit jemandem sprechen möchtest.

Deine Anfrage ist zur Prüfung durch das Team erfasst.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_16_es

### Turn 1

**Guest:** Hola, soy Calvin. Nos quedamos un mes.

**Tracy (attempted outbound):**

¡Hola, Calvin! Con un mes podéis descubrir la isla a vuestro ritmo.

¿Con quién vienes?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: welcome.

### Turn 2

**Guest:** Somos dos adultos.

**Tracy (attempted outbound):**

Gracias, podemos buscar algo que os guste a los dos.

¿Preferís algo tranquilo o activo?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 3

**Guest:** Queremos algo tranquilo. ¿Qué recomiendas?

**Tracy (attempted outbound):**

Para la salida tranquila que buscáis, empezaría con Synthetic Cruise. Un tranquilo crucero de tres horas por el puerto.

¿Quieres añadir esta excursión al itinerario?

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: Añadir viaje, Más fotos

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 4

**Guest:** Quiero hablar con una persona.

**Tracy (attempted outbound):**

Claro, entiendo que prefieras hablar con alguien.

Tu solicitud está registrada para que la revise el equipo.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_16_nl

### Turn 1

**Guest:** Hoi, ik ben Calvin. We blijven een maand.

**Tracy (attempted outbound):**

Hoi Calvin! Fijn dat jullie een maand de tijd hebben om het eiland te ontdekken.

Met wie kom je?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: welcome.

### Turn 2

**Guest:** We zijn met twee volwassenen.

**Tracy (attempted outbound):**

Dank je, dan zoeken we iets dat bij jullie past.

Willen jullie iets rustigs of actiefs?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 3

**Guest:** We willen iets rustigs. Wat raad je aan?

**Tracy (attempted outbound):**

Voor jullie rustige uitstapje zou ik beginnen met Synthetic Cruise. Een rustige rondvaart van drie uur door de haven.

Wil je deze trip aan je reisplan toevoegen?

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: Reis toevoegen, Meer foto’s

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 4

**Guest:** Ik wil graag een medewerker spreken.

**Tracy (attempted outbound):**

Natuurlijk, ik begrijp dat je liever met iemand spreekt.

Je verzoek is vastgelegd voor beoordeling door het team.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_16_pap

### Turn 1

**Guest:** Bon dia, mi ta Calvin. Nos ta keda un luna.

**Tracy (attempted outbound):**

Bon dia Calvin! Ku un luna boso tin tempu pa eksplorá e isla na boso ritmo.

Ku ken bo ta bini?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: welcome.

### Turn 2

**Guest:** Nos ta dos adulto.

**Tracy (attempted outbound):**

Danki, nos por buska algu ku ta pas ku boso dos.

Boso ta preferá algu trankil òf aktivo?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 3

**Guest:** Nos ke algu trankil. Kiko bo ta rekomendá?

**Tracy (attempted outbound):**

Pa e paseo trankil ku boso ke, mi lo kuminsá ku Synthetic Cruise. Un paseo trankil di tres ora den haf.

Bo ke agregá e paseo aki na bo itinerario?

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: Agregá biahe, Mas potrèt

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 4

**Guest:** Mi ke papia ku un persona.

**Tracy (attempted outbound):**

Di akuerdo, mi ta komprondé ku bo ke papia ku un persona.

Bo petishon ta registrá pa e ekipo revisá.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_16_pt

### Turn 1

**Guest:** Olá, sou o Calvin. Ficamos um mês.

**Tracy (attempted outbound):**

Olá, Calvin! Com um mês podem descobrir a ilha ao vosso ritmo.

Quem vem contigo?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: welcome.

### Turn 2

**Guest:** Somos dois adultos.

**Tracy (attempted outbound):**

Podemos encontrar algo que agrade aos dois.

Preferem algo tranquilo ou ativo?

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 3

**Guest:** Queremos algo tranquilo. O que recomenda?

**Tracy (attempted outbound):**

Para o passeio tranquilo que procuram, começaria com Synthetic Cruise. Um cruzeiro tranquilo de três horas pelo porto.

Quer adicionar este passeio ao itinerário?

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: Adicionar viagem, Mais fotos

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 4

**Guest:** Quero falar com uma pessoa.

**Tracy (attempted outbound):**

Claro, compreendo que prefira falar com alguém.

O pedido está registado para análise pela equipa.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

## test_17_structural_rejection_and_semantic_limit

**Residual-risk example, never dispatched:**

{fact:fixture-cruise:summary} A luxury transfer is included.

Structurally valid, semantically unsupported; must fail human conversation review. NOT sent by this harness.

## test_18_pending_intake_greeting_never_completes_it

### Turn 1

**Guest:** Add the cruise. I am Calvin, age 35.

**Tracy (attempted outbound):**

Let's put the details together for your demo itinerary.

Could you share the trip date?

Outcome: accepted. Model calls: 1. Pending items: 1. Stage: exploration.

### Turn 2

**Guest:** Hi, Calvin here, I am 35.

**Tracy (attempted outbound):**

Hi Calvin, good to hear from you.

Would you like to continue arranging the cruise?

Outcome: accepted. Model calls: 1. Pending items: 1. Stage: exploration.

## test_19_question_survives_failed_correction

### Turn 1

**Guest:** Please add the cruise for 2026-10-15. I am Calvin, age 35.

**Tracy (attempted outbound):**

Your choices are together in a demo draft:
Synthetic Cruise · 2026-10-15 · 09:00

The draft total is USD 100.00.

Would you like to review the full itinerary?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: booking_preparation.

### Turn 2

**Guest:** Move the cruise to yesterday, and is lunch included?

**Tracy (attempted outbound):**

Lunch is included.

I couldn't complete that change.

That date is in the past. Please choose a future date.
Synthetic Cruise · 2026-10-15 · 09:00
Demo total: USD 100.00

Would another day suit you?

Outcome: accepted. Model calls: 1. Pending items: 1. Stage: exploration.

## test_20_partial_delivery_and_duplicate_no_resend

### Turn 1

**Guest:** Photos of the cruise.

**Tracy (attempted outbound):**

Here is Synthetic Cruise.

Would you like more?

Attachment: image `https://example.invalid/cruise-photo-0.jpg`

Buttons: Add trip, More photos

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.

### Turn 2

**Guest:** Another photo.

**Tracy (attempted outbound):**

Another view of Synthetic Cruise.

Shall I show the next?

Attachment: image `https://example.invalid/cruise-photo-1.jpg`

Buttons: Add trip, More photos

Outcome: rejected. Model calls: 1. Pending items: 0. Stage: exploration.

## test_21_answering_authorized_name_intake

### Turn 1

**Guest:** Add the cruise on October 15.

**Tracy (attempted outbound):**

Let's put the details together for your demo itinerary.

Could you share the guest name?

Outcome: accepted. Model calls: 1. Pending items: 1. Stage: exploration.

### Turn 2

**Guest:** Calvin, age 35.

**Tracy (attempted outbound):**

Your choices are together in a demo draft:
Synthetic Cruise · 2026-10-15 · 09:00

The draft total is USD 100.00.

Would you like to review the full itinerary?

Demo: no real reservation or payment.

Outcome: accepted. Model calls: 1. Pending items: 0. Stage: exploration.
