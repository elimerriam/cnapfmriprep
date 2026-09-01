"""Project-specific exception hierarchy."""

from __future__ import annotations


class SeventPrepError(RuntimeError):
    """Base class for expected, user-facing pipeline failures."""


class ValidationError(SeventPrepError):
    """Raised when an input or intermediate violates a pipeline invariant."""


class ExternalCommandError(SeventPrepError):
    """Raised when an external command cannot be found or exits unsuccessfully."""


class WorkflowError(SeventPrepError):
    """Raised when the workflow engine cannot produce a valid result."""
