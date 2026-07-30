"""Disabled legacy Neo4j client.

S2P graph access is provided by the configured AGE GraphStore and the
domain-bound S2PGraphReader.  This module remains only to make stale imports
fail explicitly during migration; it does not create a global client or read
Neo4j environment variables.
"""


class Neo4jClient:
    """Compatibility sentinel for the removed Neo4j integration."""

    def __init__(self) -> None:
        raise RuntimeError(
            "The legacy Neo4j client is disabled; use the S2P AGE GraphStore"
        )
