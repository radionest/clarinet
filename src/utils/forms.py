"""
Form generation utilities for the Clarinet framework.

This module provides utilities for generating HTML forms based on task schemas
and questionnaires for user interaction.
"""

from typing import Dict, List, Optional, Literal, Any, Self, cast
import itertools as it
import os
import json
import html
import markdown
from pathlib import Path

from pydantic import BaseModel, Field
from fastapi import HTTPException, status, Request
from fastapi.templating import Jinja2Templates

from src.models import Task, TaskRead, TaskTypeCreate, User, DicomQueryLevel
from src.settings import settings
from src.utils.logger import logger


class FormGeneratorError(Exception):
    """Base exception for form generation errors."""

    pass


class ButtonGeneratorError(FormGeneratorError):
    """Exception for button generation errors."""

    pass


class ResultSchemaQuestion(BaseModel):
    """Model representing a question in a result schema."""

    description: Optional[str] = None
    const: Optional[str | bool | int] = None
    enum: Optional[List[str]] = []
    type: Optional[str] = None


class IfThenElse(BaseModel):
    """Model representing conditional logic in a schema."""

    properties: Dict[str, ResultSchemaQuestion] = {}


class ResultSchema(BaseModel):
    """Model representing a complete result schema."""

    schema_url: str = Field(alias="$schema")
    schema_id: str = Field(alias="$id")
    title: str
    description: str
    type: str
    properties: Dict[str, ResultSchemaQuestion]
    required: List[str] = []
    then: Optional[IfThenElse] = None
    if_else: Optional[IfThenElse] = Field(default=None, alias="else")


class TaskTypeSchema(BaseModel):
    """Model representing a task type schema."""

    name: str
    label: Optional[str] = None
    level: DicomQueryLevel
    role_name: str
    max_users: int
    min_users: int
    result_schema: ResultSchema
    slicer_script: Optional[str] = None
    slicer_script_args: Optional[dict] = None
    slicer_result_validator: Optional[str] = None
    slicer_result_validator_args: Optional[dict] = None

    def __repr__(self) -> str:
        return f"{self.name} \n ----- \n {self.result_schema.model_dump_json()}"


class QuestionaryNode:
    """Represents a node in a questionnaire."""

    def __init__(
        self,
        name: str,
        properties: ResultSchemaQuestion,
        isrequired: bool,
        task: TaskRead,
        task_type: TaskTypeSchema,
        request: Request,
        parent_node: Optional["QuestionaryNode"] = None,
    ):
        self.request = request
        self.name = name
        self.task = task
        self.properties = properties
        self.task_type = task_type
        self.isrequired = isrequired
        self.parent_node = parent_node
        self.children: List["QuestionaryNode"] = []

    def _format_boolean(self, value: bool | str) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return value

    def add_child(self, **kwargs: Any) -> None:
        child = QuestionaryNode(**kwargs)
        self.children.append(child)

    def _format_path_arg(self, arg: str) -> str:
        try:
            return arg.format(
                patient_id=self.task.study.patient.anon_id,
                patient_anon_name=html.escape(self.task.study.patient.anon_name or ""),
                study_uid=self.task.study_uid,
                series_uid=self.task.series_uid,
                user_id=self.task.user_id,
            )
        except AttributeError:
            return "attribute error"

    def _get_working_folder(self) -> str:
        match self.task_type.level:
            case DicomQueryLevel.series:
                path_list = (
                    settings.storage_path,
                    self.task.study.patient.anon_id,
                    self.task.study.anon_uid,
                    getattr(self.task.series, "anon_uid", None),
                )
            case DicomQueryLevel.study:
                path_list = (
                    settings.storage_path,
                    self.task.study.patient.anon_id,
                    self.task.study_uid,
                )
            case _:
                raise NotImplementedError(
                    "Can't cast working path for other task type levels."
                )

        try:
            return os.path.join(*path_list)
        except TypeError as e:
            logger.error(e)
            logger.info(f"{self.name}, {self.task_type.name}")
            raise FormGeneratorError(
                f"Not enough data to make working folder path. {path_list}"
            )

    def _get_script_args(self) -> str:
        if not self.task_type.slicer_script_args:
            return "{}"

        script_args_json = [
            f'"{k}": "{self._format_path_arg(v)}"'
            for k, v in self.task_type.slicer_script_args.items()
        ]
        script_args_json = ",".join(script_args_json)
        return f"{{ {script_args_json} }}"

    def _create_slicer_button(self) -> str:
        working_folder = self._get_working_folder()
        script_name = self.task_type.slicer_script
        script_args_json = self._get_script_args()

        return f"""<button 
                        class="slicer"
                        hx-post="{self.request.url_for("run_slicer")}"
                        hx-vals='{{"working_folder": "{html.escape(working_folder)}",
                                   "script_name": "{script_name}",
                                   "slicer_script_args":  {script_args_json} 
                        }}'
                        hx-ext="json-enc, response-targets"
                        hx-target="this"
                        hx-swap="none"
                        hx-target-error="#serious-errors"
                        hx-indicator="this" 
                        >Open in Slicer</button>
                   """

    def __repr__(self) -> str:
        html_head = f"<div>{self.properties.description}</div>"
        elements = ""
        match self.properties.type:
            case "boolean":
                elements = f"""
                <input name={self.name} type="radio" value=true>Yes<br>
                <input name={self.name} type="radio" value=false>No<br>
                            """
            case "string":
                if self.name == "series_uid" and self.task:
                    elements = "\n".join(
                        [
                            f"<input name='series_uid' type='radio' value={s.series_uid}>{s.series_number} {s.series_description}<br>"
                            for s in self.task.study.series
                        ]
                    )
                else:
                    elements = f"<input name={self.name} type='text'>"
            case "object":
                if self.name == "slicer":
                    elements = self._create_slicer_button()
            case _:
                if enum_variants := self.properties.enum:
                    elements = "\n".join(
                        [
                            f"<input name={self.name} type='radio' value={v}>{v}<br>"
                            for v in enum_variants
                        ]
                    )
                elif self.properties.const is not None:
                    elements = f"""<input name={self.name} type="checkbox" value={self._format_boolean(self.properties.const)}><br>"""
                else:
                    elements = f"<span>Can't build element for {self.task_type}</span>"

        return html_head + elements


