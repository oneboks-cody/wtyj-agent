# Tracy hospitality: independent offline review

PASS for implementation and scripted offline review; release and live quality remain separate.

Candidate: `3a5f5240f9d51e71fb47c47fbce8a16d1688f78e`.

Reviewed 24 complete scripted conversations, 71 turns, plus the separate nondispatched adversarial example. Independently ran 29 network-disabled checks and reproduced both previously failing cases successfully.

Scores describe these scripted examples only. They do not establish live model behavior, guest receipt or native-language quality.

| Conversation | Warmth | Listening | Guidance | Commercial progress | Accuracy | Ease |
| --- | --- | --- | --- | --- | --- | --- |
| test_01_calvin_introduction_and_month | 5 | 5 | 4 | 4 | 5 | 5 |
| test_02_relaxed_couple | 4 | 5 | 4 | 4 | 5 | 4 |
| test_03_family_volunteers_details | 4 | 5 | 4 | 4 | 5 | 4 |
| test_04_specific_budget | 4 | 5 | 4 | 4 | 5 | 4 |
| test_05_direct_booking_known_trip | 4 | 5 | 4 | 5 | 5 | 4 |
| test_06_interest_is_not_consent | 4 | 5 | 4 | 4 | 5 | 4 |
| test_07_photos_more_and_ambiguous | 4 | 5 | 4 | 4 | 5 | 4 |
| test_08_changed_preference | 4 | 5 | 4 | 4 | 5 | 4 |
| test_09_conflicting_trips | 4 | 5 | 4 | 5 | 5 | 4 |
| test_10_correction_and_question | 4 | 5 | 4 | 5 | 5 | 4 |
| test_11_missing_supplier_information | 4 | 5 | 4 | 4 | 5 | 4 |
| test_12_declined_recommendation | 4 | 5 | 4 | 4 | 5 | 5 |
| test_13_processing_failure | 4 | 4 | 4 | 4 | 5 | 4 |
| test_14_explicit_human | 4 | 5 | 4 | 4 | 5 | 5 |
| test_15_returning_guest_pending_and_booked | 4 | 5 | 4 | 5 | 5 | 4 |
| test_16_de | 4 | 4 | 4 | 4 | 4 | 4 |
| test_16_es | 4 | 4 | 4 | 4 | 4 | 4 |
| test_16_nl | 4 | 4 | 4 | 4 | 4 | 4 |
| test_16_pap | 4 | 4 | 4 | 4 | 4 | 4 |
| test_16_pt | 4 | 4 | 4 | 4 | 4 | 4 |
| test_18_pending_intake_greeting_never_completes_it | 4 | 5 | 4 | 4 | 5 | 4 |
| test_19_question_survives_failed_correction | 4 | 5 | 4 | 4 | 5 | 4 |
| test_20_partial_delivery_and_duplicate_no_resend | 4 | 5 | 4 | 4 | 5 | 4 |
| test_21_answering_authorized_name_intake | 4 | 5 | 4 | 5 | 5 | 4 |

## test_01_calvin_introduction_and_month

- Warmth 5/5: Personal welcome and island inspiration suit the introduction.
- Listening 5/5: Acknowledges the month and then uses the supplied partner detail.
- Guidance 4/5: Narrows companions first and preferred pace second.
- Commercial progress 4/5: Builds useful discovery without premature administration.
- Accuracy 5/5: No itinerary or handoff is created by the name or holiday details.
- Ease 5/5: Three short opening paragraphs and one easy question.

Examples from the reviewed conversation:

Turn 1, guest: Hi, I’m Calvin and interested in activities in Curaçao. We’re arriving next week and staying for a month.

Tracy: Hi Calvin! Lovely to hear you're coming to Curaçao. / A month gives you time for days on the water, a little adventure and slower days to enjoy the island at your own pace. / Who's coming with you?

Turn 2, guest: My partner and I, just the two of us.

Tracy: Lovely, we can find something that suits you both. / Would you prefer a relaxed outing or something active?


## test_02_relaxed_couple

- Warmth 4/5: Relaxed language fits the couple’s stated mood.
- Listening 5/5: Recommendation explicitly uses their slower pace.
- Guidance 4/5: Names a fitting cruise and gives its verified duration and setting.
- Commercial progress 4/5: Offers photos as a concrete next step before purchase.
- Accuracy 5/5: Cruise facts and media remain tied to the fixture product.
- Ease 4/5: Two brief messages and manageable photo delivery.

