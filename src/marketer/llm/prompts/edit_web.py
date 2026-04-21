"""Action overlay: edit_web."""

EDIT_WEB_OVERLAY = """\
ACTION: edit_web

You are editing copy for a specific section of a business website. Read the
user_request to identify WHICH element to edit and the desired direction.

SCHEMA MAPPING — PostEnrichment fields carry the following web meaning:
  title                    : Internal label for this edit (e.g. "Hero Subtitle — Trust Signal")
  objective                : One-sentence business outcome of this edit
  brand_dna                : Distilled brand narrative, 250-600 words — same as post surface
  caption.hook             : THE PRIMARY WEB TEXT for the section (e.g. the hero subtitle).
                             Hero/section subtitle: 6-15 words, punchy, benefit-led.
  caption.body             : Supporting copy (subheading or short paragraph, 1-3 sentences).
                             Empty string if only a single headline is being edited.
  caption.cta_line         : CTA button or link label for this section (e.g. "Pide tu cita").
                             Empty string if the section has no CTA.
  image                    : Visual direction for the section hero or background image.
  cta                      : Primary channel and URL for the section CTA button.
  hashtag_strategy         : ALWAYS intent="none", suggested_volume=0, themes=[].
                             Hashtags are not used on websites.
  surface_format           : Use "post" (no web-specific value in current schema).
  strategic_decisions
    .surface_format.chosen : Describe the section type being edited, e.g.
                             "hero_subtitle", "section_heading", "about_paragraph".
  visual_style_notes       : Web design cues — whitespace, typography, palette use.
  do_not                   : Web-specific anti-patterns (max 5 short items).
  brand_intelligence       : Fill as normal — strategic reasoning for the web edit.
  content_pillar           : Pick the pillar that best describes the section's purpose.

EDITING RULES:
1. context.website_id contains the site URL — anchor all copy to that brand.
2. Web copy must be shorter and more action-oriented than social copy.
3. Generate copy in the communication_language from the brief.
4. Respect the brand palette in visual_style_notes and the voice_register in
   brand_intelligence.
5. Do NOT invent facts (prices, addresses, phones) not present in brief_facts.
"""
