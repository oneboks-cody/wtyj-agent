# Progressive visual WhatsApp examples

Actual renderer and sender payloads with scripted model output and network denial. Product prices/rules are not changed. No engineering customer send.

## test_warm_introduction_no_forced_products_or_booking

Guest: Hello, I will be on holiday in Curaçao for a month.

Model calls: 1

Welcome! A month on Curaçao gives you time to explore at your own pace.

Who are you travelling with?

Complete provider acceptance (fixture): True

Final state: {"pending_products": [], "active_itinerary_present": false}

## test_two_cards_real_http_and_exact_history

Guest: We are a family visiting Curaçao and enjoy relaxed boat trips and beaches.

Model calls: 1

These two could suit your relaxed family holiday.

Image: https://example.invalid/cruise-0.jpg

Synthetic Cruise

A relaxed coastal cruise.

Actions: More photos → fixture-cruise, Trip details → fixture-cruise, Plan this trip → fixture-cruise

Image: https://example.invalid/beach-0.jpg

Beach adventure

Explore beaches with a guide.

Which appeals to you?

Actions: More photos → fixture-beach, Trip details → fixture-beach, Plan this trip → fixture-beach

Complete provider acceptance (fixture): True

Complete provider acceptance (fixture): True

Guest: [photos for fixture-cruise]

Model calls: 0

Image: https://example.invalid/cruise-1.jpg

Synthetic Cruise

Image: https://example.invalid/cruise-2.jpg

Synthetic Cruise

A relaxed coastal cruise.

Actions: More photos → fixture-cruise, Trip details → fixture-cruise, Plan this trip → fixture-cruise

Complete provider acceptance (fixture): True

Final state: {"pending_products": [], "active_itinerary_present": false}

## test_sparse_dutch_details_use_one_bound_call_then_native_pages

Guest: Wij zijn op vakantie met het gezin en houden van rustige boottochten en stranden.

Model calls: 1

Dit past bij jullie rustige vakantie.

Image: https://example.invalid/cruise-0.jpg

Synthetic Cruise

Een rustige vaartocht langs de kust.

Lijkt dit jullie leuk?

Actions: Meer foto’s → fixture-cruise, Reisdetails → fixture-cruise, Plan deze reis → fixture-cruise

Complete provider acceptance (fixture): True

Guest: Reisdetails

Model calls: 1

Hier zijn de reisdetails.

Synthetic Cruise

Een gids is inbegrepen.

Actions: Meer foto’s → fixture-cruise, Reisdetails → fixture-cruise, Plan deze reis → fixture-cruise

Complete provider acceptance (fixture): True

Guest: Reisdetails

Model calls: 0

Complete provider acceptance (fixture): True

Guest: [info for fixture-cruise]

Model calls: 0

Synthetic Cruise

Een rustige vaartocht langs de kust.

Een gids is inbegrepen.

Actions: Meer foto’s → fixture-cruise, Reisdetails → fixture-cruise, Plan deze reis → fixture-cruise

Complete provider acceptance (fixture): True

Final state: {"pending_products": [], "active_itinerary_present": false}

## test_recommendation_photo_plan_and_intake_are_separate_authorities

Guest: We are a family visiting Curaçao and enjoy relaxed boat trips and beaches.

Model calls: 1

These two could suit your relaxed family holiday.

Image: https://example.invalid/cruise-0.jpg

Synthetic Cruise

A relaxed coastal cruise.

Actions: More photos → fixture-cruise, Trip details → fixture-cruise, Plan this trip → fixture-cruise

Image: https://example.invalid/beach-0.jpg

Beach adventure

Explore beaches with a guide.

Which appeals to you?

Actions: More photos → fixture-beach, Trip details → fixture-beach, Plan this trip → fixture-beach

Complete provider acceptance (fixture): True

Guest: [photos for fixture-cruise]

Model calls: 0

Image: https://example.invalid/cruise-1.jpg

Synthetic Cruise

Image: https://example.invalid/cruise-2.jpg

Synthetic Cruise

A relaxed coastal cruise.

Actions: More photos → fixture-cruise, Trip details → fixture-cruise, Plan this trip → fixture-cruise

Complete provider acceptance (fixture): True

Guest: [Plan this trip]

Model calls: 0

Please provide: guest name.

Complete provider acceptance (fixture): True