Examples from the reviewed conversation:

Turn 1, guest: We are a couple looking for a relaxed day.

Tracy: For the slower pace you're after, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour. / Would you like to see a few photos?

Turn 2, guest: Yes please.

Tracy: Here is a photo of Synthetic Cruise. / Would you like more photos or trip details?


## test_03_family_volunteers_details

- Warmth 4/5: Addresses the family’s wishes without exaggerated enthusiasm.
- Listening 5/5: Retains party, child ages, week-long holiday and no-pickup preference.
- Guidance 4/5: Explains the fit of the named relaxed cruise.
- Commercial progress 4/5: Moves from a suitable option to answering lunch and offering photos.
- Accuracy 5/5: Lunch uses a source binding; no age or eligibility inference becomes a booking.
- Ease 4/5: Does not ask again for volunteered details.

Examples from the reviewed conversation:

Turn 1, guest: We are two adults and two children, 6 and 10, here for a week. We like relaxed outings and need no pickup.

Tracy: For the relaxed outing your family wants, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour. / Would you like to explore this option?

Turn 2, guest: Do they include lunch?

Tracy: Lunch is included. / Would you like to see the cruise photos?


## test_04_specific_budget

- Warmth 4/5: Respects the budget and the request for thinking time.
- Listening 5/5: Remembers the total budget and both ages.
- Guidance 4/5: Asks for the missing pricing input before calculating.
- Commercial progress 4/5: Offers an itinerary step and then accepts hesitation.
- Accuracy 5/5: USD 200 demo estimate is calculated without creating booking records.
- Ease 4/5: One pricing question, clear amount and concise pause acknowledgment.

Examples from the reviewed conversation:

Turn 1, guest: We have USD 250 for two people. How much is the cruise for us?

Tracy: I'll keep your total budget in mind. To work out the right price for your party, I need your ages first. / How old are both guests?

Turn 2, guest: We are 35 and 34, October 15 morning, no pickup or extras.

Tracy: The demo estimate for your party is USD 200.00. / Would you like to add this cruise to your itinerary? / Demo: no real reservation or payment.

Turn 3, guest: We need time to think.

Tracy: Of course, take your time. I've kept your preferences for when you're ready.


## test_05_direct_booking_known_trip

- Warmth 4/5: Helpful intake fits a direct booking request.
- Listening 5/5: Uses Calvin’s name and age without asking again.
- Guidance 4/5: Asks only the outstanding trip date.
- Commercial progress 5/5: Moves the authorized choice into a draft and offers review.
- Accuracy 5/5: The draft total and departure are application-generated; defaults are existing catalog defaults.
- Ease 4/5: Missing-date question is short and misleading Add trip controls are absent.

Examples from the reviewed conversation:

Turn 1, guest: Please add the cruise. I am Calvin, age 35.

Tracy: Let's put the details together for your demo itinerary. / Could you share the trip date?

Turn 2, guest: October 15 please.

Tracy: Your choices are together in a demo draft:; Synthetic Cruise · 2026-10-15 · 09:00 / The draft total is USD 100.00. / Would you like to review the full itinerary? / Demo: no real reservation or payment.


## test_06_interest_is_not_consent

- Warmth 4/5: Acknowledges interest without pressure.
- Listening 5/5: Keeps the same named cruise in context.
- Guidance 4/5: Offers a clear next step after the explanation.
- Commercial progress 4/5: Invites selection without treating friendliness as consent.
- Accuracy 5/5: No booking is created by “That looks nice.”
- Ease 4/5: Short explanation followed by one focused question.

Examples from the reviewed conversation:

Turn 1, guest: Tell me about the cruise.

Tracy: Synthetic Cruise is the trip you asked about. A relaxed three-hour cruise around the harbour. / Does that pace suit you?

Turn 2, guest: That looks nice.

Tracy: It sounds like it appeals to you. / Would you like to add this cruise to your itinerary?


## test_07_photos_more_and_ambiguous

