"""
Slicer web interface utilities for the Clarinet framework.

This module provides classes and functions for interacting with Slicer Web,
executing scripts remotely, and validating segmentations.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Union, cast

import requests

from src.exceptions import (
    NoScriptError,
    ScriptArgumentError,
    SlicerConnectionError,
    SlicerError,
    SlicerSegmentationError,
)
from src.settings import settings
from src.utils.logger import logger


def clean_value(value: str) -> str:
    """
    Clean a value string by removing single and double quotes.

    Args:
        value: The value to clean

    Returns:
        Cleaned value string
    """
    return re.sub(r"[\'\"]", "", value)


def input_values_to_template(template_line: str, values_dict: Dict[str, Any]) -> str:
    """
    Replace template placeholders with actual values.

    Args:
        template_line: The template line containing placeholders
        values_dict: Dictionary of values to insert

    Returns:
        The template line with values inserted

    Raises:
        ScriptArgumentError: If a required value is missing
    """
    # If no placeholder marker in the line, return unchanged
    if "###" not in template_line:
        return template_line

    # Extract the placeholder key
    value_key = template_line.partition("###")[-1].strip()

    try:
        new_value = values_dict[value_key]
    except KeyError:
        raise ScriptArgumentError(f"Missing required parameter: {value_key}")

    # Format value based on type
    if isinstance(new_value, str):
        new_value = f"'{clean_value(new_value)}'"

    # Replace the placeholder with the value
    return re.sub(r"=(.*)###", f"= {new_value} #", template_line)


def script_from_template(script_path: Union[str, Path], **kwargs: Any) -> str:
    """
    Create a script from a template by replacing placeholders with values.

    Args:
        script_path: Path to the script template
        **kwargs: Values to insert into the template

    Returns:
        The complete script with values inserted

    Raises:
        FileNotFoundError: If the script file doesn't exist
        ScriptArgumentError: If a required value is missing
    """
    with open(os.path.abspath(script_path), "r") as f:
        script_lines = f.readlines()

    processed_lines = [input_values_to_template(line, kwargs) for line in script_lines]
    return "".join(processed_lines)


class SlicerWeb:
    """
    Interface for interacting with 3D Slicer via its web server.

    This class provides methods to execute scripts in a remote Slicer instance,
    run template-based scripts, and validate segmentation results.
    """

    def __init__(self, url: str):
        """
        Initialize a SlicerWeb connection.

        Args:
            url: The URL of the Slicer web server
        """
        self.url = url
        # Verify connection with a simple test
        self.exec()

    def exec(self, script: str = "print()") -> Dict[str, Any]:
        """
        Execute a script in Slicer.

        Args:
            script: The Python script to execute (defaults to a harmless print)

        Returns:
            The JSON response from Slicer

        Raises:
            SlicerConnectionError: If unable to connect to Slicer
            SlicerError: If Slicer returns an error
        """
        try:
            response = requests.post(
                f"{self.url}/slicer/exec", data=script, timeout=5.0
            )
        except requests.ConnectionError:
            raise SlicerConnectionError(f"Cannot connect to Slicer at {self.url}")
        except requests.Timeout:
            raise SlicerConnectionError(f"Connection to Slicer at {self.url} timed out")

        if response.status_code != 200:
            logger.error(f"Slicer error: {response.status_code} - {response.text}")
            logger.error(f"Script: {script}")
            raise SlicerError(f"Slicer execution failed: {response.text}")

        return response.json()

    def run_script(self, script_name: str, **kwargs: Any) -> Dict[str, Any]:
        """
        Run a script by name with parameters.

        Looks for the script in the configured script paths and runs it.

        Args:
            script_name: Name of the script to run
            **kwargs: Parameters to pass to the script

        Returns:
            The response from Slicer

        Raises:
            NoScriptError: If the script cannot be found
        """
        script_file = self.find_script_file(script_name)
        return self.run_from_file(script_file, **kwargs)

    def run_from_file(
        self, file_path: Union[str, Path], **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Run a script from a file path with parameters.

        Args:
            file_path: Path to the script file
            **kwargs: Parameters to pass to the script

        Returns:
            The response from Slicer
        """
        script = script_from_template(file_path, **kwargs)
        return self.exec(script=script)

    def find_script_file(self, script_name: str) -> str:
        """
        Find a script file by name in the configured script paths.

        Args:
            script_name: Name of the script to find

        Returns:
            Full path to the script file

        Raises:
            NoScriptError: If the script cannot be found
        """
        script_file_name = f"{script_name}.py"

        # Check all configured script paths
        for script_path in settings.slicer_script_paths:
            for root, _, files in os.walk(script_path):
                if script_file_name in files:
                    return os.path.join(root, script_file_name)

        raise NoScriptError(f"Cannot find script '{script_name}' in script paths")

    def validate_result(
        self, validation_script: Optional[str] = None, **kwargs: Any
    ) -> bool:
        """
        Validate a segmentation result either with a custom script or the default validator.

        Args:
            validation_script: Optional name of the validation script
            **kwargs: Parameters for the validation

        Returns:
            True if validation succeeds, False otherwise

        Raises:
            SlicerSegmentationError: If validation fails catastrophically
        """
        logger.info(f"Validating segmentation with parameters: {kwargs}")

        try:
            if validation_script is None:
                # Use the default validation approach
                kwargs_formatted = ",".join(f"{k}={v!r}" for k, v in kwargs.items())
                validation_result = self.exec(
                    f"slicer_scene_manager.finish({kwargs_formatted})"
                )
                return True
            else:
                # Use the provided validation script
                validation_result = self.run_script(validation_script, **kwargs)
                logger.info(f"Validation result: {validation_result}")

                # Check if all validations passed
                if "validations" in validation_result:
                    return all(validation_result["validations"].values())
                return True

        except (SlicerError, ScriptArgumentError) as e:
            logger.error(f"Validation error: {e}")
            raise SlicerSegmentationError(f"Failed to validate segmentation: {e}")


def get_client_ip(request: Any) -> str:
    """
    Extract client IP address from a FastAPI request.

    Args:
        request: The FastAPI request object

    Returns:
        The client's IP address
    """
    return cast(str, request.client.host)


def get_webslicer(client_ip: str) -> SlicerWeb:
    """
    Get a SlicerWeb instance for a client IP address.

    Args:
        client_ip: The client's IP address

    Returns:
        A SlicerWeb instance configured for the client

    Raises:
        SlicerConnectionError: If unable to connect to Slicer
    """
    return SlicerWeb(f"http://{client_ip}:2016")
