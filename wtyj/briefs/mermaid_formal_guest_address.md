# Mermaid — Formal guest address in every supported language
**Status:** Prepared locally; deployment pending authorization | **Files:** client.json, reservation_catalog.json, response_policy.json, reservation_email.json; mermaid_documents.py, mermaid_model_recovery.py, mermaid_reply_planning.py, mermaid_reservation_workflow.py | **Depends on:** current multilingual flow | **Blocks:** none

## Context
The owner requires Dutch u/uw unless the customer explicitly permits informal address, and asks for equivalent respectful treatment across languages. The shown pickup summary uses fixed catalog copy; a model-prompt change alone cannot correct it.

## Why This Approach
Update the tenant register and existing guest-facing copy only. Dutch uses u/uw; Spanish uses usted forms; Portuguese uses courteous third-person phrasing without inferring a gendered title. Preserve formal German, courteous English, and approved standard Papiamentu bo/boso. Explicit permission may allow informal generated conversation; fixed documents and confirmations remain formal. Reject runtime pronoun substitution or language classification, which would alter guest data or grammar and violate the architecture.

## Instructions
Cover greetings, questions, pickup summaries, status/recovery, date changes, payment links, document cards, PDF labels and reservation emails. Preserve exact placeholders, facts, URLs, keys, routing, prices, date logic and guest records. Casual guest language, a first name or previous assistant informality is not consent. Keep validation offline and bounded.

## Validation
Review changed wording and check JSON/Python parsing, placeholder preservation, non-copy AST equivalence and unaffected languages. Run the existing isolated email integration checks with network/model adapters blocked. These checks do not establish live model language behavior. No provider calls or live messages are authorized by this brief.

## Success Condition
Guest-facing Dutch consistently defaults to u/uw and every supported language uses its appropriate professional register without unsolicited familiarity.

## Rollback
Restore only the approved changed configuration fields and prior source image after an authorized deployment; preserve concurrent tenant configuration and customer records. Current release policy requires action-specific deployment authorization.

## Completed local verification
Reviewed 215 changed copy/guidance values. JSON and Python parse successfully; formatter fields are preserved; executable AST outside the named existing copy dictionaries is identical. English, German and Papiamentu fixed copy is unchanged; the shared register specifies their appropriate respectful defaults. Six existing isolated email checks passed in 1.24 seconds with network and model adapters blocked. No live provider calls or customer sends; no deployment yet.