- Warmth 4/5: Brief captions fit a direct photo request.
- Listening 5/5: Understands successive requests for additional photos.
- Guidance 4/5: Offers another image or continued discovery in manageable steps.
- Commercial progress 4/5: Uses photos to support choosing without forcing a booking.
- Accuracy 5/5: Distinct product-bound assets are attempted; ambiguous media is not automatically resent.
- Ease 4/5: One photo per turn and no unsolicited gallery flood.

Examples from the reviewed conversation:

Turn 1, guest: Show cruise photos.

Tracy: Here is Synthetic Cruise. / Would you like another photo?

Turn 2, guest: More photos please.

Tracy: Here is another view of Synthetic Cruise. / Would you like to keep browsing?

Turn 3, guest: Another please.

Tracy: Another view of Synthetic Cruise.

Turn 4, guest: Show the next photo.

Tracy: Here is the next view of Synthetic Cruise.


## test_08_changed_preference

- Warmth 4/5: Accepts the change of preference naturally.
- Listening 5/5: Updates active pace to relaxed without discarding unrelated context.
- Guidance 4/5: Explains the named alternative using the newly preferred pace.
- Commercial progress 4/5: Offers the cruise details as a useful next step.
- Accuracy 5/5: Both descriptions come from their own product facts and no selection is invented.
- Ease 4/5: One considered alternative and one question.

Examples from the reviewed conversation:

Turn 1, guest: We want something active.

Tracy: For the active outing you want, I'd start with Synthetic Walk. A two-hour active guided walk. / Does a walk appeal to you?

Turn 2, guest: Actually, a relaxed outing would suit us better.

Tracy: Let's switch to the slower pace you now prefer. Synthetic Cruise could fit. A relaxed three-hour cruise around the harbour. / Would you like the cruise details?


## test_09_conflicting_trips

- Warmth 4/5: Acknowledges the failed change calmly.
- Listening 5/5: Preserves the first cruise while handling the second trip.
- Guidance 4/5: Explains overlapping times and suggests another day.
- Commercial progress 5/5: Completes the second selection once its date is corrected.
- Accuracy 5/5: Authoritative conflict checking blocks incompatible items and preserves the first item.
- Ease 4/5: Specific reason is grouped with preserved draft facts; longer draft review is justified.

Examples from the reviewed conversation:

Turn 1, guest: Please add the cruise for 2026-10-15. I am Calvin, age 35.

Tracy: Your choices are together in a demo draft:; Synthetic Cruise · 2026-10-15 · 09:00 / The draft total is USD 100.00. / Would you like to review the full itinerary? / Demo: no real reservation or payment.

Turn 2, guest: Add the walk on the same date, October 15.

Tracy: I couldn't complete that change. / Those trip times overlap. Choose a different day or a departure that does not conflict.; Synthetic Cruise · 2026-10-15 · 09:00; Demo total: USD 100.00 / Would another day suit you?

Turn 3, guest: Move the walk to October 16.

Tracy: Your choices are together in a demo draft:; Synthetic Cruise · 2026-10-15 · 09:00; Synthetic Walk · 2026-10-16 · 09:00 / The draft total is USD 200.00. / Would you like to review the full itinerary? / Demo: no real reservation or payment.


## test_10_correction_and_question

- Warmth 4/5: Direct and helpful for a practical mixed request.
- Listening 5/5: Answers lunch and applies the requested date correction.
- Guidance 4/5: Shows the corrected draft and review step.
- Commercial progress 5/5: Advances the already-authorized itinerary without losing the question.
- Accuracy 5/5: Source-bound lunch answer and application-calculated corrected draft agree.
- Ease 4/5: Both parts are visible; the draft needs more space than discovery.

Examples from the reviewed conversation:

Turn 1, guest: Please add the cruise for 2026-10-15. I am Calvin, age 35.

Tracy: Your choices are together in a demo draft:; Synthetic Cruise · 2026-10-15 · 09:00 / The draft total is USD 100.00. / Would you like to review the full itinerary? / Demo: no real reservation or payment.

Turn 2, guest: Move the cruise to October 16, and is lunch included?

Tracy: Lunch is included. / Your choices are together in a demo draft:; Synthetic Cruise · 2026-10-16 · 09:00 / The draft total is USD 100.00. / Would you like to review the full itinerary? / Demo: no real reservation or payment.


