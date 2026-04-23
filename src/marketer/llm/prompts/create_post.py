"""Action overlay: create_post."""

CREATE_POST_OVERLAY = """\
ACTION: create_post

You are proposing a NEW post for `context.platform` (usually Instagram).

Required behavior:
- Pick exactly one `surface_format`. If `requested_surface_format` is set, USE IT.
- Pick one `content_pillar` and justify it in `strategic_decisions.angle.rationale`.
- Compose `caption.hook`, `caption.body`, `caption.cta_line` as a publishable
  first draft in the brief's `communication_language`. Bind every concrete
  fact (price, URL, phone, email) to `brief_facts`.
- Reference brand_tokens.palette hex codes literally in `visual_style_notes`
  when you mean those exact colors.
- `image.generation_prompt` must be concrete enough that a generator could
  produce the photo: subject, composition, POV, lighting, props, style.
- `cta.channel` must be one of `available_channels`. If none fit, set
  channel="none", url_or_handle=null, label="".
- Fill `do_not[]` with up to 5 short anti-patterns grounded in brand voice
  (e.g. "no usar tipografía sobre la imagen").
- Set `narrative_connection` to null unless the brief implies a recurring
  weekly series.
- Set `confidence.*` based on how strongly the brief supported each choice.
- Fill `hashtag_strategy.tags` with 5-10 actual hashtag strings (# prefix)
  aligned to intent and platform. These land verbatim in cf_post_brief.
- Set `hashtag_strategy.suggested_volume` as the hashtag count (0-30), aligned
  to `len(tags)`. This is NOT popularity/search volume.
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
