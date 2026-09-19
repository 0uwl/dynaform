"""WTForms integration: the static upload form and the dynamic per-template form."""
from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    HiddenField,
    IntegerField,
    PasswordField,
    RadioField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import InputRequired, Optional

from .template_library import list_templates
from .template_parser import ParsedTemplate

FIELD_CLASSES = {
    "S": StringField,
    "P": PasswordField,
    "N": IntegerField,
    "B": BooleanField,
}


class UploadForm(FlaskForm):
    template_choice = SelectField("Choose a template", validators=[Optional()])
    template_text = TextAreaField("Template text", validators=[Optional()])
    template_file = FileField(
        "Upload a file",
        validators=[Optional(), FileAllowed(["j2", "txt"], "Template files only (.j2, .txt).")],
    )
    load = SubmitField("Load into editor")
    submit = SubmitField("Parse template")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Choices are rebuilt per instance so a template added to the mounted
        # directory appears on the next page load, and so WTForms' own choice
        # validation rejects any value that isn't currently on disk.
        self.library_templates = list_templates()
        self.template_choice.choices = [("", "-- none --")] + [
            (template.name, template.label) for template in self.library_templates
        ]


def build_dynamic_form(parsed: ParsedTemplate) -> type[FlaskForm]:
    """Build a FlaskForm subclass with one field per template variable.

    Field classes come from a fixed whitelist (FIELD_CLASSES) keyed by the
    validated single-letter prefix -- never from attacker-controlled input.
    """
    attrs: dict[str, object] = {
        "template_source": HiddenField(validators=[InputRequired()]),
        "submit": SubmitField("Render template"),
    }

    for spec in parsed.fields:
        field_cls = FIELD_CLASSES[spec.prefix]
        if spec.prefix == "B":
            validators = []
        elif spec.parent is not None:
            # ponytail: conditional children are Optional rather than
            # cross-validated against their parent checkbox's state -- add
            # server-side "required if parent checked" if that's ever needed.
            validators = [Optional()]
        elif spec.has_default:
            # A default makes the field optional by implication: submitting it
            # blank is how you ask for the default, so it cannot also be
            # required. Nothing in the template says so, which is why the
            # README spells it out.
            validators = [Optional()]
        else:
            validators = [InputRequired()]
        # default=None is what WTForms already assumes, so an undefaulted
        # field is built exactly as before. A PasswordField never renders its
        # value, so a P_ default prefills without reaching the markup.
        attrs[spec.var_name] = field_cls(spec.label, validators=validators, default=spec.default)

    for group in parsed.radio_groups:
        validators = [Optional()] if group.parent is not None else [InputRequired()]
        # A preselected option always submits, so the group can keep
        # InputRequired: the default cannot make it fail.
        attrs["R_" + group.name] = RadioField(
            group.label, choices=group.options, validators=validators, default=group.default
        )

    return type("DynamicForm", (FlaskForm,), attrs)