## test_11_missing_supplier_information

- Warmth 4/5: Handles an accessibility concern without unsupported reassurance.
- Listening 5/5: Honours the later decision to browse photos instead of seeking help.
- Guidance 4/5: States what is unknown and offers the supported help path.
- Commercial progress 4/5: Allows the guest to continue discovery at their own pace.
- Accuracy 5/5: Does not invent accessibility or create an unsolicited operator request.
- Ease 4/5: A concise unknown-answer explanation and one choice.

Examples from the reviewed conversation:

Turn 1, guest: Is the cruise wheelchair accessible?

Tracy: I don't have confirmed accessibility details for this cruise. / Would you like help from the team?

Turn 2, guest: No, just show me the photos for now.

Tracy: Here is Synthetic Cruise.


## test_12_declined_recommendation

- Warmth 4/5: Accepts the decline graciously.
- Listening 5/5: Remembers that boat trips are unwanted.
- Guidance 4/5: Suggests switching to land activities.
- Commercial progress 4/5: Continues relevant discovery without pressure or urgency.
- Accuracy 5/5: No invented discount, availability or follow-up consent.
- Ease 5/5: Brief acknowledgment and a single useful question.

Examples from the reviewed conversation:

Turn 1, guest: What is the cruise like?

Tracy: Synthetic Cruise offers this kind of outing: A relaxed three-hour cruise around the harbour. / Would that suit your plans?

Turn 2, guest: No thanks, we do not want a boat trip.

Tracy: Thanks for telling me. We can leave boat trips out. / Would you prefer an activity on land?


## test_13_processing_failure

- Warmth 4/5: A calm difficulty acknowledgment is appropriate, though the fixed failure copy remains plain.
- Listening 4/5: Retains the existing draft through failure and a fresh follow-up.
- Guidance 4/5: Offers a fresh message as the supported next step.
- Commercial progress 4/5: Allows the conversation to continue without replaying the uncertain turn.
- Accuracy 5/5: Failure is nonempty; the itinerary is unchanged and the failed understanding is not retried.
- Ease 4/5: Short failure reply followed by a direct answer to the fresh question.

Examples from the reviewed conversation:

Turn 1, guest: Please add the cruise for 2026-10-15. I am Calvin, age 35.

Tracy: Your choices are together in a demo draft:; Synthetic Cruise · 2026-10-15 · 09:00 / The draft total is USD 100.00. / Would you like to review the full itinerary? / Demo: no real reservation or payment.

Turn 2, guest: Move it to October 16.

Tracy: I couldn’t process your message. Please send a new message to try again.

Turn 3, guest: What is the current cruise description?

Tracy: A relaxed three-hour cruise around the harbour.


## test_14_explicit_human

- Warmth 4/5: Acknowledges the preference to speak to a person.
- Listening 5/5: Records the human request once and accepts thanks.
- Guidance 4/5: Uses the established operator-review path.
- Commercial progress 4/5: Moves the guest toward appropriate help without selling over the request.
- Accuracy 5/5: Says recorded for review, without claiming notification or an operator response.
- Ease 5/5: Two short paragraphs and a brief acknowledgment.

Examples from the reviewed conversation:

Turn 1, guest: I would like to speak to a person.

Tracy: Of course, I understand you'd prefer help from the team. / Your request is recorded for operator review.

Turn 2, guest: Thank you.

Tracy: You're welcome.


## test_15_returning_guest_pending_and_booked

- Warmth 4/5: Welcomes the returning guest and responds directly to review requests.
- Listening 5/5: Preserves the draft, quote and later demo payment across greetings and photos.
- Guidance 4/5: Acknowledges the requested review and points to its existing confirmation control.
- Commercial progress 5/5: Proceeds through distinct summary, quote approval and demo payment steps.
- Accuracy 5/5: Repeated review uses the original quote ledger; accepted parts are not resent; paid-photo browsing cannot add a trip.
- Ease 4/5: Formal multipart review remains longer, but the natural acknowledgment and controls match the task.

Examples from the reviewed conversation:

Turn 1, guest: Please add the cruise for 2026-10-15. I am Calvin, age 35.

Tracy: Your choices are together in a demo draft:; Synthetic Cruise · 2026-10-15 · 09:00 / The draft total is USD 100.00. / Would you like to review the full itinerary? / Demo: no real reservation or payment.

