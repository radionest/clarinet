"""Common type definitions for the Clarinet framework.

This module provides type aliases for commonly used types across the application,
improving type safety and reducing repetition.
"""

from typing import Any

# JSON-compatible types for API responses and database fields
type JSONDict = dict[str, Any]

# Task-related types
type TaskResult = dict[str, Any]
type SlicerArgs = dict[str, str]
type SlicerResult = dict[str, Any]
type ResultSchema = dict[str, Any]

# Authentication types
type AuthResponse = dict[str, str]
type TokenResponse = dict[str, str]

# API response types
type PaginationParams = dict[str, int | None]
type MessageResponse = dict[str, str]

# Form and validation types
type FormData = dict[str, Any]
type ValidationSchema = dict[str, Any]
