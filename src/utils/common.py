
"""
Common utility functions for the Clarinet framework.

This module provides various utility functions used throughout the framework,
including timing, object copying, and other common operations.
"""

import time
from copy import deepcopy
from functools import wraps
from typing import Any, Callable, ParamSpec, TypeVar, cast

P = ParamSpec('P')
T = TypeVar('T')
R = TypeVar('R')


def timing(func: Callable[P, R]) -> Callable[P, R]:
    """
    Measure and log function execution time.

    Args:
        func: The function to measure

    Returns:
        Wrapped function that logs execution time
    """
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        print(f'func:{func.__name__!r} took: {end_time - start_time:.4f} sec')
        return result
    return wrapper


def copy_object(method: Callable[[Any], T]) -> Callable[[Any], T]:
    """
    Create a copy of an object before modifying it.

    This is useful for creating method chains \
    that don't modify the original object.

    Args:
        method: The method to wrap

    Returns:
        Wrapped method that works on a copy of the object
    """
    @wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> T:
        new_obj = deepcopy(self)
        return method(new_obj, *args, **kwargs)
    return wrapped


def copy_signature(
    source_func: Callable[..., Any]
) -> Callable[[Callable[..., T]], Callable[P, T]]:
    """
    Create a decorator that copies the signature from one function to another.

    Args:
        source_func: The function whose signature should be copied

    Returns:
        A decorator that applies the signature
    """
    def decorator(target_func: Callable[..., T]) -> Callable[P, T]:
        return cast(Callable[P, T], target_func)
    return decorator