Turn 2, guest: Hi again, Calvin here.

Tracy: Welcome back, Calvin. / What would you like help with?

Turn 3, guest: Please show the itinerary for review.

Tracy: Here's the review you asked for. Please check the details and use Confirm details when you're ready. / Demo: no real reservation or payment.

Turn 4, guest: Show the review again, and is lunch included?

Tracy: Lunch is included. / Would you like to confirm the details in the review? / Demo: no real reservation or payment.

Turn 5, guest: My name is Calvin, just checking in.

Tracy: Good to hear from you. / What would you like to check?

Turn 6, guest: [Native: Confirm summary]

Tracy: Details confirmed. Review the quote before approving.

Turn 7, guest: [Native: Approve quote]

Tracy: Quote approved. No payment or real reservation has been made.

Turn 8, guest: [Native: Complete demo payment]

Tracy: DEMO PAYMENT COMPLETED - No real money was charged. No supplier booking was made.

Turn 9, guest: Hi, Calvin here again.

Tracy: Welcome back, Calvin. / What can I help you with?

Turn 10, guest: Can I see photos of my cruise?

Tracy: You can browse the gallery for Synthetic Cruise.


## test_16_de

- Warmth 4/5: Appropriate personal welcome in the scripted language example.
- Listening 4/5: Retains the month, party and relaxed preference across four turns.
- Guidance 4/5: Names the cruise and connects it to the requested pace.
- Commercial progress 4/5: Moves from discovery to photos, then honours the request for a person.
- Accuracy 4/5: Translated fixture facts, matched image and recorded-review wording retain the intended boundaries.
- Ease 4/5: Short messages and one next question; native-language polish remains unverified.

Examples from the reviewed conversation:

Turn 1, guest: Hallo, ich bin Calvin. Wir bleiben einen Monat.

Tracy: Hallo Calvin! Mit einem ganzen Monat könnt ihr die Insel in eurem Tempo entdecken. / Wer kommt mit?

Turn 2, guest: Wir sind zwei Erwachsene.

Tracy: Danke, dann suchen wir etwas Passendes für euch beide. / Möchtet ihr etwas Ruhiges oder Aktives?

Turn 3, guest: Wir möchten etwas Ruhiges. Was empfiehlst du?

Tracy: Für euren ruhigen Ausflug würde ich mit Synthetic Cruise beginnen. Eine entspannte dreistündige Hafenrundfahrt. / Möchtest du diesen Ausflug zu deinem Reiseplan hinzufügen?

Turn 4, guest: Ich möchte mit einer Person sprechen.

Tracy: Natürlich, ich verstehe, dass du lieber mit jemandem sprechen möchtest. / Deine Anfrage ist zur Prüfung durch das Team erfasst.


## test_16_es

- Warmth 4/5: Appropriate personal welcome in the scripted language example.
- Listening 4/5: Retains the month, party and relaxed preference across four turns.
- Guidance 4/5: Names the cruise and connects it to the requested pace.
- Commercial progress 4/5: Moves from discovery to photos, then honours the request for a person.
- Accuracy 4/5: Translated fixture facts, matched image and recorded-review wording retain the intended boundaries.
- Ease 4/5: Short messages and one next question; native-language polish remains unverified.

Examples from the reviewed conversation:

Turn 1, guest: Hola, soy Calvin. Nos quedamos un mes.

Tracy: ¡Hola, Calvin! Con un mes podéis descubrir la isla a vuestro ritmo. / ¿Con quién vienes?

Turn 2, guest: Somos dos adultos.

Tracy: Gracias, podemos buscar algo que os guste a los dos. / ¿Preferís algo tranquilo o activo?

Turn 3, guest: Queremos algo tranquilo. ¿Qué recomiendas?

Tracy: Para la salida tranquila que buscáis, empezaría con Synthetic Cruise. Un tranquilo crucero de tres horas por el puerto. / ¿Quieres añadir esta excursión al itinerario?

Turn 4, guest: Quiero hablar con una persona.

Tracy: Claro, entiendo que prefieras hablar con alguien. / Tu solicitud está registrada para que la revise el equipo.


## test_16_nl

