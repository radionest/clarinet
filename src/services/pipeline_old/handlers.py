"""
Task execution handlers for pipeline processing.

This module provides handler classes for processing tasks in the pipeline,
including the base TaskHandler and simpler implementations.
"""

from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union, cast, overload

from faststream import Logger, Context, Depends, apply_types
from faststream.broker.message import StreamMessage
from faststream.rabbit import RabbitMessage

from src.utils.logger import logger
from src.exceptions import INTERNAL_SERVER_ERROR

from .core import Message, HandlerError, HandlerProtocol

T = TypeVar('T')
R = TypeVar('R')


@apply_types
def _get_header(header: dict = Context("message.headers")) -> Dict[str, Any]:
    """
    Get the headers from the message context.
    
    Args:
        header: Message headers from context
        
    Returns:
        Dictionary of headers
    """
    return header


@apply_types
def _get_body(body = Context("message.decoded_body")) -> Any:
    """
    Get the decoded body from the message context.
    
    Args:
        body: Message body from context
        
    Returns:
        Decoded message body
    """
    return body


@apply_types
def _get_logger(logger: Logger) -> Logger:
    """
    Get the logger from the context.
    
    Args:
        logger: Logger from context
        
    Returns:
        Logger instance
    """
    return logger


class TaskHandler(Generic[T, R]):
    """
    Base class for task handlers.
    
    This class provides the foundation for implementing custom task handlers,
    with support for common dependencies and error handling.
    """
    
    @apply_types
    def __init__(self) -> None:
        """Initialize a TaskHandler."""
        self.logger = None
        self.input_message = None
        self.output_message = None
    
    async def transform(self) -> R:
        """
        Transform input data into output data.
        
        This method should be implemented by subclasses to process task data.
        
        Returns:
            Processed result
            
        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError("Subclasses must implement transform()")
    
    @overload 
    @apply_types
    async def __call__(
        self,
        body: List[T],
        *,
        msg: RabbitMessage,
        logger=Depends(_get_logger),
        header: Dict[str, Any] = Depends(_get_header)
    ) -> List[R]: ...
    
    @overload 
    @apply_types
    async def __call__(
        self,
        body: T,
        *,
        msg: RabbitMessage,
        logger=Depends(_get_logger),
        header: Dict[str, Any] = Depends(_get_header)
    ) -> R: ...
    
    @apply_types
    async def __call__(
        self,
        body: Union[T, List[T]],
        *,
        msg: RabbitMessage,
        logger=Depends(_get_logger),
        header: Dict[str, Any] = Depends(_get_header)
    ) -> Union[R, List[R]]:
        """
        Process a message.
        
        Args:
            body: Message body
            msg: RabbitMQ message
            logger: Logger
            header: Message headers
            
        Returns:
            Processing result
            
        Raises:
            Various exceptions based on processing outcome
        """
        self.logger = logger
        self.header = header
        self.input_message = body

        self.logger.info(f'Processing message in {self.__class__.__name__}')
        
        try:
            self.output_message = await self.transform()
        except Exception as e:
            self.logger.error(f"Error in {self.__class__.__name__}: {str(e)}")
            # Re-raise for pipeline error handling
            raise
            
        return self.output_message or self.input_message


class SimpleHandler(TaskHandler[Dict[str, Any], Dict[str, Any]]):
    """
    A simple handler that processes dictionary data.
    
    This class provides a simplified handler implementation for common cases
    where both input and output are dictionaries.
    """
    
    async def transform(self) -> Dict[str, Any]:
        """
        Transform the input dictionary.
        
        This method can be overridden by subclasses to provide specific processing.
        The default implementation returns the input unchanged.
        
        Returns:
            Processed dictionary
        """
        # Default implementation returns the input unchanged
        return cast(Dict[str, Any], self.input_message)


def create_message_processor(handler_func: callable) -> callable:
    """
    Create a message processor from a handler function.
    
    Args:
        handler_func: Function to process messages
        
    Returns:
        Message processor function
    """
    @apply_types
    async def processor(
        body: Any,
        msg: RabbitMessage,
        logger=Depends(_get_logger),
        header: Dict[str, Any] = Depends(_get_header)
    ) -> Any:
        """
        Process a message with the handler function.
        
        Args:
            body: Message body
            msg: RabbitMQ message
            logger: Logger
            header: Message headers
            
        Returns:
            Processing result
        """
        logger.info(f"Processing message with {handler_func.__name__}")
        
        try:
            result = await handler_func(body)
            return result
        except Exception as e:
            logger.error(f"Error in {handler_func.__name__}: {str(e)}")
            raise HandlerError(f"Processing failed: {str(e)}")
    
    return processor