Guest: My name is Alex

Model calls: 1

Thanks, Alex.

Could you share the guest ages?

Complete provider acceptance (fixture): True

Final state: {"pending_products": ["fixture-cruise"], "active_itinerary_present": true}

## test_missing_local_media_and_unverified_location_are_truthful_text

Guest: We are a family visiting Curaçao and enjoy relaxed boat trips and beaches.

Model calls: 1

These two could suit your relaxed family holiday.

Synthetic Cruise

A relaxed coastal cruise.

Photos unavailable

Actions: More photos → fixture-cruise, Trip details → fixture-cruise, Plan this trip → fixture-cruise

Beach adventure

Explore beaches with a guide.

Photos unavailable

Which appeals to you?

Actions: More photos → fixture-beach, Trip details → fixture-beach, Plan this trip → fixture-beach

Complete provider acceptance (fixture): True

Guest: We are a family visiting Curaçao and enjoy relaxed boat trips and beaches.

Model calls: 1

These two could suit your relaxed family holiday.

Synthetic Cruise

A relaxed coastal cruise.

Photos unavailable

Actions: Trip details → fixture-cruise, Plan this trip → fixture-cruise

Beach adventure

Explore beaches with a guide.

Photos unavailable

Which appeals to you?

Actions: Trip details → fixture-beach, Plan this trip → fixture-beach

Complete provider acceptance (fixture): True

Final state: {"pending_products": [], "active_itinerary_present": false}

## test_explicit_question_keeps_full_requested_answer

Guest: Please tell me the full arrangements for this cruise.

Model calls: 1

Synthetic Cruise

Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements.

 Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. Verified answer about the requested arrangements. 

Actions: More photos → fixture-cruise, Trip details → fixture-cruise, Plan this trip → fixture-cruise

Complete provider acceptance (fixture): True

Final state: {"pending_products": [], "active_itinerary_present": false}

## test_fresh_text_fallback_after_rejection_does_not_replay_media

Guest: We are a family visiting Curaçao and enjoy relaxed boat trips and beaches.

Model calls: 1

These two could suit your relaxed family holiday.

Complete provider acceptance (fixture): False

Guest: Please send the trip details as text.

Model calls: 1

The earlier reply could not be sent. Here are the trip details in text.

Synthetic Cruise

A relaxed coastal cruise.

Actions: More photos → fixture-cruise, Trip details → fixture-cruise, Plan this trip → fixture-cruise

Beach adventure

Explore beaches with a guide.

Which appeals to you?

Actions: More photos → fixture-beach, Trip details → fixture-beach, Plan this trip → fixture-beach

Complete provider acceptance (fixture): True

Final state: {"pending_products": [], "active_itinerary_present": false}

## actual_catalog_recommendations

Guest: We are a family visiting Curaçao and enjoy relaxed boat trips and beaches.

Model calls: 1

For a day on the water or a mix of beaches and sightseeing, these are two options.

Image: https://api.unboks.org/api/mermaid/r/isluno/media/7950c94ad3788369dc5fededd953452814285d934c5dd5dab491e6a9d7899ba0.jpg

Klein Curaçao Catamaran Day Trip

Travel to Klein Curaçao by catamaran for beach time, snorkelling, breakfast and BBQ lunch. The listing includes an open bar and beach beds.

Klein Curaçao is an island reached by boat from Curaçao.

Actions: More photos → klein-curacao-catamaran-day-trip, Trip details → klein-curacao-catamaran-day-trip, Plan this trip → klein-curacao-catamaran-day-trip

Image: https://api.unboks.org/api/mermaid/r/isluno/media/a4ba254cd9f9f743a0dc9e01fd8fef1fd5865a904502b3edf3da9d7b224a5a54.jpg

Snorkel and Beach Adventures

Combine a 4×4 trip to Shete Boka with snorkelling at Playa Piskado and time at Cas Abao. The listing gives 09:00-15:00.

Shete Boka is a national park; Playa Piskado and Cas Abao are beaches.

Which sounds more like your kind of day?

Actions: More photos → snorkel-and-beach-adventures, Trip details → snorkel-and-beach-adventures, Plan this trip → snorkel-and-beach-adventures

Complete provider acceptance (fixture): True

Final state: {"pending_products": [], "active_itinerary_present": false}