- Warmth 4/5: Appropriate personal welcome in the scripted language example.
- Listening 4/5: Retains the month, party and relaxed preference across four turns.
- Guidance 4/5: Names the cruise and connects it to the requested pace.
- Commercial progress 4/5: Moves from discovery to photos, then honours the request for a person.
- Accuracy 4/5: Translated fixture facts, matched image and recorded-review wording retain the intended boundaries.
- Ease 4/5: Short messages and one next question; native-language polish remains unverified.

Examples from the reviewed conversation:

Turn 1, guest: Hoi, ik ben Calvin. We blijven een maand.

Tracy: Hoi Calvin! Fijn dat jullie een maand de tijd hebben om het eiland te ontdekken. / Met wie kom je?

Turn 2, guest: We zijn met twee volwassenen.

Tracy: Dank je, dan zoeken we iets dat bij jullie past. / Willen jullie iets rustigs of actiefs?

Turn 3, guest: We willen iets rustigs. Wat raad je aan?

Tracy: Voor jullie rustige uitstapje zou ik beginnen met Synthetic Cruise. Een rustige rondvaart van drie uur door de haven. / Wil je deze trip aan je reisplan toevoegen?

Turn 4, guest: Ik wil graag een medewerker spreken.

Tracy: Natuurlijk, ik begrijp dat je liever met iemand spreekt. / Je verzoek is vastgelegd voor beoordeling door het team.


## test_16_pap

- Warmth 4/5: Appropriate personal welcome in the scripted language example.
- Listening 4/5: Retains the month, party and relaxed preference across four turns.
- Guidance 4/5: Names the cruise and connects it to the requested pace.
- Commercial progress 4/5: Moves from discovery to photos, then honours the request for a person.
- Accuracy 4/5: Translated fixture facts, matched image and recorded-review wording retain the intended boundaries.
- Ease 4/5: Short messages and one next question; native-language polish remains unverified.

Examples from the reviewed conversation:

Turn 1, guest: Bon dia, mi ta Calvin. Nos ta keda un luna.

Tracy: Bon dia Calvin! Ku un luna boso tin tempu pa eksplorá e isla na boso ritmo. / Ku ken bo ta bini?

Turn 2, guest: Nos ta dos adulto.

Tracy: Danki, nos por buska algu ku ta pas ku boso dos. / Boso ta preferá algu trankil òf aktivo?

Turn 3, guest: Nos ke algu trankil. Kiko bo ta rekomendá?

Tracy: Pa e paseo trankil ku boso ke, mi lo kuminsá ku Synthetic Cruise. Un paseo trankil di tres ora den haf. / Bo ke agregá e paseo aki na bo itinerario?

Turn 4, guest: Mi ke papia ku un persona.

Tracy: Di akuerdo, mi ta komprondé ku bo ke papia ku un persona. / Bo petishon ta registrá pa e ekipo revisá.


## test_16_pt

- Warmth 4/5: Appropriate personal welcome in the scripted language example.
- Listening 4/5: Retains the month, party and relaxed preference across four turns.
- Guidance 4/5: Names the cruise and connects it to the requested pace.
- Commercial progress 4/5: Moves from discovery to photos, then honours the request for a person.
- Accuracy 4/5: Translated fixture facts, matched image and recorded-review wording retain the intended boundaries.
- Ease 4/5: Short messages and one next question; native-language polish remains unverified.

Examples from the reviewed conversation:

Turn 1, guest: Olá, sou o Calvin. Ficamos um mês.

Tracy: Olá, Calvin! Com um mês podem descobrir a ilha ao vosso ritmo. / Quem vem contigo?

Turn 2, guest: Somos dois adultos.

Tracy: Podemos encontrar algo que agrade aos dois. / Preferem algo tranquilo ou ativo?

Turn 3, guest: Queremos algo tranquilo. O que recomenda?

Tracy: Para o passeio tranquilo que procuram, começaria com Synthetic Cruise. Um cruzeiro tranquilo de três horas pelo porto. / Quer adicionar este passeio ao itinerário?

Turn 4, guest: Quero falar com uma pessoa.

Tracy: Claro, compreendo que prefira falar com alguém. / O pedido está registado para análise pela equipa.


## test_18_pending_intake_greeting_never_completes_it

