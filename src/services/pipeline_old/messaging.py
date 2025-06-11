"""
Message queue integration for pipeline processing.

This module provides components for working with message queues, including
message filtering, conditional expressions, and broker setup.
"""

from typing import Any, Callable, Dict, Generic, Optional, Self, Set, TypeVar, Type, cast
from collections import defaultdict

from faststream.broker.message import StreamMessage
from faststream.broker.utils import default_filter as fs_default_filter
from faststream.rabbit import RabbitExchange, ExchangeType

from src.utils.logger import logger
from src.settings import settings

from .core import ExpressionType, NodeError, PipelineError

T = TypeVar('T')


class PublisherOneToManyError(PipelineError):
    """Error when trying to publish a single message to multiple destinations."""
    pass


class MessageFilter:
    """
    Filter for message routing based on content.
    
    This class allows filtering messages based on their content, particularly
    result fields in task results.
    """
    
    def __init__(self, **kwargs: Dict[str, Any]) -> None:
        """
        Initialize a MessageFilter.
        
        Args:
            **kwargs: Key-value pairs to match in message results
        """
        self.conditions = kwargs
    
    async def __call__(self, msg: StreamMessage) -> bool:
        """
        Apply the filter to a message.
        
        Args:
            msg: The message to filter
            
        Returns:
            True if the message matches the filter, False otherwise
        """
        msg_result = await msg.decode()
        if not isinstance(msg_result, dict):
            return bool(fs_default_filter)
            
        # Check for a result field in the message
        result = msg_result.get("result", {})
        
        # Check if all conditions are met
        return all(
            result.get(condition[0]) == condition[1] 
            for condition in self.conditions.items()
        )
    
    def __repr__(self) -> str:
        """String representation of the filter."""
        return " & ".join([f"{k}=={v}" for k, v in self.conditions.items()])


class Condition:
    """
    Condition for routing messages based on result content.
    
    This class represents a condition that can be used to route messages
    based on their content, with support for both AND and OR logic.
    """
    
    def __init__(self, expr: ExpressionType, **kwargs: Dict[str, Any]) -> None:
        """
        Initialize a Condition.
        
        Args:
            expr: Expression type ('all' for AND, 'any' for OR)
            **kwargs: Key-value pairs to match in message results
        """
        self.conditions: Dict[str, Any] = kwargs
        self.expr_type = expr
    
    def check_result(self, task_result: Dict[str, Any]) -> bool:
        """
        Check if a task result meets the condition.
        
        Args:
            task_result: The task result to check
            
        Returns:
            True if the condition is met, False otherwise
        """
        match self.expr_type:
            case "all":
                # All conditions must be true (AND)
                result = all(
                    task_result.get(cond[0]) == cond[1]
                    for cond in self.conditions.items()
                )
                logger.info(f'Condition AND: {result}. '
                           f'Task result {task_result}, Conditions {self.conditions}')
                return result
            case "any":
                # Any condition must be true (OR)
                result = any(
                    task_result.get(cond[0]) == cond[1]
                    for cond in self.conditions.items()
                )
                logger.info(f'Condition OR: {result}. '
                           f'Task result {task_result}, Conditions {self.conditions}')
                return result
            case _:
                raise NotImplementedError(f"Unknown expression type: {self.expr_type}")
    
    def __repr__(self) -> str:
        """String representation of the condition."""
        match self.expr_type:
            case "all":
                return " & ".join([f"{k}=={v}" for k, v in self.conditions.items()])
            case "any":
                return " OR ".join([f"{k}=={v}" for k, v in self.conditions.items()])
            case _:
                raise NotImplementedError(f"Unknown expression type: {self.expr_type}")


class ConditionalExpression:
    """
    Expression for conditional message routing.
    
    This class represents a conditional expression that can be used to route
    messages based on their content using the > operator.
    """
    
    def __init__(self, condition: Condition, left_task: Any) -> None:
        """
        Initialize a ConditionalExpression.
        
        Args:
            condition: Condition to check
            left_task: Source node
        """
        self.condition = condition
        self.left_task = left_task
    
    def __gt__(self, right_task: Any) -> Any:
        """
        Connect the left task to the right task with this condition.
        
        Args:
            right_task: Target node
            
        Returns:
            The right task for method chaining
        """
        self.left_task.conditional_pubs[self.condition].add(right_task)
        right_task.subs.add(self.left_task)
        return right_task


class ExceptionalExpression:
    """
    Expression for exception-based message routing.
    
    This class represents an exceptional expression that can be used to route
    messages when exceptions occur using the > operator.
    """
    
    def __init__(self, exception: Type[Exception], left_task: Any) -> None:
        """
        Initialize an ExceptionalExpression.
        
        Args:
            exception: Exception type to handle
            left_task: Source node
        """
        self.exception = exception
        self.left_task = left_task
    
    def __gt__(self, right_task: Any) -> Any:
        """
        Connect the left task to the right task for this exception.
        
        Args:
            right_task: Target node
            
        Returns:
            The right task for method chaining
        """
        self.left_task.exception_pubs[self.exception].add(right_task)
        right_task.subs.add(self.left_task)
        return right_task
    
    def __rshift__(self, other: Any) -> Any:
        """
        Inherit the publishing targets of another node for this exception.
        
        Args:
            other: Node whose targets to inherit
            
        Returns:
            The other node for method chaining
        """
        self.left_task.exception_pubs[self.exception].update(other.pubs)
        for p in other.pubs:
            p.subs.add(self.left_task)
        return other


async def publisher_middleware(
    call_next: Callable[..., Any],
    msg: Any,
    **options: Any,
) -> Any:
    """
    Middleware for message publishing.
    
    Args:
        call_next: Next middleware function
        msg: Message being published
        **options: Additional options
        
    Returns:
        Result from the next middleware
    """
    logger.info(f"Publishing message: {options}")
    logger.debug(f"Message content: {msg}")
    return await call_next(msg, **options)


def setup_message_broker(app: Any = None) -> tuple[RabbitExchange, RabbitExchange]:
    """
    Set up the message broker exchanges.
    
    Args:
        app: FastAPI application (optional)
        
    Returns:
        Tuple of (main exchange, deduplication exchange)
    """
    from faststream.rabbit import RabbitExchange
    
    # Create main exchange
    main_exchange = RabbitExchange(
        settings.database_name, 
        type=ExchangeType.HEADERS, 
        durable=True
    )
    
    # Create deduplication exchange (if needed)
    dedup_exchange = RabbitExchange(
        f"{settings.database_name}_dedup", 
        type=ExchangeType.HEADERS, 
        durable=True
    )
    
    # Configure application if provided
    if app is not None:
        # Add exchanges to app context
        app.state.main_exchange = main_exchange
        app.state.dedup_exchange = dedup_exchange
    
    return main_exchange, dedup_exchange