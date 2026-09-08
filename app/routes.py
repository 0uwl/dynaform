"""HTTP routes: upload/paste -> parse & validate -> dynamic form -> render."""
from __future__ import annotations

from flask import Blueprint, current_app, flash, render_template, request
from jinja2.exceptions import SecurityError, UndefinedError, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from .forms import UploadForm, build_dynamic_form
from .template_parser import TemplateValidationError, parse_template

bp = Blueprint("dynaform", __name__)

_RENDER_ENV = SandboxedEnvironment()


def _ordered_items(parsed):
    """Merge fields and radio groups into one list in template source order."""
    items = [("field", f.source_pos, f) for f in parsed.fields]
    items += [("radio", g.source_pos, g) for g in parsed.radio_groups]
    items.sort(key=lambda item: item[1])
    return [{"kind": kind, "spec": spec} for kind, _pos, spec in items]


@bp.route("/", methods=["GET"])
def index():
    return render_template("index.html", form=UploadForm())


@bp.route("/", methods=["POST"])
def parse():
    form = UploadForm()
    if not form.validate_on_submit():
        return render_template("index.html", form=form), 400

    uploaded = form.template_file.data
    if uploaded and uploaded.filename:
        source = uploaded.read().decode("utf-8", errors="replace")
    else:
        source = form.template_text.data or ""

    if not source.strip():
        flash("Provide a template file or paste template text.", "danger")
        return render_template("index.html", form=UploadForm()), 400

    try:
        parsed = parse_template(source)
    except TemplateValidationError as exc:
        current_app.logger.info("Template rejected: %s", exc)
        flash(str(exc), "danger")
        return render_template("index.html", form=UploadForm()), 400

    if not parsed.fields and not parsed.radio_groups:
        flash("Template has no DynaForm variables (S_/P_/N_/B_/R_) to fill in.", "warning")
        return render_template("index.html", form=UploadForm()), 400

    current_app.logger.info(
        "Template accepted: %d field(s), %d radio group(s)",
        len(parsed.fields),
        len(parsed.radio_groups),
    )

    dynamic_form = build_dynamic_form(parsed)(formdata=None, template_source=source)
    return render_template("form.html", form=dynamic_form, items=_ordered_items(parsed))


@bp.route("/render", methods=["POST"])
def render():
    source = request.form.get("template_source", "")
    if not source:
        flash("Session expired -- please submit the template again.", "danger")
        return render_template("index.html", form=UploadForm()), 400

    try:
        parsed = parse_template(source)
    except TemplateValidationError as exc:
        current_app.logger.warning("Re-validation failed on render: %s", exc)
        flash(str(exc), "danger")
        return render_template("index.html", form=UploadForm()), 400

    dynamic_form = build_dynamic_form(parsed)()
    if not dynamic_form.validate_on_submit():
        current_app.logger.info("Submitted form failed validation")
        return render_template("form.html", form=dynamic_form, items=_ordered_items(parsed)), 400

    context = {}
    for spec in parsed.fields:
        value = getattr(dynamic_form, spec.var_name).data
        if value is None:
            value = False if spec.prefix == "B" else ""
        context[spec.var_name] = value

    for group in parsed.radio_groups:
        selected = getattr(dynamic_form, "R_" + group.name).data
        for option, _label in group.options:
            context[f"R_{group.name}_{option}"] = option == selected

    try:
        output = _RENDER_ENV.from_string(source).render(**context)
    except (SecurityError, UndefinedError, TemplateError) as exc:
        current_app.logger.error("Render failed: %s", exc)
        flash(f"Rendering failed: {exc}", "danger")
        return render_template("form.html", form=dynamic_form, items=_ordered_items(parsed)), 400

    current_app.logger.info("Template rendered successfully (%d bytes output)", len(output))
    return render_template("result.html", output=output)
