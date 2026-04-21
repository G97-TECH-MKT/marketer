"""Action overlay: create_web.

Web is OUT OF SCOPE in this iteration. This overlay is kept so the reasoner
can still build a prompt deterministically; the response is overridden with
FAILED before being returned.
"""

CREATE_WEB_OVERLAY = """\
ACTION: create_web

NOTE: Web creation is not yet supported by MARKETER in this iteration. Produce
a minimal object with `title` set to "web_not_supported_in_this_iteration"
and `strategic_reasoning` stating that web output will be handled in a later
milestone. Leave all other text fields as the empty string and all URL lists
empty.
"""
