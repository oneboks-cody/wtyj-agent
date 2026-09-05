# BRIEF — Optional premium reservation email after payment
**Status:** Deployed; mailbox connection pending | **Files:** Mermaid email workflow, transport and renderer (new), mermaid_understanding.py, mermaid_reservation_workflow.py, mermaid_demo_payment.py, mermaid_document_cards.py, mermaid_date_changes.py, shared/mermaid_customers.py, reservation_email.json, connect_mermaid_gmail.py, focused offline tests | **Depends on:** live helpful recovery | **Blocks:** Gmail app password for hello@1boks.com

## Context
The owner wants Tracy to offer an optional email after payment, ask the address when the guest agrees, save it in the dashboard guest profile, and email the complete reservation, receipt, practical information and official rules/policies in a premium layout.

## Why This Approach
Use the established SMTP pattern through a dedicated outbound-only Gmail transport, and retain the single model call for structured consent/address understanding. Separate credentials avoid enabling the unrelated inbound email poller. Keep deterministic send state, current reservation data, customer profile history and SMTP outcome separate from model wording. Use a responsive table-based HTML email with the existing tropical image and a plain-text alternative plus the latest receipt attachment. Rejected: another model call to compose every email, or an external marketing platform for a transactional booking message. No email is sent merely because an address exists in a profile.

## Instructions
1. Add an offer to the paid closing; the guest may decline or agree naturally. No address or email is required to finish booking.
2. Persist consent and validate the explicitly supplied address; store it in the existing guest profile without merging unrelated customer accounts. Show truthful progress and confirm only provider acceptance.
3. Send from the verified tenant sender using current authoritative booking/payment data, the latest receipt, practical rules and the configured official general-conditions/privacy/cancellation links. Record recipient, reservation revision and send outcome in the customer history for HO tracking.
4. Prevent duplicate sends for inbound retries; uncertain SMTP outcomes must not silently send duplicates. Keep all secrets out of config templates and logs.
5. Render a preview and perform a small set of offline behavior checks with the model and mail provider mocked. No paid Anthropic calls and no unsolicited test email.
6. Keep the guest offer and actual sending gated on outbound sender readiness. The authorized live deployment may install the completed flow while this gate remains closed. Activate through the localhost connection form, which checks Gmail authentication over TLS and installs a dedicated protected credential file without sending an email or accessing the inbox.

## Tests
Focused local checks: opt-in then address sends once; refusal/invalid address/unpaid state never sends; current revision and receipt are used; email/profile/audit survive later booking updates; HTML contains the expected booking facts and official links with escaped guest content.

## Verification and deployment
- Seven offline checks passed: six email checks in 0.97 seconds and one profile persistence check in 1.18 seconds. Real workflow routing is exercised with mocked model responses. Date confirmation and Keep date both release the deferred email using the final receipt; a status question preserves pending consent. SMTP MIME/TLS behavior is mocked. No paid Anthropic calls or test emails were made.
- Browser preview checked at desktop and 390px mobile width. Preview artifacts are under the Mermaid workspace's outputs/reservation-email directory. All six supported locales have complete workflow and email copy; template content uses the approved trip information and three official policy links.
- Backend deployed as wtyj-agent:tracy-reservation-email, image sha256:176e172368158bbe4714a22ef28df8947ce5d3a8a98a9c60e902a6a06f874feb. Nine source files verified against the release. HTTP health 200; watchdog healthy; six peer containers unchanged. Protected client.json and response_policy.json remained unchanged.
- Preserved 80 conversation records, two booking states, two customers, 15 intake snapshots, one reservation, one payment, four documents and four delivery jobs. Database integrity checked. Backup and report: /root/backups/tracy-reservation-email-verified.
- Dashboard email field deployed in frontend commit 5bcd14697128e8aa4451fe85aa53f0d1f0850db8, retaining concurrent journey-timeline and customer-details changes. Asset requests and hashes verified.
- Dedicated sender hello@1boks.com is configured. At deployment, readiness was false because the mailbox credential was absent; generic inbound email remained disabled. The localhost helper verifies the app password and writes only /root/clients/mermaid/config/mermaid_email_password with mode 0600. Credential entry and authenticated delivery remain pending user action; nothing has been represented as sent.

## Success Condition
After payment a guest can opt in, supply an email, receive a premium complete confirmation and receipt, and the operator can see the saved address and send history in the guest file.

## Rollback
Restore the prior Mermaid image and backed-up targeted configuration. Preserve guest email and send records; do not resend completed or uncertain jobs during rollback.
