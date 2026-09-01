"""Project-specific exception hierarchy."""

from __future__ import annotations


class CnapFmriPrepError(RuntimeError):
    """Base class for expected, user-facing pipeline failures."""


class ValidationError(CnapFmriPrepError):
    """Raised when an input or intermediate violates a pipeline invariant."""


class ExternalCommandError(CnapFmriPrepError):
    """Raised when an external command cannot be found or exits unsuccessfully."""


class WorkflowError(CnapFmriPrepError):
    """Raised when the workflow engine cannot produce a valid result."""
