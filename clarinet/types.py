"""Common type definitions for the Clarinet framework.

This module provides type aliases for commonly used types across the application,
improving type safety and reducing repetition.
"""

from typing import Annotated, Any

from annotated_types import Ge, Gt, Le

# JSON-compatible types for API responses and database fields
type JSONDict = dict[str, Any]

type RecordData = dict[str, Any]
type SlicerArgs = dict[str, str]
type SlicerResult = dict[str, Any]
type RecordSchema = dict[str, Any]
type RecordContextInfo = dict[str, str | int | float | "RecordContextInfo"]
type SlicerHydratorNames = list[str]

# Authentication types
type AuthResponse = dict[str, str]
type TokenResponse = dict[str, str]

# API response types
type PaginationParams = dict[str, int | None]
type MessageResponse = dict[str, str]

# Form and validation types
type FormData = dict[str, Any]
type ValidationSchema = dict[str, Any]

# Database-constrained integer types
DbInt64 = Annotated[int, Ge(-(2**63)), Le(2**63 - 1)]
DbPositiveInt32 = Annotated[int, Gt(0), Le(2**31 - 1)]
