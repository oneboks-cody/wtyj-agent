# Refreshed scripted conversations

Synthetic catalog, scripted model responses, stub transports. No live model or native-language quality proof.

## test_01_calvin_introduction_and_month

```json
{
  "guest": "Hi, I’m Calvin and interested in activities in Curaçao. We’re arriving next week and staying for a month.",
  "reply": "Hi Calvin! Lovely to hear you're coming to Curaçao.\n\nA month gives you time for days on the water, a little adventure and slower days to enjoy the island at your own pace.\n\nWho's coming with you?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "Arriving next week, staying a month"
  },
  "stage": "welcome",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Hi Calvin! Lovely to hear you're coming to Curaçao.\n\nA month gives you time for days on the water, a little adventure and slower days to enjoy the island at your own pace.\n\nWho's coming with you?"
    }
  ],
  "last_accepted_question": "Who's coming with you?",
  "history": [
    {
      "content": "Hi, I’m Calvin and interested in activities in Curaçao. We’re arriving next week and staying for a month.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Hi Calvin! Lovely to hear you're coming to Curaçao.\n\nA month gives you time for days on the water, a little adventure and slower days to enjoy the island at your own pace.\n\nWho's coming with you?",
      "delivery_id": "discovery:425924189c593d82b8ab15b37879546f",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Who's coming with you?"
    }
  ]
}
```

```json
{
  "guest": "My partner and I, just the two of us.",
  "reply": "Lovely, we can find something that suits you both.\n\nWould you prefer a relaxed outing or something active?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "Arriving next week, staying a month",
    "party": "Two adults, partners"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Lovely, we can find something that suits you both.\n\nWould you prefer a relaxed outing or something active?"
    }
  ],
  "last_accepted_question": "Would you prefer a relaxed outing or something active?",
  "history": [
    {
      "content": "My partner and I, just the two of us.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Lovely, we can find something that suits you both.\n\nWould you prefer a relaxed outing or something active?",
      "delivery_id": "discovery:8db2d705d89f2dac5af2383f8f9074ea",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you prefer a relaxed outing or something active?"
    }
  ]
}
```

## test_02_relaxed_couple

```json
{
  "guest": "We are a couple looking for a relaxed day.",
  "reply": "For the slower pace you're after, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour.\n\nWould you like to see a few photos?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {
      "fixture-cruise": "The couple requested a relaxed pace; the catalog describes a relaxed cruise."
    },
    "guest": {},
    "pace": "Relaxed",
    "party": "Couple"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_9c16335dcf0cffc2",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "For the slower pace you're after, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour.\n\nWould you like to see a few photos?"
    }
  ],
  "last_accepted_question": "Would you like to see a few photos?",
  "history": [
    {
      "content": "We are a couple looking for a relaxed day.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "For the slower pace you're after, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour.\n\nWould you like to see a few photos?",
      "delivery_id": "discovery:231168c18e3f37495f99d8a1238a4634",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_9c16335dcf0cffc2": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like to see a few photos?"
    }
  ]
}
```

```json
{
  "guest": "Yes please.",
  "reply": "Here is a photo of Synthetic Cruise.\n\nWould you like more photos or trip details?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {
      "fixture-cruise": "The couple requested a relaxed pace; the catalog describes a relaxed cruise."
    },
    "guest": {},
    "pace": "Relaxed",
    "party": "Couple"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_2011da976fcb9098",
          "title": "Add trip",
          "type": "postback"
        },
        {
          "payload": "isl_6986fd1a5d8844c3",
          "title": "More photos",
          "type": "postback"
        }
      ],
      "message": "Here is a photo of Synthetic Cruise.\n\nWould you like more photos or trip details?"
    }
  ],
  "last_accepted_question": "Would you like more photos or trip details?",
  "history": [
    {
      "content": "Yes please.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Here is a photo of Synthetic Cruise.\n\nWould you like more photos or trip details?",
      "delivery_id": "discovery:0a21d526c9d2df61d48f8ee48d863cba",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_2011da976fcb9098": {
          "kind": "add",
          "product_id": "fixture-cruise"
        },
        "isl_6986fd1a5d8844c3": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like more photos or trip details?"
    }
  ]
}
```

## test_03_family_volunteers_details

```json
{
  "guest": "We are two adults and two children, 6 and 10, here for a week. We like relaxed outings and need no pickup.",
  "reply": "For the relaxed outing your family wants, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour.\n\nWould you like to explore this option?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {},
    "holiday": "One week",
    "pace": "Relaxed",
    "party": "Two adults, children 6 and 10",
    "requirements": "No pickup"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_03f7d6036a162524",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "For the relaxed outing your family wants, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour.\n\nWould you like to explore this option?"
    }
  ],
  "last_accepted_question": "Would you like to explore this option?",
  "history": [
    {
      "content": "We are two adults and two children, 6 and 10, here for a week. We like relaxed outings and need no pickup.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "For the relaxed outing your family wants, I'd start with Synthetic Cruise. A relaxed three-hour cruise around the harbour.\n\nWould you like to explore this option?",
      "delivery_id": "discovery:630b596cb21097fc90fca87053ff3151",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_03f7d6036a162524": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like to explore this option?"
    }
  ]
}
```

```json
{
  "guest": "Do they include lunch?",
  "reply": "Lunch is included.\n\nWould you like to see the cruise photos?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {},
    "holiday": "One week",
    "pace": "Relaxed",
    "party": "Two adults, children 6 and 10",
    "requirements": "No pickup"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_63048792e087eb96",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "Lunch is included.\n\nWould you like to see the cruise photos?"
    }
  ],
  "last_accepted_question": "Would you like to see the cruise photos?",
  "history": [
    {
      "content": "Do they include lunch?",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Lunch is included.\n\nWould you like to see the cruise photos?",
      "delivery_id": "discovery:2459e40bbbd76961c75b4dc5364008b9",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_63048792e087eb96": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like to see the cruise photos?"
    }
  ]
}
```

## test_04_specific_budget

```json
{
  "guest": "We have USD 250 for two people. How much is the cruise for us?",
  "reply": "I'll keep your total budget in mind. To work out the right price for your party, I need your ages first.\n\nHow old are both guests?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "budget": "USD 250 total",
    "discussed": {},
    "guest": {},
    "party": "Two people"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_cdb650f4cec5f8bc",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "I'll keep your total budget in mind. To work out the right price for your party, I need your ages first.\n\nHow old are both guests?"
    }
  ],
  "last_accepted_question": "How old are both guests?",
  "history": [
    {
      "content": "We have USD 250 for two people. How much is the cruise for us?",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "I'll keep your total budget in mind. To work out the right price for your party, I need your ages first.\n\nHow old are both guests?",
      "delivery_id": "discovery:5891f8a969132cebaa46988af1016e0b",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_cdb650f4cec5f8bc": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "How old are both guests?"
    }
  ]
}
```

```json
{
  "guest": "We are 35 and 34, October 15 morning, no pickup or extras.",
  "reply": "The demo estimate for your party is USD 200.00.\n\nWould you like to add this cruise to your itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "budget": "USD 250 total",
    "discussed": {},
    "guest": {
      "ages": [
        35,
        34
      ]
    },
    "party": "Two people"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_9aed7b55f41c8252",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "The demo estimate for your party is USD 200.00.\n\nWould you like to add this cruise to your itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to add this cruise to your itinerary?",
  "history": [
    {
      "content": "We are 35 and 34, October 15 morning, no pickup or extras.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "The demo estimate for your party is USD 200.00.\n\nWould you like to add this cruise to your itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:7d056e71bfde47aedba140f4d60adf2a",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_9aed7b55f41c8252": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like to add this cruise to your itinerary?"
    }
  ]
}
```

