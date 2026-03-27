"""
CopilotFramework — domain-agnostic copilot infrastructure.

This package is designed for future extraction to copilot-sdk.
Discipline rules (enforced for clean extraction):
  - No imports from app.domains.*
  - No imports from app.services.* (except other framework modules)
  - No imports from app.routers.*
  - Allowed: gae.*, standard library, other framework modules

When extracting to copilot-sdk:
  1. Copy this directory to copilot-sdk/src/copilot_sdk/
  2. Replace 'from app.framework' with 'from copilot_sdk'
  3. Add gae as a dependency in copilot-sdk/pyproject.toml
"""
