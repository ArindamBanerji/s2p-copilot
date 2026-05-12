"""Protocol for canonical S2P factor computers."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class FactorComputer(Protocol):
    """Factor computer whose name must match S2PDomainConfig factors exactly."""

    @property
    def name(self) -> str:
        ...

    def compute(
        self,
        invoice: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> float:
        ...
