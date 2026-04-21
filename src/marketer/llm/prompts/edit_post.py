"""Action overlay: edit_post."""

EDIT_POST_OVERLAY = """\
ACTION: edit_post

A `prior_post` is provided in Context (caption, image_url, posted_at,
surface_format). You are proposing the UPDATED version, not a fresh post.

Required behavior:
- Default `surface_format` to `prior_post.surface_format` unless the live
  request asks to change format explicitly (or `requested_surface_format` is
  set).
- Preserve the original positioning, brand signals, and anchor concepts.
  Shift only what the live request asks to change.
- The fields you return ARE the rewritten version: `title`, `caption.hook`,
  `caption.body`, `caption.cta_line`, `image.concept`,
  `image.generation_prompt`, `image.alt_text`, `visual_style_notes`.
- In `strategic_decisions.angle.rationale` state briefly what is preserved
  vs what changes, so Content Factory aligns its edit.
- `cta.channel` must remain valid against `available_channels`.
- Visual selection: only change asset recommendations if the edit scope
  warrants it.
"""
