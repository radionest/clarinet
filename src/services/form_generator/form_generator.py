from src.models.task import Task, TaskSchema, TaskResultSchema
import jinja2


def render_template(template_name, **kwargs):
    ...   


def field_to_html(cls, field_name: str, field_info: FieldInfo) -> str:
    output_template = """
        <div class="field_description">{description}</div>
        {element}
        """

    match field_info.annotation:
        case bool():
            element = f"""
                    <input name={field_name} type="radio" value=true>Да<br>
                    <input name={field_name} type="radio" value=false>Нет<br>
                    """
        case TaskDescription():
            elementrender_subform
        case _:
            raise NotImplementedError(str(field_info))

    return output_template.format(description=field_info.de, element=element)


def task_to_html_form(task: Task) -> str:
    ...

def render_subform(TaskResultSchema) -> str:
    ...