"""
Pipeline visualization utilities.

This module provides functions for visualizing pipelines as diagrams
and exporting them to various formats.
"""

import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from src.utils.logger import logger
from src.settings import settings

# Import TaskNode type for type hints, but use string to avoid circular imports
TaskNodeType = 'TaskNode'  # For type hints


def export_pipeline_diagram(
    root_node: Any,
    output_path: Union[str, Path],
    filename: str = "pipeline_diagram.md",
    additional_nodes: Optional[Set[Any]] = None,
    include_task_schema: bool = True
) -> Path:
    """
    Export a pipeline diagram to a Markdown file with Mermaid syntax.
    
    Args:
        root_node: Root node of the pipeline
        output_path: Directory to save the diagram
        filename: Name of the diagram file
        additional_nodes: Additional nodes to include
        include_task_schema: Whether to include task schema information
        
    Returns:
        Path to the generated diagram file
    """
    # Ensure the output directory exists
    output_dir = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create the diagram file path
    diagram_path = output_dir / filename
    
    # Get all connected nodes
    pipeline_nodes = root_node.get_siblings()
    if additional_nodes:
        pipeline_nodes.update(additional_nodes)
    
    with open(diagram_path, "w") as diagram_file:
        # Start the Mermaid diagram
        diagram_file.write("```mermaid\n")
        diagram_file.write("flowchart TD\n")
        
        # Add nodes
        for node in pipeline_nodes:
            # Node definition
            diagram_file.write(f'{node.queue.name}["')
            diagram_file.write(f"**{node.queue.name}**\n")
            
            # Add handler info
            handler_name = node._handler.__class__.__name__ if hasattr(node._handler, "__class__") else str(node._handler)
            diagram_file.write(f"+{handler_name}\n")
            
            # Add task schema info if available and requested
            if include_task_schema and node.db_save:
                task_schema = _get_task_schema(node.queue.name)
                if task_schema:
                    # Extract properties from schema
                    schema_props = _extract_schema_properties(task_schema)
                    diagram_file.write("\n".join(schema_props))
            
            diagram_file.write('"]\n')
        
        # Add connections
        for node in pipeline_nodes:
            node_name = node.queue.name
            
            # Regular connections
            for target in node.pubs:
                target_name = target.queue.name
                headers = ",".join(f"{k}=={v}" for k, v in target.extra_headers.items())
                diagram_file.write(f"{node_name}--{headers}-->{target_name}\n")
            
            # One-to-many connections
            for target in node.one_to_many_pubs:
                target_name = target.queue.name
                headers = ",".join(f"{k}=={v}" for k, v in target.extra_headers.items())
                diagram_file.write(f"{node_name}--{headers}--o{target_name}\n")
            
            # Conditional connections
            for condition, targets in node.conditional_pubs.items():
                for target in targets:
                    target_name = target.queue.name
                    diagram_file.write(f"{node_name}--{condition}-->{target_name}\n")
            
            # Exception connections
            for exception_type, targets in node.exception_pubs.items():
                for target in targets:
                    target_name = target.queue.name
                    diagram_file.write(f"{node_name}-.{exception_type.__name__}.->{target_name}\n")
        
        # End the Mermaid diagram
        diagram_file.write("```")
    
    logger.info(f"Pipeline diagram exported to {diagram_path}")
    return diagram_path


