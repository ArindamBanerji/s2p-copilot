"""
Domain-agnostic feedback state store for CopilotFramework.

FEEDBACK_GIVEN is extracted here so audit.py (and any other framework module)
can import it without depending on app.services.feedback (which is SOC-domain).

When copilot-sdk is extracted, import from copilot_sdk.feedback_store directly.
"""

from typing import Any, Dict

# Tracks which alerts have received feedback.
# Keyed by alert_id; values are {decision_id, outcome, timestamp, graph_updates}.
FEEDBACK_GIVEN: Dict[str, Dict[str, Any]] = {}
