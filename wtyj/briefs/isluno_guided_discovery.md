# Guided holiday discovery and illustrated trip pitches

Calvin requests immediate implementation/deployment: explain a few quick questions,
learn party size, adults/children and relevant ages, then interests, one question per
turn. Remember supplied answers and skip known questions. Answer direct trip enquiries
without a compulsory questionnaire. Adult drinks preferences are optional, never
assumed. Qualification remains browsing memory and does not authorize a booking.

Update the single-call hospitality contract and brand voice consistently. Require
descriptive/recommendation cards to include a verified product image even if the
model accidentally selects photo none. Explicit guest opt-out is represented by a
validated boolean and preserved by native details controls. Repeated pitches may
reuse a verified image; More photos continues to deduplicate. Never substitute the
brand logo for a trip, invent a stop's photo, or claim a missing image was delivered.

Use network-disabled existing sender fixtures to verify forced recommendation media,
gallery reuse, opt-out, native details, and retained welcome behavior. Prompt checks
cannot prove live model compliance; customer testing supplies that evidence.

Deploy scoped source/profile changes with data preservation and existing rollback.

## Expanded owner request: full catalogue and carousels

Rewrite all 31 product descriptions with vivid, concise travel-documentary qualities,
without imitating a named presenter or inventing source facts. Keep source-hash-bound
editorial facts in the profile; retain restrictions, extras and policy uncertainty.
The Jungle Tour's conflicting refund claims must be identified, not silently resolved.
The offline authoring record is `release/premium_experience/prepare_copy.py`.

Use three verified gallery images per native carousel where available, and one image
when only one is available. Preserve per-part asset records, all scoped action tokens,
the single-call understanding path, delivery idempotency and no retry after ambiguity.
The current provider documents media carousels with no Meta catalogue requirement:
https://docs.zernio.com/platforms/whatsapp/inbox
The quick-reply card payload follows the provider's pass-through schema and the
documented Cloud API quick-reply example:
https://developer.vonage.com/en/messages/guides/whatsapp-carousel-interactive-messages
No live sends are authorized as engineering tests; verify actual rendered payloads
offline and public image URLs read-only, then let Calvin test customer rendering.

Calvin also authorized a clean slate after deployment, without a customer backup.
Use the existing tenant-scoped reset, retaining payload-free replay-prevention IDs.

## Short intake correction

Calvin's screenshot showed duplicated questions, group recaps and emojis during
qualification. Allow empty reply paragraphs in the schema and validator so the
model can send only its single next question. Direct the model to use that shape
for ordinary intake, with no emojis or praise; answer mixed guest questions briefly.
Keep rich language for trip pitches. Deploy and reset the same test tenant again.
