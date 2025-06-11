"""
Core types and interfaces for the pipeline framework.

This module provides the base classes, interfaces, types and exceptions that form
the foundation of the pipeline processing system.
"""

from typing import (
    TypeVar, Generic, Optional, Dict, List, Any, Self, 
    Union, Literal, Annotated, Protocol
)
from pydantic import BaseModel, Field

from faststream.broker.message import StreamMessage

# Type definitions
ExpressionType = Literal["all", "any"]
T = TypeVar("T")


class PipelineError(Exception):
    """Base exception for all pipeline-related errors."""
    pass


class NodeError(PipelineError):
    """Error related to pipeline node operations."""
    pass


class HandlerError(PipelineError):
    """Error related to task handler execution."""
    pass


class DependentTaskNotFinished(HandlerError):
    """Error indicating a dependent task has not yet finished."""
    pass


class TokenData(BaseModel):
    """Token data for authentication."""
    username: str
    exp: Optional[Any] = None


class Message(BaseModel):
    """Base message type for pipeline communications."""
    
    patient_id: str
    study_uid: Optional[str] = None
    series_uid: Optional[str] = None
    
    # These are populated by a relationship and not included in serialization
    study: Optional[Any] = Field(default=None, exclude=True)
    task: Optional[Any] = Field(default=None, exclude=True)
    
    class Config:
        arbitrary_types_allowed = True




class PipelineNodeProtocol(Protocol):
    """Protocol defining the interface for pipeline nodes."""
    
    def create_route(self) -> Any:
        """Create a route for this node."""
        ...
        
    def get_siblings(self) -> set[Self]:
        """Get all connected nodes."""
        ...
        
    def __gt__(self, other: Union[Self, List[Self]]) -> Self:
        """Connect this node to another node or nodes."""
        ...

class Node:
    ...

class NodeDSL:
    def __init__(self, node: Node, pipeline: 'PipelineProtocol'):
        self.node = node
        self.pipeline = pipeline

    def __gt__(self, right: Self):
        self.pipeline.add_connection(self.node, right.node, None)

    

class NodeResultExpression:
    second_expression = 
    
    def __and__(self, right: Self) -> Self:
        return self
    
    def check_single_expression(self, expression):
        getattr(self.node.result, expression.result_name)


    def check_multi_expression(self):
        

class NodeResult:
    def __init__(self, node:Node, pipeline: 'PipelineProtocol', result_attribute: str):s
        self.node = node
        self.pipeline = pipeline
        self.result_attribute = result_attribute
        self.comparison = None
    
    def __gt__(self, right) -> NodeResultExpression:
        add_conditional_connection_to_pipilen(, 
                                               
                                              condition=self)

    def add_conditional_connection_to_pipilen():
        ...

    def __call__(self) -> Bool:

        self.operator(
            getattr(self.node, self.result_attribute), 
            self.comparison) 
        
        
        



class PipelineProtocol(Protocol):
    def add_connection(self, input_node, output_node, condition) -> None:
        ...


(compare_seg_ai_vs_doc.calcifications > 5) > give_task('compare_false_segmentation')