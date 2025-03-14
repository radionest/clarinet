"""
Rendering router for the Clarinet framework.

This module provides API endpoints for template rendering and web UI views.
"""

import os
from typing import Annotated, Callable, Dict, List, Optional, Any, Union, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates
from jinja_markdown import MarkdownExtension
from sqlmodel import Session

from src.exceptions import SlicerConnectionError
from src.models import Task, TaskRead, TaskStatus, TaskType, User, UserRead
from src.settings import settings
from src.utils.common import trans_booleans_in_form
from src.utils.database import get_session
from src.utils.form_generator import Questionary
from src.utils.logger import logger

from ..routers import auth, slicer, study, task, user
from ..security import decode_token_cookie, Token


class RenderRouter(APIRoute):
    """Custom router for template rendering that handles common exceptions."""

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                response: Response = await original_route_handler(request)
            except SlicerConnectionError:
                return HTMLResponse(
                    "Cannot connect to Slicer",
                    status_code=status.HTTP_404_NOT_FOUND,
                    headers={"HX-Reswap": "innerHTML settle:5s"},
                )
            except HTTPException as e:
                match e:
                    case err if err.status_code == status.HTTP_408_REQUEST_TIMEOUT:
                        return HTMLResponse(
                            "Cannot connect to Slicer",
                            status_code=status.HTTP_408_REQUEST_TIMEOUT,
                        )
                    case err if err.status_code == 401:
                        return render_login(request)
                    case err if err.status_code == status.HTTP_404_NOT_FOUND:
                        return HTMLResponse(
                            err.detail,
                            status_code=status.HTTP_404_NOT_FOUND,
                            headers={
                                "HX-Reswap": "innerHTML settle:5s",
                                "HX-Retarget": "#serious-errors",
                                "HX-Push-Url": "false",
                            },
                        )
                    case _:
                        return HTMLResponse(
                            e.detail,
                            status_code=status.HTTP_417_EXPECTATION_FAILED,
                            headers={"HX-Push-Url": "false"},
                        )

            return response

        return custom_route_handler


router: APIRouter = APIRouter(route_class=RenderRouter)


# Initialize templates with markdown support
templates: Jinja2Templates = Jinja2Templates(
    directory=settings.get_template_dir(), extensions=[MarkdownExtension]
)


@router.get("/")
def render_index(
    request: Request,
    user: UserRead = Depends(user.get_current_user_cookie),
    task_list: List[TaskRead] = Depends(task.get_my_tasks_pending),
    available_task_types: Dict[TaskType, int] = Depends(
        task.get_my_available_task_types
    ),
) -> templates.TemplateResponse:
    """Render the index page with task list and available task types."""
    task_questionaries: List[Questionary] = list(map(lambda t: Questionary(t, request), task_list))

    context: Dict[str, Any] = {
        "request": request,
        "username": user.id,
        "task_questionaries": task_questionaries,
        "available_task_types": available_task_types,
    }
    return templates.TemplateResponse("all_tasks.jinja", context=context)


