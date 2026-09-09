# Isluno communication correction — reviewed candidate

Status: not deployed. Prior staging recovery and presentation rollout receipts remain preserved. This packet does not reuse either dispatch allowance.

## What failed

The two latest investigated messages passed signed ingress, debounce, the model call and durable decision/application storage. The generated replies then reached the outbound sender as 1,122- and 1,085-character messages with three top-level buttons. Both plans were recorded as rejected without a provider message ID. The original HTTP error detail was discarded, so its exact status/code cannot be reconstructed.

Zernio documents that top-level buttons become WhatsApp interactive reply buttons. Meta's interactive reply-button body limit is 1,024 characters. The stored payloads therefore violated a documented wire limit. This is a confirmed payload defect and the last evidenced failing boundary; it is not proof of the discarded HTTP error code.

Primary references: [Zernio message contract](https://docs.zernio.com/messages/send-inbox-message), [Meta interactive reply-button reference](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/types/InteractiveObject/#reply-button).

Earlier deployment checks verified source, image, configuration, gates and data preservation. They did not exercise the real sender boundary. The earlier presentation fix therefore did not establish a working customer conversation. Two later delivered callbacks were logged without retained IDs; they cannot be attributed to either rejected reply.

## Required structural changes

1. Validate final wire messages, after source expansion, using conservative UTF-16 limits. Preserve the complete answer and put media/buttons with the final question. Bound the number of parts and dispatch each part once.
2. Persist each part's claim, acceptance, provider IDs and safe HTTP/platform codes. An accepted prefix survives a later rejection, timeout or process interruption. Do not retry uncertain or held work automatically.
3. Keep preparation, provider acceptance and provider delivery/read evidence distinct. Correlate callbacks only by exact account, conversation and message IDs. Preserve unmatched callbacks for later correlation when they precede the HTTP result. Late failures remain visible and block dependent approval without undoing bookings or payments.
4. Require accepted prerequisite messages before native controls can mutate an itinerary or advance a composed quote/payment. A stale control gets a specific, scoped operational response and no model call or mutation.
5. Reduce duplicated prompt state without dropping authoritative item IDs, selections or prices. Allow unused speculative reply branches to be omitted; combine the neutral answer with the actual server outcome. Incomplete intake keeps one model-authored question whose missing field/product/time are server-bound; it does not claim the itinerary is saved. Keep one existing model call and strict fact/consent validation.
6. Mark communication progress only after the complete required reply is accepted. Model completion alone must not resolve old incidents. Surface held/failed parts, transport reasons and conversation links in Nr2.
7. Use a catalog-independent, allowlisted operational notice through the same durable sender if catalog/presentation storage fails. If durable storage itself is unavailable, no safe send can be guaranteed: retain or surface the inbound processing failure when storage is available, and return a persistence error rather than inventing delivery.

The existing generic webhook delivery-failure notification was already durable. The missing visibility was the Isluno domain incident, the discarded transport detail, and the opaque/unlinked operator rows. The correction does not claim the previous system had no failure record anywhere.

## Verification boundaries

The parent source e8aead27 passes175 offline regressions. The final source ab88343 adds the model-authored preparation-question correction; DELTA-TESTS.json records its targeted checks and the corrected test-module invocation. TESTS.json and the original24-conversation/71-turn export remain attributed to the parent, while delta-hospitality-conversations.json and supplementary-conversations.json record the final change. Synthetic SDK output is passed through actual handlers and sender code; external provider adapters are stubbed inside network-disabled Docker. The signed webhook test also exercises parsing, acceptance, burst buffering, duplicate suppression, service-window logic and sender dispatch. Frontend component tests and TypeScript checking verify the actionable recovery view.

These checks prove behavior for the tested inputs and failure injections. They do not prove live model quality, native-language fluency, supplier inventory, real payment settlement, or a customer's receipt of a message. No paid model evaluation, engineering WhatsApp send or email was performed.

## Release gate

The source candidate, exact preservation proof and bounded deployment runner need independent review before any host mutation. A new proof must include the latest rejected plans and all intervening records. No cleanup, historical replay, database restore, profile/account/model change or reuse of a consumed upload guard is permitted. Nr2 session verification is a separate gate for a frontend pointer change.

## Review links

[Backend PR360](https://github.com/BensonOpas/wtyj-agent/pull/360) and [dashboard PR167](https://github.com/unboks-org/unboks-dashboard-api/pull/167) retain this work for review. Application source ab88343b325d3ee67a6dcdad5e15e1f6dd2d254f; dashboard25dfebfb8688f76ef357b97c60c893fdf1bb8791. Deployment remains separately recorded in deployment receipts.