```json
{
  "guest": "We need time to think.",
  "reply": "Of course, take your time. I've kept your preferences for when you're ready.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "budget": "USD 250 total",
    "discussed": {},
    "guest": {
      "ages": [
        35,
        34
      ]
    },
    "party": "Two people"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Of course, take your time. I've kept your preferences for when you're ready."
    }
  ],
  "last_accepted_question": "Would you like to add this cruise to your itinerary?",
  "history": [
    {
      "content": "We need time to think.",
      "role": "user",
      "trigger_id": "hospitality-3"
    },
    {
      "role": "assistant",
      "content": "Of course, take your time. I've kept your preferences for when you're ready.",
      "delivery_id": "discovery:10099b666fcaf1ab55cbbc109ada488c",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

## test_05_direct_booking_known_trip

```json
{
  "guest": "Please add the cruise. I am Calvin, age 35.",
  "reply": "Let's put the details together for your demo itinerary.\n\nCould you share the trip date?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 1,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Let's put the details together for your demo itinerary.\n\nCould you share the trip date?"
    }
  ],
  "last_accepted_question": "Could you share the trip date?",
  "history": [
    {
      "content": "Please add the cruise. I am Calvin, age 35.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Let's put the details together for your demo itinerary.\n\nCould you share the trip date?",
      "delivery_id": "discovery:da5213257f60d7fbc831a00f1793908e",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Could you share the trip date?"
    }
  ]
}
```

```json
{
  "guest": "October 15 please.",
  "reply": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "October 15 please.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:472beed9ef906a17a18ad1f312712944",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to review the full itinerary?"
    }
  ]
}
```

## test_06_interest_is_not_consent

```json
{
  "guest": "Tell me about the cruise.",
  "reply": "Synthetic Cruise is the trip you asked about. A relaxed three-hour cruise around the harbour.\n\nDoes that pace suit you?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_24c67cc8c0cff7a3",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "Synthetic Cruise is the trip you asked about. A relaxed three-hour cruise around the harbour.\n\nDoes that pace suit you?"
    }
  ],
  "last_accepted_question": "Does that pace suit you?",
  "history": [
    {
      "content": "Tell me about the cruise.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Synthetic Cruise is the trip you asked about. A relaxed three-hour cruise around the harbour.\n\nDoes that pace suit you?",
      "delivery_id": "discovery:b6ce2de25395ae6540e31730f0c1d05a",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_24c67cc8c0cff7a3": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Does that pace suit you?"
    }
  ]
}
```

```json
{
  "guest": "That looks nice.",
  "reply": "It sounds like it appeals to you.\n\nWould you like to add this cruise to your itinerary?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_10e1b7a3fdf44f5b",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "It sounds like it appeals to you.\n\nWould you like to add this cruise to your itinerary?"
    }
  ],
  "last_accepted_question": "Would you like to add this cruise to your itinerary?",
  "history": [
    {
      "content": "That looks nice.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "It sounds like it appeals to you.\n\nWould you like to add this cruise to your itinerary?",
      "delivery_id": "discovery:845dba7064d32e6da0b015f3a0d390e7",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_10e1b7a3fdf44f5b": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like to add this cruise to your itinerary?"
    }
  ]
}
```

## test_07_photos_more_and_ambiguous

```json
{
  "guest": "Show cruise photos.",
  "reply": "Here is Synthetic Cruise.\n\nWould you like another photo?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_aa1766c339422fe9",
          "title": "Add trip",
          "type": "postback"
        },
        {
          "payload": "isl_63cc2bf8f25919ca",
          "title": "More photos",
          "type": "postback"
        }
      ],
      "message": "Here is Synthetic Cruise.\n\nWould you like another photo?"
    }
  ],
  "last_accepted_question": "Would you like another photo?",
  "history": [
    {
      "content": "Show cruise photos.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Here is Synthetic Cruise.\n\nWould you like another photo?",
      "delivery_id": "discovery:e3449afb4f981ea30a29e43a4a36ef65",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_63cc2bf8f25919ca": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        },
        "isl_aa1766c339422fe9": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like another photo?"
    }
  ]
}
```

```json
{
  "guest": "More photos please.",
  "reply": "Here is another view of Synthetic Cruise.\n\nWould you like to keep browsing?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-1.jpg",
      "buttons": [
        {
          "payload": "isl_a527e69a9e7de1fd",
          "title": "Add trip",
          "type": "postback"
        },
        {
          "payload": "isl_58c8eadb919073ed",
          "title": "More photos",
          "type": "postback"
        }
      ],
      "message": "Here is another view of Synthetic Cruise.\n\nWould you like to keep browsing?"
    }
  ],
  "last_accepted_question": "Would you like to keep browsing?",
  "history": [
    {
      "content": "More photos please.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Here is another view of Synthetic Cruise.\n\nWould you like to keep browsing?",
      "delivery_id": "discovery:407b683a0a9f57426bb713e8b3c161c8",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-1"
        }
      ],
      "buttons": {
        "isl_58c8eadb919073ed": {
          "fallback": false,
          "kind": "photos",
          "offset": 2,
          "product_id": "fixture-cruise"
        },
        "isl_a527e69a9e7de1fd": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like to keep browsing?"
    }
  ]
}
```

```json
{
  "guest": "Another please.",
  "reply": "Another view of Synthetic Cruise.",
  "provider_outcome": "ambiguous",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-2.jpg",
      "buttons": [
        {
          "payload": "isl_4fea10ede0eef0af",
          "title": "Add trip",
          "type": "postback"
        },
        {
          "payload": "isl_e98edec65b8cf216",
          "title": "More photos",
          "type": "postback"
        }
      ],
      "message": "Another view of Synthetic Cruise."
    }
  ],
  "last_accepted_question": "Would you like to keep browsing?",
  "history": [
    {
      "content": "Another please.",
      "role": "user",
      "trigger_id": "hospitality-3"
    },
    {
      "role": "assistant",
      "content": "Another view of Synthetic Cruise.",
      "delivery_id": "discovery:2b634bee5acc35dec223612525f952ea",
      "delivery_status": "ambiguous",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-2"
        }
      ],
      "buttons": {
        "isl_4fea10ede0eef0af": {
          "kind": "add",
          "product_id": "fixture-cruise"
        },
        "isl_e98edec65b8cf216": {
          "fallback": false,
          "kind": "photos",
          "offset": 3,
          "product_id": "fixture-cruise"
        }
      },
      "question": ""
    }
  ]
}
```

```json
{
  "guest": "Show the next photo.",
  "reply": "Here is the next view of Synthetic Cruise.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-3.jpg",
      "buttons": [
        {
          "payload": "isl_02a7b4444e857113",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "Here is the next view of Synthetic Cruise."
    }
  ],
  "last_accepted_question": "Would you like to keep browsing?",
  "history": [
    {
      "content": "Show the next photo.",
      "role": "user",
      "trigger_id": "hospitality-4"
    },
    {
      "role": "assistant",
      "content": "Here is the next view of Synthetic Cruise.",
      "delivery_id": "discovery:e23acaf1862546ea1f889cff94693c0d",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-3"
        }
      ],
      "buttons": {
        "isl_02a7b4444e857113": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": ""
    }
  ]
}
```

## test_08_changed_preference

```json
{
  "guest": "We want something active.",
  "reply": "For the active outing you want, I'd start with Synthetic Walk. A two-hour active guided walk.\n\nDoes a walk appeal to you?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {},
    "pace": "Active"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_0060b051a03e01b7",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "For the active outing you want, I'd start with Synthetic Walk. A two-hour active guided walk.\n\nDoes a walk appeal to you?"
    }
  ],
  "last_accepted_question": "Does a walk appeal to you?",
  "history": [
    {
      "content": "We want something active.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "For the active outing you want, I'd start with Synthetic Walk. A two-hour active guided walk.\n\nDoes a walk appeal to you?",
      "delivery_id": "discovery:5ea63c43fc6c6dbf608fde2ba1f412cb",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_0060b051a03e01b7": {
          "kind": "add",
          "product_id": "fixture-walk"
        }
      },
      "question": "Does a walk appeal to you?"
    }
  ]
}
```

```json
{
  "guest": "Actually, a relaxed outing would suit us better.",
  "reply": "Let's switch to the slower pace you now prefer. Synthetic Cruise could fit. A relaxed three-hour cruise around the harbour.\n\nWould you like the cruise details?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {},
    "pace": "Relaxed"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_5322ee7421edf80c",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "Let's switch to the slower pace you now prefer. Synthetic Cruise could fit. A relaxed three-hour cruise around the harbour.\n\nWould you like the cruise details?"
    }
  ],
  "last_accepted_question": "Would you like the cruise details?",
  "history": [
    {
      "content": "Actually, a relaxed outing would suit us better.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Let's switch to the slower pace you now prefer. Synthetic Cruise could fit. A relaxed three-hour cruise around the harbour.\n\nWould you like the cruise details?",
      "delivery_id": "discovery:9474c8a988340baad075bacf722bf637",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_5322ee7421edf80c": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like the cruise details?"
    }
  ]
}
```

## test_09_conflicting_trips

```json
{
  "guest": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
  "reply": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:dba3c4067c2dda2b8b6f64ba599798d6",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to review the full itinerary?"
    }
  ]
}
```

```json
{
  "guest": "Add the walk on the same date, October 15.",
  "reply": "I couldn't complete that change.\n\nThose trip times overlap. Choose a different day or a departure that does not conflict.\nSynthetic Cruise · 2026-10-15 · 09:00\nDemo total: USD 100.00\n\nWould another day suit you?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 1,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "I couldn't complete that change.\n\nThose trip times overlap. Choose a different day or a departure that does not conflict.\nSynthetic Cruise · 2026-10-15 · 09:00\nDemo total: USD 100.00\n\nWould another day suit you?"
    }
  ],
  "last_accepted_question": "Would another day suit you?",
  "history": [
    {
      "content": "Add the walk on the same date, October 15.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "I couldn't complete that change.\n\nThose trip times overlap. Choose a different day or a departure that does not conflict.\nSynthetic Cruise · 2026-10-15 · 09:00\nDemo total: USD 100.00\n\nWould another day suit you?",
      "delivery_id": "discovery:5f37acce986f6ddfca5cf06f5895e4b7",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would another day suit you?"
    }
  ]
}
```

```json
{
  "guest": "Move the walk to October 16.",
  "reply": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\nSynthetic Walk · 2026-10-16 · 09:00\n\nThe draft total is USD 200.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\nSynthetic Walk · 2026-10-16 · 09:00\n\nThe draft total is USD 200.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "Move the walk to October 16.",
      "role": "user",
      "trigger_id": "hospitality-3"
    },
    {
      "role": "assistant",
      "content": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\nSynthetic Walk · 2026-10-16 · 09:00\n\nThe draft total is USD 200.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:9017e350f5b4f87dca38ba9f38bad457",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to review the full itinerary?"
    }
  ]
}
```

## test_10_correction_and_question

```json
{
  "guest": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
  "reply": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:70f3fe96ba1260b6742ae75b9edf1c3b",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to review the full itinerary?"
    }
  ]
}
```

```json
{
  "guest": "Move the cruise to October 16, and is lunch included?",
  "reply": "Lunch is included.\n\nYour choices are together in a demo draft:\nSynthetic Cruise · 2026-10-16 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "exploration",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Lunch is included.\n\nYour choices are together in a demo draft:\nSynthetic Cruise · 2026-10-16 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "Move the cruise to October 16, and is lunch included?",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Lunch is included.\n\nYour choices are together in a demo draft:\nSynthetic Cruise · 2026-10-16 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:05973744ea2ad0f416b2d140c87357fc",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to review the full itinerary?"
    }
  ]
}
```

## test_11_missing_supplier_information

```json
{
  "guest": "Is the cruise wheelchair accessible?",
  "reply": "I don't have confirmed accessibility details for this cruise.\n\nWould you like help from the team?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_5674320b55e10725",
          "title": "Ask the team",
          "type": "postback"
        }
      ],
      "message": "I don't have confirmed accessibility details for this cruise.\n\nWould you like help from the team?"
    }
  ],
  "last_accepted_question": "Would you like help from the team?",
  "history": [
    {
      "content": "Is the cruise wheelchair accessible?",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "I don't have confirmed accessibility details for this cruise.\n\nWould you like help from the team?",
      "delivery_id": "discovery:82e0d7ad7051514dee2214e281a7b3fb",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_5674320b55e10725": {
          "kind": "human",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like help from the team?"
    }
  ]
}
```

```json
{
  "guest": "No, just show me the photos for now.",
  "reply": "Here is Synthetic Cruise.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_1825f160b04cfb0c",
          "title": "Add trip",
          "type": "postback"
        },
        {
          "payload": "isl_4add6a1c8382fa1b",
          "title": "More photos",
          "type": "postback"
        }
      ],
      "message": "Here is Synthetic Cruise."
    }
  ],
  "last_accepted_question": "Would you like help from the team?",
  "history": [
    {
      "content": "No, just show me the photos for now.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Here is Synthetic Cruise.",
      "delivery_id": "discovery:b85fae3a9f25e75381f54751346b34d0",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_1825f160b04cfb0c": {
          "kind": "add",
          "product_id": "fixture-cruise"
        },
        "isl_4add6a1c8382fa1b": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        }
      },
      "question": ""
    }
  ]
}
```

## test_12_declined_recommendation

```json
{
  "guest": "What is the cruise like?",
  "reply": "Synthetic Cruise offers this kind of outing: A relaxed three-hour cruise around the harbour.\n\nWould that suit your plans?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "isl_d5e8108841adcd28",
          "title": "Add trip",
          "type": "postback"
        }
      ],
      "message": "Synthetic Cruise offers this kind of outing: A relaxed three-hour cruise around the harbour.\n\nWould that suit your plans?"
    }
  ],
  "last_accepted_question": "Would that suit your plans?",
  "history": [
    {
      "content": "What is the cruise like?",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Synthetic Cruise offers this kind of outing: A relaxed three-hour cruise around the harbour.\n\nWould that suit your plans?",
      "delivery_id": "discovery:ce58d127c5683850deeb45edc1d66535",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": {
        "isl_d5e8108841adcd28": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would that suit your plans?"
    }
  ]
}
```

```json
{
  "guest": "No thanks, we do not want a boat trip.",
  "reply": "Thanks for telling me. We can leave boat trips out.\n\nWould you prefer an activity on land?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {},
    "interests": "Land activities, no boat trips"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Thanks for telling me. We can leave boat trips out.\n\nWould you prefer an activity on land?"
    }
  ],
  "last_accepted_question": "Would you prefer an activity on land?",
  "history": [
    {
      "content": "No thanks, we do not want a boat trip.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Thanks for telling me. We can leave boat trips out.\n\nWould you prefer an activity on land?",
      "delivery_id": "discovery:42b03da1b08eca0bfef70e1bf1888856",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you prefer an activity on land?"
    }
  ]
}
```

## test_13_processing_failure

```json
{
  "guest": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
  "reply": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:38e899d89e184cae55546d7cc4cf581f",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to review the full itinerary?"
    }
  ]
}
```

```json
{
  "guest": "Move it to October 16.",
  "reply": "I couldn’t process your message. Please send a new message to try again.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "I couldn’t process your message. Please send a new message to try again."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "Move it to October 16.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "I couldn’t process your message. Please send a new message to try again.",
      "delivery_id": "discovery:047295da2d836d1c93fe24c3972540b1",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

```json
{
  "guest": "What is the current cruise description?",
  "reply": "A relaxed three-hour cruise around the harbour.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "A relaxed three-hour cruise around the harbour."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "What is the current cruise description?",
      "role": "user",
      "trigger_id": "hospitality-3"
    },
    {
      "role": "assistant",
      "content": "A relaxed three-hour cruise around the harbour.",
      "delivery_id": "discovery:9631f6516c853b2891a07be35250e688",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

## test_14_explicit_human

```json
{
  "guest": "I would like to speak to a person.",
  "reply": "Of course, I understand you'd prefer help from the team.\n\nYour request is recorded for operator review.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Of course, I understand you'd prefer help from the team.\n\nYour request is recorded for operator review."
    }
  ],
  "last_accepted_question": null,
  "history": [
    {
      "content": "I would like to speak to a person.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Of course, I understand you'd prefer help from the team.\n\nYour request is recorded for operator review.",
      "delivery_id": "discovery:c85c0197d63afca45d05b8d8ba59dc5b",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

```json
{
  "guest": "Thank you.",
  "reply": "You're welcome.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "You're welcome."
    }
  ],
  "last_accepted_question": null,
  "history": [
    {
      "content": "Thank you.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "You're welcome.",
      "delivery_id": "discovery:9c0c865e3277cdd88d2ded32a224a4e9",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

## test_15_returning_guest_pending_and_booked

```json
{
  "guest": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
  "reply": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:f0a78faa9ed4dc0c893b7ba4fae51670",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to review the full itinerary?"
    }
  ]
}
```

```json
{
  "guest": "Hi again, Calvin here.",
  "reply": "Welcome back, Calvin.\n\nWhat would you like help with?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    }
  },
  "stage": "after_booking",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Welcome back, Calvin.\n\nWhat would you like help with?"
    }
  ],
  "last_accepted_question": "What would you like help with?",
  "history": [
    {
      "content": "Hi again, Calvin here.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Welcome back, Calvin.\n\nWhat would you like help with?",
      "delivery_id": "discovery:18ceb3469de335013484bf80f3419fbb",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "What would you like help with?"
    }
  ]
}
```

```json
{
  "guest": "Please show the itinerary for review.",
  "reply": "Here's the review you asked for. Please check the details and use Confirm details when you're ready.\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    }
  },
  "stage": "review_approval",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Here's the review you asked for. Please check the details and use Confirm details when you're ready.\n\nDemo: no real reservation or payment."
    },
    {
      "accountId": "synthetic-account",
      "message": "Isluno | Itinerary summary | Version 1\nDEMO - No real reservation or payment. Availability is assumed.\nGuest: Calvin\nDocument language: en"
    },
    {
      "accountId": "synthetic-account",
      "message": "1. Synthetic Cruise\nGuest: Calvin\nGuests / ages: 35\nDeparture: 2026-10-15 09:00 (America/Curacao)\nCheck-in: 2026-10-15 09:00\nPickup / meeting point: Synthetic pier\nages 13-120: 1 x USD 100.00 = USD 100.00\nTotal: USD 100.00\nDemo booking using catalog rules"
    },
    {
      "accountId": "synthetic-account",
      "message": "Total: USD 100.00\nDEMO - No real reservation or payment. Availability is assumed.\nCorrect any details before continuing."
    },
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "iq_d25a48321fa25b030941c897665c598c",
          "title": "Confirm details",
          "type": "postback"
        }
      ],
      "message": "Correct any details before continuing."
    }
  ],
  "last_accepted_question": "What would you like help with?",
  "history": [
    {
      "role": "assistant",
      "content": "Total: USD 100.00\nDEMO - No real reservation or payment. Availability is assumed.\nCorrect any details before continuing.",
      "delivery_id": "quote:a5e036dc410d52f1e779172e19a31073:2",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    },
    {
      "role": "assistant",
      "content": "Correct any details before continuing.",
      "delivery_id": "quote:a5e036dc410d52f1e779172e19a31073:3",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [
        {
          "payload": "iq_d25a48321fa25b030941c897665c598c",
          "title": "Confirm details",
          "type": "postback"
        }
      ],
      "question": ""
    }
  ]
}
```

```json
{
  "guest": "Show the review again, and is lunch included?",
  "reply": "Lunch is included.\n\nWould you like to confirm the details in the review?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    }
  },
  "stage": "exploration",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Lunch is included.\n\nWould you like to confirm the details in the review?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to confirm the details in the review?",
  "history": [
    {
      "content": "Show the review again, and is lunch included?",
      "role": "user",
      "trigger_id": "hospitality-4"
    },
    {
      "role": "assistant",
      "content": "Lunch is included.\n\nWould you like to confirm the details in the review?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:7728769f3811f8f4bcc57c91ea8849a5",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to confirm the details in the review?"
    }
  ]
}
```

```json
{
  "guest": "My name is Calvin, just checking in.",
  "reply": "Good to hear from you.\n\nWhat would you like to check?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    }
  },
  "stage": "exploration",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Good to hear from you.\n\nWhat would you like to check?"
    }
  ],
  "last_accepted_question": "What would you like to check?",
  "history": [
    {
      "content": "My name is Calvin, just checking in.",
      "role": "user",
      "trigger_id": "hospitality-5"
    },
    {
      "role": "assistant",
      "content": "Good to hear from you.\n\nWhat would you like to check?",
      "delivery_id": "discovery:9c56f8616d5ea84889a5832fadb134ba",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "What would you like to check?"
    }
  ]
}
```

```json
{
  "guest": "[Native: Confirm summary]",
  "reply": "Details confirmed. Review the quote before approving.",
  "provider_outcome": "accepted",
  "model_calls": 0,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    }
  },
  "stage": "review_approval",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Details confirmed. Review the quote before approving.",
      "attachmentUrl": "https://example.invalid/documents/0ecbbbe084bc9a2ee4c9a0b69887e040.pdf",
      "attachmentType": "file",
      "attachmentName": "Isluno-quote.pdf"
    },
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "iq_8b08e0a378f741378ce086db35c947d6",
          "title": "Approve quote",
          "type": "postback"
        }
      ],
      "message": "Details confirmed. Review the quote before approving."
    }
  ],
  "last_accepted_question": "What would you like to check?",
  "history": [
    {
      "role": "assistant",
      "content": "Details confirmed. Review the quote before approving.",
      "delivery_id": "quote:889c274b08fc069cd0fcc99069712bf2:0",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "document_quote_id": "0ecbbbe084bc9a2ee4c9a0b69887e040",
          "type": "file"
        }
      ],
      "buttons": [],
      "question": ""
    },
    {
      "role": "assistant",
      "content": "Details confirmed. Review the quote before approving.",
      "delivery_id": "quote:889c274b08fc069cd0fcc99069712bf2:1",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [
        {
          "payload": "iq_8b08e0a378f741378ce086db35c947d6",
          "title": "Approve quote",
          "type": "postback"
        }
      ],
      "question": ""
    }
  ]
}
```

```json
{
  "guest": "[Native: Approve quote]",
  "reply": "Quote approved. No payment or real reservation has been made.",
  "provider_outcome": "accepted",
  "model_calls": 0,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    }
  },
  "stage": "payment_confirmation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "buttons": [
        {
          "payload": "ip_0a5e44dd45e617af54d801e172a7b3c6",
          "title": "Complete demo pay",
          "type": "postback"
        }
      ],
      "message": "Quote approved. No payment or real reservation has been made."
    }
  ],
  "last_accepted_question": "What would you like to check?",
  "history": [
    {
      "content": "[Verified WhatsApp action: approve_quote]",
      "role": "user"
    },
    {
      "role": "assistant",
      "content": "Quote approved. No payment or real reservation has been made.",
      "delivery_id": "quote:a7c756a0692ac671a634887edbaa26a4:0",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [
        {
          "payload": "ip_0a5e44dd45e617af54d801e172a7b3c6",
          "title": "Complete demo pay",
          "type": "postback"
        }
      ],
      "question": ""
    }
  ]
}
```

```json
{
  "guest": "[Native: Complete demo payment]",
  "reply": "DEMO PAYMENT COMPLETED - No real money was charged. No supplier booking was made.",
  "provider_outcome": "accepted",
  "model_calls": 0,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    }
  },
  "stage": "after_booking",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "DEMO PAYMENT COMPLETED - No real money was charged. No supplier booking was made."
    },
    {
      "accountId": "synthetic-account",
      "attachmentName": "Isluno-receipt-634fa8a7deb519915c53917d100bc3cb.pdf",
      "message": "Demo receipt / itinerary: 634fa8a7deb519915c53917d100bc3cb",
      "attachmentUrl": "https://example.invalid/documents/5fd191d453cf4e7f7804962dfd17c99c.pdf",
      "attachmentType": "file"
    },
    {
      "accountId": "synthetic-account",
      "attachmentName": "Isluno-ticket-ISL-DEMO-8D04E4F6CB50C0FA.pdf",
      "message": "Demo ticket: ISL-DEMO-8D04E4F6CB50C0FA",
      "attachmentUrl": "https://example.invalid/documents/944f9765c62f8be8da919f531e80317c.pdf",
      "attachmentType": "file"
    },
    {
      "accountId": "synthetic-account",
      "message": "Send receipt by email - Please provide the email address for your receipt."
    }
  ],
  "last_accepted_question": "What would you like to check?",
  "history": [
    {
      "role": "assistant",
      "content": "Demo ticket: ISL-DEMO-8D04E4F6CB50C0FA",
      "delivery_id": "quote:ca6bc079d7e3c3c4efe97c0dfbfe6ad6:2",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "document_quote_id": "944f9765c62f8be8da919f531e80317c",
          "type": "file"
        }
      ],
      "buttons": [],
      "question": ""
    },
    {
      "role": "assistant",
      "content": "Send receipt by email - Please provide the email address for your receipt.",
      "delivery_id": "quote:ca6bc079d7e3c3c4efe97c0dfbfe6ad6:3",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

```json
{
  "guest": "Hi, Calvin here again.",
  "reply": "Welcome back, Calvin.\n\nWhat can I help you with?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    }
  },
  "stage": "after_booking",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Welcome back, Calvin.\n\nWhat can I help you with?"
    }
  ],
  "last_accepted_question": "What can I help you with?",
  "history": [
    {
      "content": "Hi, Calvin here again.",
      "role": "user",
      "trigger_id": "hospitality-9"
    },
    {
      "role": "assistant",
      "content": "Welcome back, Calvin.\n\nWhat can I help you with?",
      "delivery_id": "discovery:fd037804dca19994d98795d99a20af40",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "What can I help you with?"
    }
  ]
}
```

```json
{
  "guest": "Can I see photos of my cruise?",
  "reply": "You can browse the gallery for Synthetic Cruise.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    }
  },
  "stage": "after_booking",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_ad3e11c0e8f57773",
          "title": "More photos",
          "type": "postback"
        }
      ],
      "message": "You can browse the gallery for Synthetic Cruise."
    }
  ],
  "last_accepted_question": "What can I help you with?",
  "history": [
    {
      "content": "Can I see photos of my cruise?",
      "role": "user",
      "trigger_id": "hospitality-10"
    },
    {
      "role": "assistant",
      "content": "You can browse the gallery for Synthetic Cruise.",
      "delivery_id": "discovery:759a23b4fcd2feb2ec3440fc294113d8",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_ad3e11c0e8f57773": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        }
      },
      "question": ""
    }
  ]
}
```

## test_16_de

```json
{
  "guest": "Hallo, ich bin Calvin. Wir bleiben einen Monat.",
  "reply": "Hallo Calvin! Mit einem ganzen Monat könnt ihr die Insel in eurem Tempo entdecken.\n\nWer kommt mit?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month"
  },
  "stage": "welcome",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Hallo Calvin! Mit einem ganzen Monat könnt ihr die Insel in eurem Tempo entdecken.\n\nWer kommt mit?"
    }
  ],
  "last_accepted_question": "Wer kommt mit?",
  "history": [
    {
      "content": "Hallo, ich bin Calvin. Wir bleiben einen Monat.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Hallo Calvin! Mit einem ganzen Monat könnt ihr die Insel in eurem Tempo entdecken.\n\nWer kommt mit?",
      "delivery_id": "discovery:1a4ffb4bf14b4062951f62e2aea2ec24",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Wer kommt mit?"
    }
  ]
}
```

```json
{
  "guest": "Wir sind zwei Erwachsene.",
  "reply": "Danke, dann suchen wir etwas Passendes für euch beide.\n\nMöchtet ihr etwas Ruhiges oder Aktives?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Danke, dann suchen wir etwas Passendes für euch beide.\n\nMöchtet ihr etwas Ruhiges oder Aktives?"
    }
  ],
  "last_accepted_question": "Möchtet ihr etwas Ruhiges oder Aktives?",
  "history": [
    {
      "content": "Wir sind zwei Erwachsene.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Danke, dann suchen wir etwas Passendes für euch beide.\n\nMöchtet ihr etwas Ruhiges oder Aktives?",
      "delivery_id": "discovery:a13bc2d84bbd6fd08709de1acfdef873",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Möchtet ihr etwas Ruhiges oder Aktives?"
    }
  ]
}
```

```json
{
  "guest": "Wir möchten etwas Ruhiges. Was empfiehlst du?",
  "reply": "Für euren ruhigen Ausflug würde ich mit Synthetic Cruise beginnen. Eine entspannte dreistündige Hafenrundfahrt.\n\nMöchtest du diesen Ausflug zu deinem Reiseplan hinzufügen?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_f6c4e5366f42e748",
          "title": "Reise hinzufügen",
          "type": "postback"
        },
        {
          "payload": "isl_15bb894ccb34fe5a",
          "title": "Mehr Fotos",
          "type": "postback"
        }
      ],
      "message": "Für euren ruhigen Ausflug würde ich mit Synthetic Cruise beginnen. Eine entspannte dreistündige Hafenrundfahrt.\n\nMöchtest du diesen Ausflug zu deinem Reiseplan hinzufügen?"
    }
  ],
  "last_accepted_question": "Möchtest du diesen Ausflug zu deinem Reiseplan hinzufügen?",
  "history": [
    {
      "content": "Wir möchten etwas Ruhiges. Was empfiehlst du?",
      "role": "user",
      "trigger_id": "hospitality-3"
    },
    {
      "role": "assistant",
      "content": "Für euren ruhigen Ausflug würde ich mit Synthetic Cruise beginnen. Eine entspannte dreistündige Hafenrundfahrt.\n\nMöchtest du diesen Ausflug zu deinem Reiseplan hinzufügen?",
      "delivery_id": "discovery:e905675ee12825a7e8cd3066f9079937",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_15bb894ccb34fe5a": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        },
        "isl_f6c4e5366f42e748": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Möchtest du diesen Ausflug zu deinem Reiseplan hinzufügen?"
    }
  ]
}
```

```json
{
  "guest": "Ich möchte mit einer Person sprechen.",
  "reply": "Natürlich, ich verstehe, dass du lieber mit jemandem sprechen möchtest.\n\nDeine Anfrage ist zur Prüfung durch das Team erfasst.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Natürlich, ich verstehe, dass du lieber mit jemandem sprechen möchtest.\n\nDeine Anfrage ist zur Prüfung durch das Team erfasst."
    }
  ],
  "last_accepted_question": "Möchtest du diesen Ausflug zu deinem Reiseplan hinzufügen?",
  "history": [
    {
      "content": "Ich möchte mit einer Person sprechen.",
      "role": "user",
      "trigger_id": "hospitality-4"
    },
    {
      "role": "assistant",
      "content": "Natürlich, ich verstehe, dass du lieber mit jemandem sprechen möchtest.\n\nDeine Anfrage ist zur Prüfung durch das Team erfasst.",
      "delivery_id": "discovery:e38471ffb0467081d0ed4ffb114ea3b0",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

## test_16_es

```json
{
  "guest": "Hola, soy Calvin. Nos quedamos un mes.",
  "reply": "¡Hola, Calvin! Con un mes podéis descubrir la isla a vuestro ritmo.\n\n¿Con quién vienes?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month"
  },
  "stage": "welcome",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "¡Hola, Calvin! Con un mes podéis descubrir la isla a vuestro ritmo.\n\n¿Con quién vienes?"
    }
  ],
  "last_accepted_question": "¿Con quién vienes?",
  "history": [
    {
      "content": "Hola, soy Calvin. Nos quedamos un mes.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "¡Hola, Calvin! Con un mes podéis descubrir la isla a vuestro ritmo.\n\n¿Con quién vienes?",
      "delivery_id": "discovery:a2860b66d29bd9286770c0ef60d01209",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "¿Con quién vienes?"
    }
  ]
}
```

```json
{
  "guest": "Somos dos adultos.",
  "reply": "Gracias, podemos buscar algo que os guste a los dos.\n\n¿Preferís algo tranquilo o activo?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Gracias, podemos buscar algo que os guste a los dos.\n\n¿Preferís algo tranquilo o activo?"
    }
  ],
  "last_accepted_question": "¿Preferís algo tranquilo o activo?",
  "history": [
    {
      "content": "Somos dos adultos.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Gracias, podemos buscar algo que os guste a los dos.\n\n¿Preferís algo tranquilo o activo?",
      "delivery_id": "discovery:8f4190cc1d413447956a25c8eb57d32a",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "¿Preferís algo tranquilo o activo?"
    }
  ]
}
```

```json
{
  "guest": "Queremos algo tranquilo. ¿Qué recomiendas?",
  "reply": "Para la salida tranquila que buscáis, empezaría con Synthetic Cruise. Un tranquilo crucero de tres horas por el puerto.\n\n¿Quieres añadir esta excursión al itinerario?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_eeb3fec606e6e513",
          "title": "Añadir viaje",
          "type": "postback"
        },
        {
          "payload": "isl_5dc1d58ab809f712",
          "title": "Más fotos",
          "type": "postback"
        }
      ],
      "message": "Para la salida tranquila que buscáis, empezaría con Synthetic Cruise. Un tranquilo crucero de tres horas por el puerto.\n\n¿Quieres añadir esta excursión al itinerario?"
    }
  ],
  "last_accepted_question": "¿Quieres añadir esta excursión al itinerario?",
  "history": [
    {
      "content": "Queremos algo tranquilo. ¿Qué recomiendas?",
      "role": "user",
      "trigger_id": "hospitality-3"
    },
    {
      "role": "assistant",
      "content": "Para la salida tranquila que buscáis, empezaría con Synthetic Cruise. Un tranquilo crucero de tres horas por el puerto.\n\n¿Quieres añadir esta excursión al itinerario?",
      "delivery_id": "discovery:861281fad61e3be8f6b72cda41e82bdd",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_5dc1d58ab809f712": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        },
        "isl_eeb3fec606e6e513": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "¿Quieres añadir esta excursión al itinerario?"
    }
  ]
}
```

```json
{
  "guest": "Quiero hablar con una persona.",
  "reply": "Claro, entiendo que prefieras hablar con alguien.\n\nTu solicitud está registrada para que la revise el equipo.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Claro, entiendo que prefieras hablar con alguien.\n\nTu solicitud está registrada para que la revise el equipo."
    }
  ],
  "last_accepted_question": "¿Quieres añadir esta excursión al itinerario?",
  "history": [
    {
      "content": "Quiero hablar con una persona.",
      "role": "user",
      "trigger_id": "hospitality-4"
    },
    {
      "role": "assistant",
      "content": "Claro, entiendo que prefieras hablar con alguien.\n\nTu solicitud está registrada para que la revise el equipo.",
      "delivery_id": "discovery:e3ce056edaa57664a86a11b499ddb01f",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

## test_16_nl

```json
{
  "guest": "Hoi, ik ben Calvin. We blijven een maand.",
  "reply": "Hoi Calvin! Fijn dat jullie een maand de tijd hebben om het eiland te ontdekken.\n\nMet wie kom je?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month"
  },
  "stage": "welcome",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Hoi Calvin! Fijn dat jullie een maand de tijd hebben om het eiland te ontdekken.\n\nMet wie kom je?"
    }
  ],
  "last_accepted_question": "Met wie kom je?",
  "history": [
    {
      "content": "Hoi, ik ben Calvin. We blijven een maand.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Hoi Calvin! Fijn dat jullie een maand de tijd hebben om het eiland te ontdekken.\n\nMet wie kom je?",
      "delivery_id": "discovery:29768efb13fc0bfbc8c04165f8c51d99",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Met wie kom je?"
    }
  ]
}
```

```json
{
  "guest": "We zijn met twee volwassenen.",
  "reply": "Dank je, dan zoeken we iets dat bij jullie past.\n\nWillen jullie iets rustigs of actiefs?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Dank je, dan zoeken we iets dat bij jullie past.\n\nWillen jullie iets rustigs of actiefs?"
    }
  ],
  "last_accepted_question": "Willen jullie iets rustigs of actiefs?",
  "history": [
    {
      "content": "We zijn met twee volwassenen.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Dank je, dan zoeken we iets dat bij jullie past.\n\nWillen jullie iets rustigs of actiefs?",
      "delivery_id": "discovery:2439bf9dc3688c6dc2a4888eee495ab6",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Willen jullie iets rustigs of actiefs?"
    }
  ]
}
```

```json
{
  "guest": "We willen iets rustigs. Wat raad je aan?",
  "reply": "Voor jullie rustige uitstapje zou ik beginnen met Synthetic Cruise. Een rustige rondvaart van drie uur door de haven.\n\nWil je deze trip aan je reisplan toevoegen?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_c272db856408f9ee",
          "title": "Reis toevoegen",
          "type": "postback"
        },
        {
          "payload": "isl_f06f0d75326ac060",
          "title": "Meer foto’s",
          "type": "postback"
        }
      ],
      "message": "Voor jullie rustige uitstapje zou ik beginnen met Synthetic Cruise. Een rustige rondvaart van drie uur door de haven.\n\nWil je deze trip aan je reisplan toevoegen?"
    }
  ],
  "last_accepted_question": "Wil je deze trip aan je reisplan toevoegen?",
  "history": [
    {
      "content": "We willen iets rustigs. Wat raad je aan?",
      "role": "user",
      "trigger_id": "hospitality-3"
    },
    {
      "role": "assistant",
      "content": "Voor jullie rustige uitstapje zou ik beginnen met Synthetic Cruise. Een rustige rondvaart van drie uur door de haven.\n\nWil je deze trip aan je reisplan toevoegen?",
      "delivery_id": "discovery:53eae97cb31cb2a1e66038c9bb760f98",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_c272db856408f9ee": {
          "kind": "add",
          "product_id": "fixture-cruise"
        },
        "isl_f06f0d75326ac060": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        }
      },
      "question": "Wil je deze trip aan je reisplan toevoegen?"
    }
  ]
}
```

```json
{
  "guest": "Ik wil graag een medewerker spreken.",
  "reply": "Natuurlijk, ik begrijp dat je liever met iemand spreekt.\n\nJe verzoek is vastgelegd voor beoordeling door het team.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Natuurlijk, ik begrijp dat je liever met iemand spreekt.\n\nJe verzoek is vastgelegd voor beoordeling door het team."
    }
  ],
  "last_accepted_question": "Wil je deze trip aan je reisplan toevoegen?",
  "history": [
    {
      "content": "Ik wil graag een medewerker spreken.",
      "role": "user",
      "trigger_id": "hospitality-4"
    },
    {
      "role": "assistant",
      "content": "Natuurlijk, ik begrijp dat je liever met iemand spreekt.\n\nJe verzoek is vastgelegd voor beoordeling door het team.",
      "delivery_id": "discovery:20d346f3b505905900d4e9bc3aef349d",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

## test_16_pap

```json
{
  "guest": "Bon dia, mi ta Calvin. Nos ta keda un luna.",
  "reply": "Bon dia Calvin! Ku un luna boso tin tempu pa eksplorá e isla na boso ritmo.\n\nKu ken bo ta bini?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month"
  },
  "stage": "welcome",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Bon dia Calvin! Ku un luna boso tin tempu pa eksplorá e isla na boso ritmo.\n\nKu ken bo ta bini?"
    }
  ],
  "last_accepted_question": "Ku ken bo ta bini?",
  "history": [
    {
      "content": "Bon dia, mi ta Calvin. Nos ta keda un luna.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Bon dia Calvin! Ku un luna boso tin tempu pa eksplorá e isla na boso ritmo.\n\nKu ken bo ta bini?",
      "delivery_id": "discovery:7f455585193acce55caade8f97f5b232",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Ku ken bo ta bini?"
    }
  ]
}
```

```json
{
  "guest": "Nos ta dos adulto.",
  "reply": "Danki, nos por buska algu ku ta pas ku boso dos.\n\nBoso ta preferá algu trankil òf aktivo?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Danki, nos por buska algu ku ta pas ku boso dos.\n\nBoso ta preferá algu trankil òf aktivo?"
    }
  ],
  "last_accepted_question": "Boso ta preferá algu trankil òf aktivo?",
  "history": [
    {
      "content": "Nos ta dos adulto.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Danki, nos por buska algu ku ta pas ku boso dos.\n\nBoso ta preferá algu trankil òf aktivo?",
      "delivery_id": "discovery:9ba902c08e10c3154ad3c6ae4ec22854",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Boso ta preferá algu trankil òf aktivo?"
    }
  ]
}
```

```json
{
  "guest": "Nos ke algu trankil. Kiko bo ta rekomendá?",
  "reply": "Pa e paseo trankil ku boso ke, mi lo kuminsá ku Synthetic Cruise. Un paseo trankil di tres ora den haf.\n\nBo ke agregá e paseo aki na bo itinerario?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_b24068b5a3e6af57",
          "title": "Agregá biahe",
          "type": "postback"
        },
        {
          "payload": "isl_c5f7febc5eff8512",
          "title": "Mas potrèt",
          "type": "postback"
        }
      ],
      "message": "Pa e paseo trankil ku boso ke, mi lo kuminsá ku Synthetic Cruise. Un paseo trankil di tres ora den haf.\n\nBo ke agregá e paseo aki na bo itinerario?"
    }
  ],
  "last_accepted_question": "Bo ke agregá e paseo aki na bo itinerario?",
  "history": [
    {
      "content": "Nos ke algu trankil. Kiko bo ta rekomendá?",
      "role": "user",
      "trigger_id": "hospitality-3"
    },
    {
      "role": "assistant",
      "content": "Pa e paseo trankil ku boso ke, mi lo kuminsá ku Synthetic Cruise. Un paseo trankil di tres ora den haf.\n\nBo ke agregá e paseo aki na bo itinerario?",
      "delivery_id": "discovery:3ddd7e91652528406e38d6b8d2116170",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_b24068b5a3e6af57": {
          "kind": "add",
          "product_id": "fixture-cruise"
        },
        "isl_c5f7febc5eff8512": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        }
      },
      "question": "Bo ke agregá e paseo aki na bo itinerario?"
    }
  ]
}
```

```json
{
  "guest": "Mi ke papia ku un persona.",
  "reply": "Di akuerdo, mi ta komprondé ku bo ke papia ku un persona.\n\nBo petishon ta registrá pa e ekipo revisá.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Di akuerdo, mi ta komprondé ku bo ke papia ku un persona.\n\nBo petishon ta registrá pa e ekipo revisá."
    }
  ],
  "last_accepted_question": "Bo ke agregá e paseo aki na bo itinerario?",
  "history": [
    {
      "content": "Mi ke papia ku un persona.",
      "role": "user",
      "trigger_id": "hospitality-4"
    },
    {
      "role": "assistant",
      "content": "Di akuerdo, mi ta komprondé ku bo ke papia ku un persona.\n\nBo petishon ta registrá pa e ekipo revisá.",
      "delivery_id": "discovery:86a140e40cc6696742b145b96faf8318",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

## test_16_pt

```json
{
  "guest": "Olá, sou o Calvin. Ficamos um mês.",
  "reply": "Olá, Calvin! Com um mês podem descobrir a ilha ao vosso ritmo.\n\nQuem vem contigo?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month"
  },
  "stage": "welcome",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Olá, Calvin! Com um mês podem descobrir a ilha ao vosso ritmo.\n\nQuem vem contigo?"
    }
  ],
  "last_accepted_question": "Quem vem contigo?",
  "history": [
    {
      "content": "Olá, sou o Calvin. Ficamos um mês.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Olá, Calvin! Com um mês podem descobrir a ilha ao vosso ritmo.\n\nQuem vem contigo?",
      "delivery_id": "discovery:6e1b16ae65a55da65b68d0b2cf1fb7e6",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Quem vem contigo?"
    }
  ]
}
```

```json
{
  "guest": "Somos dois adultos.",
  "reply": "Podemos encontrar algo que agrade aos dois.\n\nPreferem algo tranquilo ou ativo?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Podemos encontrar algo que agrade aos dois.\n\nPreferem algo tranquilo ou ativo?"
    }
  ],
  "last_accepted_question": "Preferem algo tranquilo ou ativo?",
  "history": [
    {
      "content": "Somos dois adultos.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Podemos encontrar algo que agrade aos dois.\n\nPreferem algo tranquilo ou ativo?",
      "delivery_id": "discovery:399204c2ef30d087becd66342b8284a5",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Preferem algo tranquilo ou ativo?"
    }
  ]
}
```

```json
{
  "guest": "Queremos algo tranquilo. O que recomenda?",
  "reply": "Para o passeio tranquilo que procuram, começaria com Synthetic Cruise. Um cruzeiro tranquilo de três horas pelo porto.\n\nQuer adicionar este passeio ao itinerário?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_c4eb926a15681e29",
          "title": "Adicionar viagem",
          "type": "postback"
        },
        {
          "payload": "isl_b8e731f96d659610",
          "title": "Mais fotos",
          "type": "postback"
        }
      ],
      "message": "Para o passeio tranquilo que procuram, começaria com Synthetic Cruise. Um cruzeiro tranquilo de três horas pelo porto.\n\nQuer adicionar este passeio ao itinerário?"
    }
  ],
  "last_accepted_question": "Quer adicionar este passeio ao itinerário?",
  "history": [
    {
      "content": "Queremos algo tranquilo. O que recomenda?",
      "role": "user",
      "trigger_id": "hospitality-3"
    },
    {
      "role": "assistant",
      "content": "Para o passeio tranquilo que procuram, começaria com Synthetic Cruise. Um cruzeiro tranquilo de três horas pelo porto.\n\nQuer adicionar este passeio ao itinerário?",
      "delivery_id": "discovery:122929ffc58be8805603ca13ad16dfec",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_b8e731f96d659610": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        },
        "isl_c4eb926a15681e29": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Quer adicionar este passeio ao itinerário?"
    }
  ]
}
```

```json
{
  "guest": "Quero falar com uma pessoa.",
  "reply": "Claro, compreendo que prefira falar com alguém.\n\nO pedido está registado para análise pela equipa.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "name": "Calvin"
    },
    "holiday": "One month",
    "pace": "Relaxed",
    "party": "Two adults"
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Claro, compreendo que prefira falar com alguém.\n\nO pedido está registado para análise pela equipa."
    }
  ],
  "last_accepted_question": "Quer adicionar este passeio ao itinerário?",
  "history": [
    {
      "content": "Quero falar com uma pessoa.",
      "role": "user",
      "trigger_id": "hospitality-4"
    },
    {
      "role": "assistant",
      "content": "Claro, compreendo que prefira falar com alguém.\n\nO pedido está registado para análise pela equipa.",
      "delivery_id": "discovery:3eccdc207c1d344a278edb3f90c2394c",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": ""
    }
  ]
}
```

## test_17_structural_rejection_and_semantic_limit

## test_18_pending_intake_greeting_never_completes_it

```json
{
  "guest": "Add the cruise. I am Calvin, age 35.",
  "reply": "Let's put the details together for your demo itinerary.\n\nCould you share the trip date?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "exploration",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 1,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Let's put the details together for your demo itinerary.\n\nCould you share the trip date?"
    }
  ],
  "last_accepted_question": "Could you share the trip date?",
  "history": [
    {
      "content": "Add the cruise. I am Calvin, age 35.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Let's put the details together for your demo itinerary.\n\nCould you share the trip date?",
      "delivery_id": "discovery:d9c6a28d4841be9b4e788daffc5d37f8",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Could you share the trip date?"
    }
  ]
}
```

```json
{
  "guest": "Hi, Calvin here, I am 35.",
  "reply": "Hi Calvin, good to hear from you.\n\nWould you like to continue arranging the cruise?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {
      "ages": [
        35
      ],
      "name": "Calvin"
    }
  },
  "stage": "exploration",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 1,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Hi Calvin, good to hear from you.\n\nWould you like to continue arranging the cruise?"
    }
  ],
  "last_accepted_question": "Would you like to continue arranging the cruise?",
  "history": [
    {
      "content": "Hi, Calvin here, I am 35.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Hi Calvin, good to hear from you.\n\nWould you like to continue arranging the cruise?",
      "delivery_id": "discovery:62d3c083777daf673cdbdf244c8b8947",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to continue arranging the cruise?"
    }
  ]
}
```

## test_19_question_survives_failed_correction

```json
{
  "guest": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
  "reply": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "booking_preparation",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "Please add the cruise for 2026-10-15. I am Calvin, age 35.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:341946d1230b977a0353322c02b7fd74",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to review the full itinerary?"
    }
  ]
}
```

```json
{
  "guest": "Move the cruise to yesterday, and is lunch included?",
  "reply": "Lunch is included.\n\nI couldn't complete that change.\n\nThat date is in the past. Please choose a future date.\nSynthetic Cruise · 2026-10-15 · 09:00\nDemo total: USD 100.00\n\nWould another day suit you?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "exploration",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 1,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Lunch is included.\n\nI couldn't complete that change.\n\nThat date is in the past. Please choose a future date.\nSynthetic Cruise · 2026-10-15 · 09:00\nDemo total: USD 100.00\n\nWould another day suit you?"
    }
  ],
  "last_accepted_question": "Would another day suit you?",
  "history": [
    {
      "content": "Move the cruise to yesterday, and is lunch included?",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Lunch is included.\n\nI couldn't complete that change.\n\nThat date is in the past. Please choose a future date.\nSynthetic Cruise · 2026-10-15 · 09:00\nDemo total: USD 100.00\n\nWould another day suit you?",
      "delivery_id": "discovery:b72d0c9d7dda0ca1d94d01437ce6a214",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would another day suit you?"
    }
  ]
}
```

## test_20_partial_delivery_and_duplicate_no_resend

```json
{
  "guest": "Photos of the cruise.",
  "reply": "Here is Synthetic Cruise.\n\nWould you like more?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-0.jpg",
      "buttons": [
        {
          "payload": "isl_fc497fb8a000d43d",
          "title": "Add trip",
          "type": "postback"
        },
        {
          "payload": "isl_cb5b6718355c0c1c",
          "title": "More photos",
          "type": "postback"
        }
      ],
      "message": "Here is Synthetic Cruise.\n\nWould you like more?"
    }
  ],
  "last_accepted_question": "Would you like more?",
  "history": [
    {
      "content": "Photos of the cruise.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Here is Synthetic Cruise.\n\nWould you like more?",
      "delivery_id": "discovery:288ab18b0af4d60ca68d7a5827bd3317",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-0"
        }
      ],
      "buttons": {
        "isl_cb5b6718355c0c1c": {
          "fallback": false,
          "kind": "photos",
          "offset": 1,
          "product_id": "fixture-cruise"
        },
        "isl_fc497fb8a000d43d": {
          "kind": "add",
          "product_id": "fixture-cruise"
        }
      },
      "question": "Would you like more?"
    }
  ]
}
```

```json
{
  "guest": "Another photo.",
  "reply": "Another view of Synthetic Cruise.\n\nShall I show the next?",
  "provider_outcome": "rejected",
  "model_calls": 1,
  "browsing": {
    "discussed": {},
    "guest": {}
  },
  "stage": "exploration",
  "itinerary_id": null,
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "attachmentType": "image",
      "attachmentUrl": "https://example.invalid/cruise-photo-1.jpg",
      "buttons": [
        {
          "payload": "isl_9aa149bc5c6d2318",
          "title": "Add trip",
          "type": "postback"
        },
        {
          "payload": "isl_ce9fdd3c6f8d1f4a",
          "title": "More photos",
          "type": "postback"
        }
      ],
      "message": "Another view of Synthetic Cruise.\n\nShall I show the next?"
    }
  ],
  "last_accepted_question": "Would you like more?",
  "history": [
    {
      "content": "Another photo.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Another view of Synthetic Cruise.\n\nShall I show the next?",
      "delivery_id": "discovery:3fad56d28b21763a0976c183c6ae2dc6",
      "delivery_status": "rejected",
      "guest_receipt_verified": false,
      "media": [
        {
          "product_ids": [
            "fixture-cruise"
          ],
          "asset_id": "cruise-photo-1"
        }
      ],
      "buttons": {
        "isl_9aa149bc5c6d2318": {
          "kind": "add",
          "product_id": "fixture-cruise"
        },
        "isl_ce9fdd3c6f8d1f4a": {
          "fallback": false,
          "kind": "photos",
          "offset": 2,
          "product_id": "fixture-cruise"
        }
      },
      "question": ""
    }
  ]
}
```

## test_21_answering_authorized_name_intake

```json
{
  "guest": "Add the cruise on October 15.",
  "reply": "Let's put the details together for your demo itinerary.\n\nCould you share the guest name?",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "exploration",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 1,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Let's put the details together for your demo itinerary.\n\nCould you share the guest name?"
    }
  ],
  "last_accepted_question": "Could you share the guest name?",
  "history": [
    {
      "content": "Add the cruise on October 15.",
      "role": "user",
      "trigger_id": "hospitality-1"
    },
    {
      "role": "assistant",
      "content": "Let's put the details together for your demo itinerary.\n\nCould you share the guest name?",
      "delivery_id": "discovery:8a6f7bba622cda4bbb1ff66c974dac68",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Could you share the guest name?"
    }
  ]
}
```

```json
{
  "guest": "Calvin, age 35.",
  "reply": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
  "provider_outcome": "accepted",
  "model_calls": 1,
  "browsing": {
    "discussed": {}
  },
  "stage": "exploration",
  "itinerary_id": "39ae3a844aaa0b6034daca89759f8c91",
  "pending_items": 0,
  "outbound": [
    {
      "accountId": "synthetic-account",
      "message": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment."
    }
  ],
  "last_accepted_question": "Would you like to review the full itinerary?",
  "history": [
    {
      "content": "Calvin, age 35.",
      "role": "user",
      "trigger_id": "hospitality-2"
    },
    {
      "role": "assistant",
      "content": "Your choices are together in a demo draft:\nSynthetic Cruise · 2026-10-15 · 09:00\n\nThe draft total is USD 100.00.\n\nWould you like to review the full itinerary?\n\nDemo: no real reservation or payment.",
      "delivery_id": "discovery:42ade419cb6c679ebd6c797f84e35e24",
      "delivery_status": "accepted",
      "guest_receipt_verified": false,
      "media": [],
      "buttons": [],
      "question": "Would you like to review the full itinerary?"
    }
  ]
}
```