class Questionary:
    """Generates a questionnaire from a task schema."""

    def __init__(self, task: TaskRead, request: Request):
        self.task = task
        self.request = request
        self.type = TaskTypeSchema(**task.task_type.model_dump())
        self.result_schema = ResultSchema(**task.task_type.result_schema)

        if self.result_schema is None:
            raise ValueError(
                f"Task with id {task.id} of type {task.task_type.name} should have result schema to make questionnaire! "
            )
        self.nodes: List[QuestionaryNode] = []
        self.create_nodes(self.result_schema.properties)
        if self.result_schema.then:
            self.create_nodes(self.result_schema.then.properties)

    def create_nodes(self, properties_node: Dict[str, ResultSchemaQuestion]) -> None:
        for node_name, node_props in properties_node.items():
            if any(node.name == node_name for node in self.nodes):
                continue
            new_node = QuestionaryNode(
                name=node_name,
                properties=node_props,
                task_type=self.type,
                isrequired=node_name in self.result_schema.required,
                task=self.task,
                request=self.request,
            )
            self.nodes.append(new_node)

    def __repr__(self) -> str:
        p = self.task.study.patient
        patient_name = p.anon_name if p.anon_name else p.name
        radiant_ico = f"""<a class="img_link_ico" href="{self.task.radiant}">RA</a>"""
        start = f"""
        <div class="question" tabindex="0">
            <div class="patient_name">{html.escape(patient_name or "")}</div>
            <div class="patient_id">{p.anon_id if p.anon_name else p.id}</div>
            <div class="study_details">{self.task.study.date}</div>
            <div class="task_name" tabindex="0">{self.result_schema.title}</div>
            <div class="task_description">{markdown.markdown(self.result_schema.description)}</div>
            <div class="task_info">{self.task.info or ""}</div>

            <a class="img_link" href="{self.task.radiant}">Open in Radiant</a>
            {radiant_ico if all([n.name != "slicer" for n in self.nodes]) else ""}
            
            <form
            action={self.request.url_for("submit_task", task_id=self.task.id)}
            method="post"
            hx-boost="true"
            hx-target="closest div.question"
            hx-swap="outerHTML"
            tabindex="0"
            >
        """
        end = f"""
        <input type="submit" 
               hx-indicator="this" 
               value="Submit">
        <input type="button"
        hx-post={self.request.url_for("pause_task", task_id=self.task.id)}
               hx-indicator="this" 
               value="Pause">
        </form>
        
        </div>
        """
        output_str = start + "\n".join(map(str, self.nodes)) + end
        return output_str.replace("\\n", "<br>")


def trans_booleans_in_form(form: dict[str, Any]) -> dict[str, Any]:
    """
    Transform string boolean values in a form dictionary to Python booleans.

    HTML forms can only submit strings, so boolean values come as 'true'/'false'.
    This function converts those strings to actual Python boolean values.

    Args:
        form: Dictionary of form values, typically from request.form()

    Returns:
        Dictionary with 'true'/'false' strings converted to Python booleans
    """
    output = {}
    for k, v in form.items():
        # Convert 'true'/'false' strings to Python booleans
        new_v = v if v not in ["true", "false"] else v == "true"
        output[k] = new_v
    return output