@router.post("/get_task/{task_name}")
async def get_task_questionary(
    task_name: str,
    request: Request,
    user: User = Depends(user.get_current_user_cookie),
    session: Session = Depends(get_session),
) -> templates.TemplateResponse:
    """Get a questionary for a specific task type."""
    new_task: List[Task] = await task.find_task(
        random_one=True,
        task_status=TaskStatus.pending,
        task_name=task_name,
        find_queries=[],
        session=session,
        commons={"limit": 1, "skip": 0},
    )

    try:
        new_task_item: Task = new_task[0]
    except IndexError:
        raise HTTPException(
            detail="No more tasks of this type available!",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    updated_task: Task = task.append_user_to_task(user=user, task=new_task_item, session=session)
    question: Questionary = Questionary(updated_task, request=request)
    context: Dict[str, Any] = {"request": request, "username": user.id, "new_task": question}
    return templates.TemplateResponse("task.jinja", context=context)


@router.get("/login")
def render_login(request: Request) -> templates.TemplateResponse:
    """Render the login page."""
    context: Dict[str, Any] = {"request": request}
    return templates.TemplateResponse("login.jinja", context=context)


@router.get("/signup")
def render_signup(request: Request) -> templates.TemplateResponse:
    """Render the signup page."""
    context: Dict[str, Any] = {"request": request}
    return templates.TemplateResponse("signup.jinja", context=context)


@router.get("/logout")
def render_logout(request: Request) -> RedirectResponse:
    """Log out the user and redirect to the login page."""
    response: RedirectResponse = RedirectResponse(request.url_for("render_index"))
    response.delete_cookie(key="clarinet_auth_token")
    return response


@router.post("/authorize")
def render_authorize(request: Request, token: Token = Depends(auth.login_by_form)) -> RedirectResponse:
    """Authorize a user and set the authentication cookie."""
    response: RedirectResponse = RedirectResponse(
        request.url_for("render_index"), status_code=status.HTTP_302_FOUND
    )
    response.set_cookie(key="clarinet_auth_token", value=token.access_token)
    return response


@router.get("/navigation/{button_name}")
def add_navigation_button(button_name: str, request: Request) -> templates.TemplateResponse:
    """Render a navigation button template."""
    context: Dict[str, Any] = {"request": request}
    return templates.TemplateResponse(f"navigation/{button_name}.html", context=context)


@router.get("/task/{task_id}")
def show_task(request: Request, task: Task = Depends(task.get_task_details)) -> templates.TemplateResponse:
    """Show a specific task."""
    context: Dict[str, Any] = {"request": request, "task": task}
    return templates.TemplateResponse("task.jinja", context=context)


@router.post("/task/{task_id}/submit")
async def submit_task(
    request: Request,
    inwork_task: Task = Depends(task.get_task_details),
    session: Session = Depends(get_session),
    user_ip: str = Depends(slicer.get_client_ip),
    klara_url: str = Depends(task.get_clarinet_instance_url),
) -> Response:
    """Submit a completed task."""
    form: Dict[str, Any] = dict(await request.form())
    form = trans_booleans_in_form(form)
    logger.info(f"Task form: {form}")

    updated_task: Task = await task.add_task_result(
        result=form,
        task=inwork_task,
        user_ip=user_ip,
        session=session,
        clarinet_url=klara_url,
    )
    logger.info(f"Submitted: {updated_task}")

    return Response(
        status_code=status.HTTP_202_ACCEPTED, headers={"HX-Push-Url": "false"}
    )


@router.post("/task/{task_id}/pause")
async def pause_task(
    request: Request,
    inwork_task: Task = Depends(task.get_task_details),
    session: Session = Depends(get_session),
) -> Response:
    """Pause a task."""
    updated_task: Task = task.change_task_status(
        session=session, task_status=TaskStatus.pause, task=inwork_task
    )

    return Response(
        status_code=status.HTTP_202_ACCEPTED, headers={"HX-Push-Url": "false"}
    )


@router.post("/create_user")
def create_user(
    request: Request,
    username: str = Form(),
    password: str = Form(),
    password2: str = Form(),
    session: Session = Depends(get_session),
) -> Union[HTMLResponse, Response]:
    """Create a new user account."""
    if password != password2:
        return HTMLResponse(
            "Passwords do not match!", status_code=status.HTTP_406_NOT_ACCEPTABLE
        )

    new_user: User = User(id=username, password=password)

    try:
        new_user = user.add_user(user=new_user, session=session)
    except HTTPException as e:
        if e.status_code == status.HTTP_409_CONFLICT:
            return HTMLResponse(
                "User already exists!", status_code=status.HTTP_409_CONFLICT
            )

    # Assign a sample task to new users if available
    sample_series: Any = study.get_random_series(session=session)
    if sample_series:
        task.add_demo_task_to_user(user=new_user, series=sample_series, session=session)

    # Log the user in
    token: Token = auth.login_by_data(username, password, session=session)
    response: templates.TemplateResponse = render_index(
        request, user=UserRead.model_validate(new_user), task_list=[]
    )
    response.set_cookie(key="clarinet_auth_token", value=token.access_token)
    return response


@router.get("/feedback")
def render_feedback(
    request: Request,
    user: Optional[UserRead] = Depends(user.get_current_user_cookie),
) -> templates.TemplateResponse:
    """Render the feedback page."""
    context: Dict[str, Any] = {"request": request, "username": user.id if user else None}
    return templates.TemplateResponse("feedback.jinja", context=context)


@router.get("/faq")
def render_faq(
    request: Request, user: UserRead = Depends(user.get_current_user_cookie)
) -> templates.TemplateResponse:
    """Render the FAQ page."""
    context: Dict[str, Any] = {"request": request, "username": user.id}
    return templates.TemplateResponse("faq.jinja", context=context)


@router.post("/slicer")
async def run_slicer(
    request: Request,
    user: UserRead = Depends(user.get_current_user_cookie),
    slicer_response: Any = Depends(slicer.run_script),
) -> Response:
    """Run a script in 3D Slicer."""
    return Response(status_code=status.HTTP_200_OK, headers={"HX-Push-Url": "false"})