"""
Pipeline node definitions and operations.

This module provides the TaskNode class and related functions for creating,
connecting, and managing pipeline nodes.
"""

from collections import defaultdict
from functools import partial
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Self,
    Set,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

from faststream import Logger, apply_types
from faststream.rabbit import RabbitExchange, RabbitQueue, RabbitRoute
from faststream.broker.utils import default_filter as fs_default_filter

from src.settings import settings
from src.utils.logger import logger

from .core import (
    Message,
    NodeError,
    HandlerError,
    HandlerProtocol,
    PipelineNodeProtocol,
    MessageProcessorProtocol,
)

from .messaging import (
    MessageFilter,
    Condition,
    ConditionalExpression,
    ExceptionalExpression,
)

T = TypeVar("T")


class TaskNode:
    """
    A node in a processing pipeline.

    TaskNode instances represent processing steps in a pipeline. They can be connected
    to form directed graphs of processing steps, with conditional branches and
    exception handling.
    """

    # Class-level exchange references
    _default_exchange: Optional[RabbitExchange] = None
    _deduplication_exchange: Optional[RabbitExchange] = None

    @classmethod
    def set_default_exchange(cls, exchange: RabbitExchange) -> None:
        """Set the default exchange for all nodes."""
        cls._default_exchange = exchange

    @classmethod
    def set_deduplication_exchange(cls, exchange: RabbitExchange) -> None:
        """Set the deduplication exchange for all nodes."""
        cls._deduplication_exchange = exchange

    def __init__(
        self,
        handler: Optional[Union[Type[HandlerProtocol], Callable]] = None,
        queue_name: Optional[str] = None,
        queue_filter: Union[MessageFilter, Callable] = fs_default_filter,
        exchange: Optional[RabbitExchange] = None,
        user_task_name: Optional[str] = None,
        user_task_event: Optional[str] = None,
        db_save: bool = False,
        if_result: Optional[Union[bool, str, int]] = None,
        musthave: Optional[Dict[str, bool]] = None,
        **kwargs: Any,
    ):
        """
        Initialize a TaskNode.

        Args:
            handler: Function or class that processes messages
            queue_name: Name of the queue to consume from
            queue_filter: Filter function for messages
            exchange: RabbitMQ exchange to use
            user_task_name: Name of the task for event publishing
            user_task_event: Event type for task events
            db_save: Whether to save results to the database
            if_result: Conditional result value for routing
            musthave: Environment requirements for this node
            **kwargs: Additional binding arguments for the queue
        """
        # Store configuration
        self.extra_headers = kwargs

        # Create binding arguments for the queue
        bind_args = {
            "dbname": settings.database_name,
            "task_type_name": user_task_name or queue_name,
            "event": user_task_event,
            "result": if_result,
            **kwargs,
        }
        # Filter out None values
        bind_args = {k: v for k, v in bind_args.items() if v is not None}

        # Configure the queue
        self.queue_filter = queue_filter
        self.queue = RabbitQueue(
            queue_name,
            auto_delete=False,
            bind_arguments=bind_args,
            arguments={"x-message-deduplication": True},
        )

        # Set exchanges
        self.exchange = exchange or self._default_exchange
        self.deduplication_exchange = self._deduplication_exchange

        if self.exchange is None:
            raise NodeError(
                "Default exchange not configured. Call TaskNode.set_default_exchange() first."
            )

        # Initialize connection tracking
        self.pubs: Set[Self] = set()
        self.one_to_many_pubs: Set[Self] = set()
        self.subs: Set[Self] = set()
        self.conditional_pubs: Dict[Condition, Set[Self]] = defaultdict(set)
        self.exception_pubs: Dict[Type[Exception], Set[Self]] = defaultdict(set)

        # Initialize processing components
        self.route: Optional[RabbitRoute] = None
        self._handler = None
        self.handler = handler  # Uses the property setter
        self._pipeline = None
        self.publisher = None

        # Additional configuration
        self.db_save = db_save
        self.musthave = musthave or {}

    def where_all(self, **kwargs: Any) -> ConditionalExpression:
        """
        Create a condition requiring all conditions to be true.

        Args:
            **kwargs: Condition key-value pairs

        Returns:
            A conditional expression object
        """
        return ConditionalExpression(
            condition=Condition("all", **kwargs), left_task=self
        )

    def where_any(self, **kwargs: Any) -> ConditionalExpression:
        """
        Create a condition requiring any condition to be true.

        Args:
            **kwargs: Condition key-value pairs

        Returns:
            A conditional expression object
        """
        return ConditionalExpression(
            condition=Condition("any", **kwargs), left_task=self
        )

    def on_exception(self, exception: Type[Exception]) -> ExceptionalExpression:
        """
        Create an exception handler route.

        Args:
            exception: Exception type to handle

        Returns:
            An exception expression object
        """
        return ExceptionalExpression(exception=exception, left_task=self)

    def set_publisher(self, publisher: Any) -> None:
        """
        Set the message publisher for this node.

        Args:
            publisher: FastStream publisher
        """
        self.publisher = publisher

    def get_pipeline_router(self) -> Any:
        """
        Get a router containing all connected nodes.

        Returns:
            A configured router
        """
        # Lazy import to avoid circular imports
        from faststream.rabbit import RabbitRouter

        # Create router with handlers for all connected nodes
        self.router = RabbitRouter(
            handlers=[
                n.create_route()
                for n in self.get_siblings()
                if n.create_route() is not None
            ]
        )

        # Set up the publisher
        self.publisher = self.router.publisher(
            routing_key=self.queue.name,
            exchange=self.exchange,
            mandatory=False,
            timeout=300,
        )

        # Share the publisher with all connected nodes
        for p in self.get_siblings():
            p.set_publisher(self.publisher)

        return self.router

    @property
    def handler(self) -> Callable:
        """Get the message handler function."""
        if self._handler is None:
            raise NodeError("No handler configured for this node")
        return self._publish_one_to_many(self._handler)

    @handler.setter
    def handler(self, f: Optional[Union[Type[HandlerProtocol], Callable]]) -> None:
        """Set the message handler function or class."""
        self._handler = f

    @staticmethod
    def get_deduplication_header(message: Message, node: Self) -> str:
        """
        Generate a deduplication header for a message.

        Args:
            message: The message to deduplicate
            node: The node processing the message

        Returns:
            A unique deduplication header string
        """
        return "{uid}_{queue_name}_{bind_args}".format(
            uid=message.series_uid or message.study_uid,
            queue_name=node.queue.name,
            bind_args="_".join(
                f"{k}-{v}" for k, v in (node.queue.bind_arguments or {}).items()
            ),
        )

    async def _publish(
        self, message: Any, node: Self, input_headers: Dict[str, Any], msg: Any
    ) -> None:
        """
        Publish a message to another node.

        Args:
            message: Message to publish
            node: Target node
            input_headers: Headers to include
            msg: Original message for correlation
        """
        if self.publisher is None:
            raise NodeError("Publisher not configured")

        # Prepare headers
        pub_headers = input_headers.copy()
        pub_headers.update(node.queue.bind_arguments or {})
        pub_headers["x-deduplication-header"] = self.get_deduplication_header(
            message, node
        )

        logger.info(
            f"Publishing to {node.queue.name}. "
            f"Deduplication: {pub_headers['x-deduplication-header']}"
        )

        try:
            # Publish the message
            await self.publisher.publish(
                message,
                queue=node.queue,
                headers=pub_headers,
                correlation_id=getattr(msg, "correlation_id", None),
                mandatory=False,
                timeout=60,
            )
        except TimeoutError:
            logger.debug("Timeout in publishing")

        logger.info(f"Finished publishing from {self.queue.name}")

    def _publish_one_to_many(
        self, handle_func: Union[Type[HandlerProtocol], Callable]
    ) -> Callable:
        """
        Wrap a handler function to support one-to-many publishing.

        Args:
            handle_func: Handler function or class

        Returns:
            Wrapped handler function
        """
        # Initialize handler (class instance if class provided)
        if isinstance(handle_func, type) and issubclass(handle_func, HandlerProtocol):
            handler = handle_func()
        else:
            handler = handle_func

        @apply_types
        async def handle_with_publisher(
            body: Any,
            msg: Any,
            input_headers: Dict[str, Any] = None,
        ) -> Any:
            try:
                # Execute the handler
                result = await handler(body)
            except tuple(self.exception_pubs.keys()) as e:
                # Handle expected exceptions
                logger.error(f"Caught exception {e.__class__.__name__}: {e}")

                for p in self.exception_pubs[e.__class__]:
                    await self._publish(
                        Message(**body) if not isinstance(body, Message) else body,
                        p,
                        input_headers or {},
                        msg=msg,
                    )

                # Re-raise as a FastStream NackMessage to reject the message
                from faststream.exceptions import NackMessage

                raise NackMessage()

            except (HandlerError, FileNotFoundError) as e:
                # Log but acknowledge these errors
                logger.error(f"Non-retriable error: {e.__class__.__name__} - {e}")
                from faststream.exceptions import AckMessage

                raise AckMessage()

            except Exception as e:
                # Log and reject unexpected errors
                logger.error(f"Unexpected error: {e}")
                from faststream.exceptions import NackMessage

                raise NackMessage()

            # Publish to one-to-many targets (list results)
            for p in self.one_to_many_pubs:
                for r in result:
                    await self._publish(r, p, input_headers or {}, msg=msg)

            # Publish based on conditional expressions
            for cond, pubs in self.conditional_pubs.items():
                if hasattr(result, "result") and cond.check_result(result.result):
                    for p in pubs:
                        await self._publish(result, p, input_headers or {}, msg=msg)

            # Publish to regular targets
            for p in self.pubs:
                # Add result fields to headers if specified
                if hasattr(result, "result"):
                    for header_extra_field in p.extra_headers:
                        extra_field_value = result.result.get(header_extra_field)
                        if extra_field_value is not None:
                            if input_headers is None:
                                input_headers = {}
                            input_headers[header_extra_field] = extra_field_value

                await self._publish(result, p, input_headers or {}, msg=msg)

            return result or body

        return handle_with_publisher

    def create_route(self) -> Optional[RabbitRoute]:
        """
        Create a route for this node.

        Returns:
            RabbitRoute or None if this node should be disabled
        """
        # Check if this node's requirements are met
        if self.musthave and not self._check_requirements():
            return None

        # Lazy import to avoid circular imports
        from faststream.rabbit import RabbitRoute

        return RabbitRoute(
            call=self.handler,
            queue=self.queue,
            exchange=self.exchange,
            publishers=[],
            filter=self.queue_filter,
            retry=5,
        )

    def _check_requirements(self) -> bool:
        """
        Check if the node's requirements are met.

        Returns:
            True if all requirements are met, False otherwise
        """
        for feature, required in self.musthave.items():
            if not hasattr(settings, feature) or getattr(settings, feature) != required:
                return False
        return True

    def __gt__(self, other: Union[Self, List[Self]]) -> Union[Self, List[Self]]:
        """
        Connect this node to another node or list of nodes.

        Args:
            other: Target node or nodes

        Returns:
            The target node(s) for method chaining
        """
        if isinstance(other, list):
            self.one_to_many_pubs.update(other)
            for s in other:
                s.subs.add(self)
            return other
        else:
            self.pubs.add(other)
            other.subs.add(self)
            return other

    def __rshift__(self, other: Self) -> Self:
        """
        Inherit the publishing targets of another node.

        Args:
            other: Node whose targets to inherit

        Returns:
            The target node for method chaining
        """
        self.pubs.update(other.pubs)
        for p in other.pubs:
            p.subs.add(self)
        return other

    def __lt__(self, other: Self) -> Self:
        """
        Make this node a subscriber to another node.

        Args:
            other: Node to subscribe to

        Returns:
            The other node for method chaining
        """
        self.subs.add(other)
        other.pubs.add(self)
        return other

    def get_siblings(
        self, result_siblings_list: Optional[Set[Self]] = None
    ) -> Set[Self]:
        """
        Get all nodes connected to this node.

        Args:
            result_siblings_list: Set to add results to (for recursion)

        Returns:
            Set of all connected nodes
        """
        if result_siblings_list is None:
            result_siblings_list = set()

        # Add self to results
        result_siblings_list.add(self)

        # Collect all directly connected nodes
        siblings = set()
        siblings.update(self.subs)
        siblings.update(self.pubs)
        siblings.update(self.one_to_many_pubs)

        # Add all nodes from conditional connections
        for pubs_set in self.conditional_pubs.values():
            siblings.update(pubs_set)

        # Add all nodes from exception connections
        for pubs_set in self.exception_pubs.values():
            siblings.update(pubs_set)

        # Process nodes not already in results
        not_included_siblings = siblings - result_siblings_list
        for s in not_included_siblings:
            s.get_siblings(result_siblings_list=result_siblings_list)

        return result_siblings_list


