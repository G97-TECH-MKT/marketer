"""Schema-repair prompt for a single retry when Pydantic validation fails."""

REPAIR_PROMPT_TEMPLATE = """\
The previous JSON you returned did not validate against the PostEnrichment
schema.

Validation error(s):
{error}

Your previous output:
{previous_output}

Return a corrected JSON object that:
1. Matches PostEnrichment exactly (field names, nesting, required fields).
2. Preserves your original proposal — do not re-plan from scratch; only fix
   the shape.
3. Keeps every literal token (URL, phone, email, hex, price) bound to the
   Context's `brief_facts` / `brand_tokens` / `available_channels`.
4. Keeps language consistent with the brief's `communication_language`.

Output only the corrected JSON. No prose, no markdown, no explanations.
"""
