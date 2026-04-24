"""Action overlay: create_post."""

CREATE_POST_OVERLAY = """\
ACTION: create_post

You are proposing a NEW post for `context.platform` (usually Instagram).

Required behavior:
- Pick exactly one `surface_format`. If `requested_surface_format` is set, USE IT.
- Pick one `content_pillar`.
- Compose `caption.hook`, `caption.body`, `caption.cta_line` as a publishable
  first draft in the brief's `communication_language`. Bind every concrete
  fact (price, URL, phone, email) to `brief_facts`.
- `cta.channel` must be one of `available_channels`. If none fit, set
  channel="none", url_or_handle=null, label="".
- Fill `hashtag_strategy.tags` with 5-10 actual hashtag strings (# prefix).
- Set `selected_asset_urls` with the content_url(s) from gallery_pool items
  you select (or gallery[] items as fallback). Empty list if no image fits.
- Set `voice_register` (2-5 words, e.g. "cálida y sin florituras").
- Set `audience_persona` (1-2 sentences naming who reads this and their
  strongest objection).
- Compose `cf_post_brief` LAST. Choose the format from §cf_post_brief based on
  surface_format:
    post / story / reel → use the "post/story/reel" format (CONCEPT block +
      Caption block + Hashtags block).
    carousel → use the "carousel" format (strategic overview + Slide N entries
      with image + copy per slide + Caption + Hashtags). Each Slide entry must
      name a specific gallery file OR "AI-generated" for that slide's image.

SURFACE-SPECIFIC CHARACTER LIMITS — these are HARD CAPS, not suggestions:

  story  → hook ≤ 80 chars · body ≤ 120 chars · cta_line ≤ 50 chars
           TOTAL hook+body+cta_line ≤ 250 chars
           A story is a BILLBOARD: one idea, one emotion, one action.
           No paragraphs. No lists. No elaborate body copy.
           If you exceed 250 total the validator will flag it — count before writing.

  reel   → hook ≤ 100 chars · total ≤ 1000 chars
  post   → hook ≤ 125 chars · total ≤ 2200 chars
  carousel → hook ≤ 125 chars · total ≤ 2200 chars
"""