@overload
def create_node(
    handler: Callable[..., Any],
    /,
) -> TaskNode: ...


@overload
def create_node(
    *,
    queue_name: Optional[str] = None,
    queue_filter: Union[MessageFilter, Callable] = fs_default_filter,
    exchange: Optional[RabbitExchange] = None,
    user_task_name: Optional[str] = None,
    user_task_event: Optional[str] = None,
    db_save: bool = False,
    if_result: Optional[Union[bool, str, int]] = None,
    musthave: Optional[Dict[str, bool]] = None
) -> Callable[[Callable[..., Any]], TaskNode]: ...


def create_node(
    handler: Optional[Callable[..., Any]] = None, /, **kwargs: Any
) -> Union[TaskNode, Callable[[Callable[..., Any]], TaskNode]]:
    """
    Create a TaskNode or a decorator that creates a TaskNode.

    This function can be used in two ways:
    1. As a decorator: @create_node
    2. With arguments: @create_node(queue_name="my_queue")

    Args:
        handler: Handler function (when used as @create_node)
        **kwargs: Arguments to pass to TaskNode constructor

    Returns:
        TaskNode or a decorator that returns a TaskNode
    """
    if handler is not None:
        # Used as @create_node without arguments
        if "queue_name" not in kwargs:
            kwargs["queue_name"] = handler.__name__

        # Apply types to handler for FastStream compatibility
        typed_handler = apply_types(handler)
        return TaskNode(handler=typed_handler, **kwargs)
    else:
        # Used as @create_node(arg=value)
        def decorator(func: Callable[..., Any]) -> TaskNode:
            if "queue_name" not in kwargs:
                kwargs["queue_name"] = func.__name__

            # Apply types to handler for FastStream compatibility
            typed_func = apply_types(func)
            return TaskNode(handler=typed_func, **kwargs)

        return decorator


def disable_node(node: TaskNode) -> None:
    """
    Disable a node in the pipeline.

    Args:
        node: The node to disable
    """
    # In Python, del doesn't actually delete the object, just removes the reference.
    # For our purposes, we can just clear the node's connections to effectively disable it.
    node.pubs.clear()
    node.subs.clear()
    node.one_to_many_pubs.clear()
    node.conditional_pubs.clear()
    node.exception_pubs.clear()


class NodeTask:
    def __init__(self, message,siblings,handler):
        self.handler=handler
        self.result=None
        self.message = message
        self.siblings = NodeConnections(siblings)

        result = await self.handle_task()
        self.generate_siblings_tasks(result)

    async def handle_task(self):
        return await self.handler(self.message)
    def generate_siblings_tasks(self, result):
        for s in self.siblings:
            NodeTask(self.message, pipe, )


class Node:
    connections: NodeConnections
    handler: NodeHandler
    messanger: NodeMessanger

    async def run(self, message):
        result = await self.handler.handle(message) 
        
        self.generate_children_nodes(result)


class Pipe:
    def __init__(self, *nodes):
        ...
    
