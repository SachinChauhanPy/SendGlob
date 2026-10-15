"""Single source of truth for action -> credit cost.

The billable unit is one generated document. Email delivery is free (sent from
the user's own Gmail). AI template generation is metered because it hits a paid
external model API.
"""
DOCUMENT_GENERATION = 1   # per document produced; delivery included free
AI_TEMPLATE_GENERATION = 2
EMAIL_SEND = 0