def visualize_pipeline(
    root_node: Any,
    output_format: str = "mermaid",
    include_task_schema: bool = True
) -> str:
    """
    Generate a visualization of the pipeline.
    
    Args:
        root_node: Root node of the pipeline
        output_format: Format of the visualization ('mermaid', 'dot', 'svg')
        include_task_schema: Whether to include task schema information
        
    Returns:
        Visualization as a string
    """
    pipeline_nodes = root_node.get_siblings()
    
    if output_format == "mermaid":
        # Generate Mermaid diagram
        mermaid = ["```mermaid", "flowchart TD"]
        
        # Add nodes
        for node in pipeline_nodes:
            # Node definition
            handler_name = node._handler.__class__.__name__ if hasattr(node._handler, "__class__") else str(node._handler)
            node_def = f'{node.queue.name}["{node.queue.name}<br>{handler_name}"]'
            mermaid.append(node_def)
        
        # Add connections
        for node in pipeline_nodes:
            node_name = node.queue.name
            
            # Regular connections
            for target in node.pubs:
                target_name = target.queue.name
                mermaid.append(f"{node_name}-->{target_name}")
            
            # One-to-many connections
            for target in node.one_to_many_pubs:
                target_name = target.queue.name
                mermaid.append(f"{node_name}--o{target_name}")
            
            # Conditional connections
            for condition, targets in node.conditional_pubs.items():
                for target in targets:
                    target_name = target.queue.name
                    mermaid.append(f"{node_name}-->{target_name}")
            
            # Exception connections
            for exception_type, targets in node.exception_pubs.items():
                for target in targets:
                    target_name = target.queue.name
                    mermaid.append(f"{node_name}-.->{target_name}")
        
        # End the Mermaid diagram
        mermaid.append("```")
        
        return "\n".join(mermaid)
    
    elif output_format == "dot":
        # Generate GraphViz DOT format
        dot = ["digraph Pipeline {", "  node [shape=box];"]
        
        # Add nodes
        for node in pipeline_nodes:
            handler_name = node._handler.__class__.__name__ if hasattr(node._handler, "__class__") else str(node._handler)
            dot.append(f'  "{node.queue.name}" [label="{node.queue.name}\\n{handler_name}"];')
        
        # Add connections
        for node in pipeline_nodes:
            node_name = node.queue.name
            
            # Regular connections
            for target in node.pubs:
                target_name = target.queue.name
                dot.append(f'  "{node_name}" -> "{target_name}";')
            
            # One-to-many connections
            for target in node.one_to_many_pubs:
                target_name = target.queue.name
                dot.append(f'  "{node_name}" -> "{target_name}" [style=dashed];')
            
            # Conditional connections
            for condition, targets in node.conditional_pubs.items():
                for target in targets:
                    target_name = target.queue.name
                    dot.append(f'  "{node_name}" -> "{target_name}" [label="{condition}"];')
            
            # Exception connections
            for exception_type, targets in node.exception_pubs.items():
                for target in targets:
                    target_name = target.queue.name
                    dot.append(f'  "{node_name}" -> "{target_name}" [style=dotted, label="{exception_type.__name__}"];')
        
        # End the DOT diagram
        dot.append("}")
        
        return "\n".join(dot)
        
    elif output_format == "svg":
        # For SVG, we'd need to convert from DOT using a library like graphviz
        # This would require an additional dependency, so we'll return a message instead
        return "SVG output requires the graphviz library. Install it with 'pip install graphviz'."
    
    else:
        return f"Unsupported output format: {output_format}"


def _get_task_schema(task_name: str) -> Optional[Dict[str, Any]]:
    """
    Get the task schema for a task type.
    
    Args:
        task_name: Name of the task type
        
    Returns:
        Task schema dictionary or None if not found
    """
    # Look for schema file in configured task directories
    for task_dir in getattr(settings, "task_dirs", []):
        schema_path = Path(task_dir) / f"{task_name}.schema.json"
        if schema_path.exists():
            try:
                with open(schema_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading schema for {task_name}: {e}")
                return None
    
    return None


def _extract_schema_properties(schema: Dict[str, Any]) -> List[str]:
    """
    Extract property descriptions from a task schema.
    
    Args:
        schema: Task schema dictionary
        
    Returns:
        List of property description strings
    """
    result = []
    
    # Extract properties
    properties = schema.get("properties", {})
    for prop_name, prop_data in properties.items():
        prop_desc = prop_data.get("description", "")
        prop_type = prop_data.get("type", "")
        
        # Add property to result
        result.append(f"* {prop_name} ({prop_type}): {prop_desc}")
    
    return result