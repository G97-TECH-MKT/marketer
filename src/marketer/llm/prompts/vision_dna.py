"""Vision DNA Blueprint — image style analysis prompt.

Used to analyze client brand images and produce the style_reference_analysis
JSON that feeds into brand_dna.

Usage contexts:
  1. Dedicated pre-analysis call: images → style_reference_analysis JSON only.
  2. Injected into the main reasoning call via VISION_NOTE when images are present.

Output contract: { "style_reference_analysis": { ...blueprint fields... } }
"""

VISION_DNA_PROMPT = """\
STYLE INTERPRETATION: DESIGN DNA BLUEPRINT

You are analyzing visual brand assets to extract the architectural design logic
so it can be reapplied to new, diverse layouts for this client without repeating
the exact composition of the reference.

# I. Core Constraints

Zero Color/Type Inference: Do NOT mention specific colors, hex codes, or font
names inside style_reference_analysis. Focus on contrast, weight, and behavior.
Colors live in the Colors section of brand_dna — not here.

No Markdown Formatting: Do not use **, *, or HTML inside JSON values. Describe
typographic treatments (boldness, italics, tracking) conceptually.

Typography Usage: Do NOT name specific typefaces. Describe how the provided fonts
combine through scale contrast, tracking variations, and alignment tension.

# II. Blueprint Requirements

Define each of the following Rules of Construction:

atmospheric_logic
  Define the emotional "temperature" and the brand's narrative voice. Name the
  design personality at concept level (e.g., "Intimate Masculine Sanctum",
  "Brutalist Utility", "Serene Minimalism"). Add 1 sentence of atmosphere.

compositional_physics
  Define the rhythm. Is it centered/stable, or does it use diagonal tension?
  Describe Rule-of-Thirds application or how visual elements anchor to frame
  edges vs. float. 1-2 sentences.

depth_stack
  Explain how layers interact. Does text sit behind the subject, overlap it
  with a translucent band, or remain isolated in negative space? Define the
  three-plane logic if present (background blur → tack-sharp subject →
  foreground type). 1-2 sentences.

imagery_lighting_standards
  Define the "camera" behavior: focal depth (shallow vs. deep), cropping logic
  (macro-detail vs. wide lifestyle), lighting quality (diffused, high-contrast,
  naturalistic, candlelit). 1-2 sentences.

spatial_ratio
  Establish the mandatory minimum negative space ratio (e.g., "minimum 30%").
  Specify whether negative space is dark or light — the emptiness must feel
  intentional. 1 sentence.

graphic_scaffolding
  Define non-photographic layer elements: hairline frames, translucent legibility
  bands, micro-text textures, repeating watermarks, overlay treatments. Specify
  their role (framing detail, ensuring legibility, adding texture). 1-2 sentences.

typographic_hierarchy
  Define the scale relationship between three tiers:
    "The Hook" — extreme/expressive scale for the main statement.
    "The Body" — structured blocks for supporting copy.
    "The Fine Print" — micro-text used as a graphic anchor, not just info.
  Specify alignment strategy (justified blocks, axis-aligned, etc.). 1-2 sentences.

variability_directive
  State explicitly how these rules flex to produce "Same-Brand, Different-Design"
  results. Name the element that CAN shift (focal point quadrant, crop axis,
  primary type scale) and the constraint that MUST hold. 1 sentence.

# III. Output Format

Return ONLY a single JSON object with this exact key:

{
  "style_reference_analysis": {
    "atmospheric_logic": "...",
    "compositional_physics": "...",
    "depth_stack": "...",
    "imagery_lighting_standards": "...",
    "spatial_ratio": "...",
    "graphic_scaffolding": "...",
    "typographic_hierarchy": "...",
    "variability_directive": "..."
  }
}

Each value: dense, professional, committed. No hedging ("may", "could").
No markdown inside values. Describe rules, not possibilities.
"""