- Warmth 4/5: Greets the returning guest without restarting intake.
- Listening 5/5: Keeps the outstanding date and known name/age.
- Guidance 4/5: Offers to continue the existing arrangement.
- Commercial progress 4/5: Preserves the partially arranged cruise.
- Accuracy 5/5: An unrelated greeting does not complete or duplicate the pending item.
- Ease 4/5: One short next-step question.

Examples from the reviewed conversation:

Turn 1, guest: Add the cruise. I am Calvin, age 35.

Tracy: Let's put the details together for your demo itinerary. / Could you share the trip date?

Turn 2, guest: Hi, Calvin here, I am 35.

Tracy: Hi Calvin, good to hear from you. / Would you like to continue arranging the cruise?


## test_19_question_survives_failed_correction

- Warmth 4/5: Explains the failed change calmly.
- Listening 5/5: Preserves the lunch question alongside the correction.
- Guidance 4/5: Gives the actual past-date reason and invites a future day.
- Commercial progress 4/5: Keeps the existing cruise while leaving correction possible.
- Accuracy 5/5: Lunch comes from the source and the old valid date remains unchanged.
- Ease 4/5: The error and preserved draft facts are grouped; no generic date error.

Examples from the reviewed conversation:

Turn 1, guest: Please add the cruise for 2026-10-15. I am Calvin, age 35.

Tracy: Your choices are together in a demo draft:; Synthetic Cruise · 2026-10-15 · 09:00 / The draft total is USD 100.00. / Would you like to review the full itinerary? / Demo: no real reservation or payment.

Turn 2, guest: Move the cruise to yesterday, and is lunch included?

Tracy: Lunch is included. / I couldn't complete that change. / That date is in the past. Please choose a future date.; Synthetic Cruise · 2026-10-15 · 09:00; Demo total: USD 100.00 / Would another day suit you?


## test_20_partial_delivery_and_duplicate_no_resend

- Warmth 4/5: Brief media captions fit the request.
- Listening 5/5: Preserves the last accepted question after a rejected send.
- Guidance 4/5: Keeps the photo sequence and its context available.
- Commercial progress 4/5: Supports browsing without a booking mutation.
- Accuracy 5/5: Rejected attempt is recorded and duplicate dispatch is suppressed.
- Ease 4/5: One image per request; no fallback flood.

Examples from the reviewed conversation:

Turn 1, guest: Photos of the cruise.

Tracy: Here is Synthetic Cruise. / Would you like more?

Turn 2, guest: Another photo.

Tracy: Another view of Synthetic Cruise. / Shall I show the next?


## test_21_answering_authorized_name_intake

- Warmth 4/5: Helpful direct intake fits explicit trip selection.
- Listening 5/5: Uses the bare name/age answer for the question Tracy actually asked.
- Guidance 4/5: Completes the already-authorized pending details.
- Commercial progress 5/5: Moves to draft review without redundant permission.
- Accuracy 5/5: The explicit contextual update completes one pending item without inventing a new selection.
- Ease 4/5: No repeated request for the supplied name or age.

Examples from the reviewed conversation:

Turn 1, guest: Add the cruise on October 15.

Tracy: Let's put the details together for your demo itinerary. / Could you share the guest name?

Turn 2, guest: Calvin, age 35.

Tracy: Your choices are together in a demo draft:; Synthetic Cruise · 2026-10-15 · 09:00 / The draft total is USD 100.00. / Would you like to review the full itinerary? / Demo: no real reservation or payment.

## Limits and release state

- No live model reply or real guest receipt was tested.
- Valid references and exact consent quotations do not semantically certify arbitrary model prose or interpretation; T17 demonstrates that limitation without dispatch.
- Non-English scores apply to the scripted text and visible flow; native-language quality remains unverified.
- Image tests prove fixture product/asset associations, not visual verification of live operator galleries.
- These are demo quotes/payments, not real availability, supplier reservations or charges.
- Transport or authorization failure can prevent delivery; nonempty processing-failure generation does not guarantee receipt.

High-level preserving-data sequence is sound. It still requires a concrete exact-source packet, fresh maintenance evidence and applicable release authority; completed historical one-run approvals are not reusable.

The rewrite is implemented and reviewed offline. It is not deployed or verified through a real customer conversation.
