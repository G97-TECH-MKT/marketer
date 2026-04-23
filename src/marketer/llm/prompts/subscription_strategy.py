"""Action overlay: subscription_strategy (multi-job batch)."""

SUBSCRIPTION_STRATEGY_OVERLAY = """\
ACTION: subscription_strategy

You are producing a BATCH of post proposals from a single strategy brief.
The `subscription_jobs` array in the Context below lists N jobs. Each job
has an `action_key` (the post type), a `description` (the per-job request),
and an `index` (its position in the output array).

Multiple jobs may share the same `action_key` and `description` — this means
the subscription requires several DIFFERENT pieces of the same type. Each
MUST be a unique, distinct proposal. Never duplicate content across items.

Return a JSON object with this shape:

```
{"items": [PostEnrichment, PostEnrichment, ...]}
```

One PostEnrichment per job, in the SAME order as `subscription_jobs`.

Required behavior — PER ITEM:
- Each item follows the exact PostEnrichment v2.0 schema (same fields as create_post).
- Pick `surface_format` independently per job. If the description hints at
  story / reel / carousel, use it; otherwise default to "post".
- Pick `content_pillar` and `angle` independently per job — ACTIVELY VARY them
  across the batch. A batch of 3 posts all with pillar "product" is wasted
  diversity. Vary pillar and angle to cover different facets of the brand.
- `brand_dna` should be identical or nearly identical across items (same brand).
- Each `caption` is a standalone publishable draft (hook + body + cta_line).
- Each `cta.channel` must be one of `available_channels`.
- Fill `hashtag_strategy.tags` with 5-10 actual hashtag strings (# prefix)
  per item.
- Compose `cf_post_brief` LAST per item, using the standard §cf_post_brief
  format based on each item's surface_format.
- Fill `brand_intelligence` per item — business_taxonomy is shared, but
  funnel_stage_target, emotional_beat, rhetorical_device SHOULD vary across items.
- Vary `rhetorical_device` across items to avoid repetition.

SURFACE-SPECIFIC CHARACTER LIMITS (same as create_post):

  story  → hook ≤ 80 chars · body ≤ 120 chars · cta_line ≤ 50 chars
           TOTAL hook+body+cta_line ≤ 250 chars
  reel   → hook ≤ 100 chars · total ≤ 1000 chars
  post   → hook ≤ 125 chars · total ≤ 2200 chars
  carousel → hook ≤ 125 chars · total ≤ 2200 chars

Strategic coherence across the batch:
- Think of the N items as a content calendar slice. They should feel like
  they come from the same brand but cover different angles, emotions, and
  funnel stages.
- If one item targets awareness, another should target consideration or
  conversion. If one uses "pregunta_retórica", another should use
  "especificidad_concreta" or "narración_origen".
- When multiple items share the same action_key (e.g., 2× create_post),
  you MUST diversify across them: different content_pillar, different angle,
  different surface_format when possible, different rhetorical_device,
  different emotional_beat. Two items with the same pillar + angle is a
  calendar that feels repetitive — avoid it.
- Flag in each item's `narrative_connection` how it relates to the other
  items in the batch (e.g., "segunda pieza del lote semanal — complementa
  el enfoque educativo de la pieza 1").
"""
