# Carousel candidate verification

Base: `1225852` (latest application `9621b5c`). Target repository: `BensonOpas/wtyj-agent`. Target tenant: Mermaid's Isluno journey.

All runs used cached image `isluno-local:e2deab8`, Linux/amd64, `--pull never --network none`, this isolated worktree mounted at `/workspace`, synthetic API credential and mocked SDK/HTTP adapters. No live provider/model/messages, resets or deployment.

- 67 tests passed in 116.122s: `test_visual_discovery.VisualDiscoveryTests`, `test_discovery.DiscoveryTests`, `test_communication_wire.CommunicationWireTests`, `test_premium_catalog.PremiumCatalogTests`. Includes all 31 products in both ordinary and carousel modes. Old tests initially expected invalid embedded card quick replies; updated assertions inspect the corrected native payload parts. Legacy pagination and source-text behavior retained.
- After six added actual-handler/sender carousel tests: 38 tests passed in 29.587s: `test_visual_discovery.VisualDiscoveryTests`, `test_welcome_image.WelcomeImageTests`. Includes grouped two/three-trip mapping, second-trip complete gallery, third-trip planning, opt-out, foreign/stale actions, partial/rejected/ambiguous sends and schema/diagnostic checks.
- After preserving the common answer's whitespace and adding the longest-description case: 3 tests passed in 4.754s: `test_premium_catalog.PremiumCatalogTests.test_three_longest_descriptions_and_long_answer_fit_carousel_plan`, `test_visual_discovery.VisualDiscoveryTests.test_carousel_sender_tracks_all_images_and_scoped_buttons`, `test_discovery.DiscoveryTests.test_all_gallery_sizes_are_accessible_and_correctly_associated`. Long common answer and all three longest premium summaries remain lossless with one final question within six parts.
- `git diff --check` passed.

Candidate activation: change only the existing tenant profile's `gallery_mode` to `carousel`. Other profile fields are unchanged by this candidate. Combined greeting/carousel rollout is owned by task `01a0811e-f378-76a1-af2e-3ad4548c2516`; Builder has performed no operational changes. Fresh preflight, preserved customer state, required independent review and separate deployment evidence remain necessary. The authenticated existing Codex dashboard tab reloaded successfully during preparation; this is not proof of carousel provider acceptance.

Provider-contract basis: existing Ali CTA-URL card plus separate native-selection implementation and Brief281. Card URL actions are optional browsing links and do not constitute an in-chat selection. Real WhatsApp carousel acceptance/rendering remains unverified; offline HTTP acceptance is a synthetic fixture outcome only.
