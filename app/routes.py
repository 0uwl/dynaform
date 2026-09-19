"""HTTP routes: choose/paste/upload -> parse & validate -> dynamic form -> render."""
from __future__ import annotations

from flask import Blueprint, Response, current_app, flash, render_template, request
from jinja2 import BaseLoader, TemplateNotFound
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

from .forms import UploadForm, build_dynamic_form
from .template_library import read_template
from .template_parser import TemplateValidationError, parse_template

bp = Blueprint("dynaform", __name__)


class _NoOtherTemplates(BaseLoader):
    """Refuse {% include %}, {% import %} and {% extends %}, in words.

    DynaForm renders one template on its own, so there is nothing for these to
    load -- and giving the environment a real loader is exactly how a template
    would get to read files off the host. Without a loader at all Jinja raises
    TypeError("no loader for this environment specified"), which is neither
    caught below nor meaningful to whoever wrote the template; TemplateNotFound
    is both.
    """

    def get_source(self, environment, template):
        raise TemplateNotFound(
            template,
            message=(
                f"this template refers to another template ({template}), and "
                "DynaForm renders one template on its own -- there are no "
                "others to include, import or extend"
            ),
        )


_RENDER_ENV = SandboxedEnvironment(loader=_NoOtherTemplates())


def _ordered_items(parsed):
    """Merge fields and radio groups into one list in template source order."""
    items = [("field", f.source_pos, f) for f in parsed.fields]
    items += [("radio", g.source_pos, g) for g in parsed.radio_groups]
    items.sort(key=lambda item: item[1])
    return [{"kind": kind, "spec": spec} for kind, _pos, spec in items]


@bp.route("/", methods=["GET"])
def index():
    return render_template("index.html", form=UploadForm())


@bp.route("/template-library", methods=["GET"])
def library_template():
    """Serve one directory template as plain text for the picker's JS.

    Read-only and idempotent, so it carries no CSRF token; it exposes exactly
    what the <select> on the first page already lists.
    """
    source = read_template(request.args.get("name", ""))
    if source is None:
        return Response("Template not found.", status=404, mimetype="text/plain")
    return Response(
        source,
        mimetype="text/plain",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@bp.route("/", methods=["POST"])
def parse():
    form = UploadForm()
    if not form.validate_on_submit():
        current_app.logger.warning("Form was not valid")
        return render_template("index.html", form=form), 400

    if form.load.data:
        current_app.logger.debug("Loading content into text area manually")
        return _load_into_editor(form)

    uploaded = form.template_file.data
    if uploaded and uploaded.filename:
        current_app.logger.info("Loading content from file")
        source = uploaded.read().decode("utf-8", errors="replace")
    elif form.template_text.data and form.template_text.data.strip():
        current_app.logger.info("Loading content from text area")
        source = form.template_text.data
    elif form.template_choice.data:
        # template_choice holds the template's *name*; the contents have to be
        # read off disk. This is how "choose, then parse" works with scripting
        # unavailable.
        current_app.logger.info("Loading content from selected template")
        source = read_template(form.template_choice.data) or ""
    else:
        # Nothing uploaded, typed or picked. Without this branch `source` is
        # never bound and the check below raises UnboundLocalError -- a 500
        # where the user should get the flash message.
        source = ""

    if not source.strip():
        current_app.logger.info("Empty or incorrect content was sent")
        flash("Choose a template, paste template text, or upload a file.", "danger")
        return render_template("index.html", form=UploadForm()), 400

    try:
        parsed = parse_template(source)
    except TemplateValidationError as exc:
        current_app.logger.warning(f"Template rejected: {exc}")
        flash(str(exc), "danger")
        return render_template("index.html", form=UploadForm()), 400

    if not parsed.fields and not parsed.radio_groups:
        current_app.logger.warning("Template does not contain any valid variables")
        flash("Template has no DynaForm variables (S_/P_/N_/B_/R_) to fill in.", "warning")
        return render_template("index.html", form=UploadForm()), 400

    current_app.logger.info(f"Template accepted: {len(parsed.fields)} field(s), {len(parsed.radio_groups)} radio group(s)")

    dynamic_form = build_dynamic_form(parsed)(formdata=None, template_source=source)
    return render_template("form.html", form=dynamic_form, items=_ordered_items(parsed))


def _load_into_editor(form: UploadForm):
    """Copy the chosen directory template into the textarea and redisplay.

    The no-JS path behind the "Load into editor" button
    """
    name = form.template_choice.data or ""
    if not name:
        current_app.logger.warning("No template was received")
        flash("Choose a template to load first.", "warning")
        return render_template("index.html", form=form), 400

    source = read_template(name)
    if source is None:
        current_app.logger.warning(f"Directory template not found: {name}")
        flash("That template is no longer available.", "danger")
        # Redisplay the submitted form rather than a fresh one so anything
        # already typed into the editor survives the failed load.
        return render_template("index.html", form=form), 404

    current_app.logger.info(f"Directory template loaded into editor: {name}")
    form.template_text.data = source
    return render_template("index.html", form=form)


@bp.route("/render", methods=["POST"])
def render():
    source = request.form.get("template_source", "")
    if not source:
        current_app.logger.warning("No source in the request, session has expired")
        flash("Session expired, please submit the template again.", "danger")
        return render_template("index.html", form=UploadForm()), 400

    try:
        parsed = parse_template(source)
    except TemplateValidationError as exc:
        current_app.logger.warning(f"Re-validation failed on render: {exc}")
        flash(str(exc), "danger")
        return render_template("index.html", form=UploadForm()), 400

    dynamic_form = build_dynamic_form(parsed)()
    if not dynamic_form.validate_on_submit():
        current_app.logger.warning("Submitted form failed validation")
        return render_template("form.html", form=dynamic_form, items=_ordered_items(parsed)), 400

    current_app.logger.info("Retrieving data from dynamic form")
    context = {}
    for spec in parsed.fields:
        value = getattr(dynamic_form, spec.var_name).data
        # Names only. Which fields were filled in is enough to follow the
        # mapping; the values belong to whoever typed them, and S_api_token is
        # no less sensitive than P_password -- the prefix does not say which.
        current_app.logger.debug(f"  Retrieved a value for variable '{spec.var_name}'")

        if spec.prefix != "B" and spec.has_default and value in (None, ""):
            # Leave it undefined so Jinja's own `default` filter supplies
            # the value, rather than substituting spec.default here. Both give
            # the same output for this field -- the filter is in the template
            # and runs either way -- but leaving it undefined is what bare
            # Jinja does, so a variable used a second time *without* the filter
            # renders empty here exactly as it would anywhere else.
            #
            # Checkboxes are excluded on purpose: an unchecked box submits
            # nothing, so omitting it would let default(true) tick it back on
            # and leave the user no way to turn it off. For B_ (and for radio
            # groups below) a default can only mean the state the form starts
            # in.
            current_app.logger.debug(f"  Leaving '{spec.var_name}' to its template default")
            continue

        if value is None:
            value = False if spec.prefix == "B" else ""
        context[spec.var_name] = value

    for group in parsed.radio_groups:
        selected = getattr(dynamic_form, "R_" + group.name).data
        current_app.logger.debug(f"  Read the selection from group '{group.name}'")
        for option, _label in group.options:
            context[f"R_{group.name}_{option}"] = option == selected

    try:
        output = _RENDER_ENV.from_string(source).render(**context)
    except SecurityError as exc:
        # The sandbox refusing a template is the one failure here that is not
        # a mistake: it is someone reaching outside it, so it stays loud.
        current_app.logger.error(f"Sandbox blocked the template: {exc}")
        return _render_failed(exc, dynamic_form, parsed)
    except Exception as exc:  # noqa: BLE001 - the blind catch is the point
        # Rendering runs code the visitor wrote, and a template can raise
        # whatever it likes: {{ 1/0 }} is a ZeroDivisionError, arithmetic on a
        # field left blank is a TypeError, and any filter can raise its own.
        # Enumerating them is a losing game, and each one missed is a 500
        # blaming the service for what the template did -- so they all belong
        # on the form with the rest of what is wrong with the submission. The
        # try wraps a single call, so this cannot swallow a bug in DynaForm's
        # own handling around it.
        current_app.logger.warning(f"Render failed: {type(exc).__name__}: {exc}")
        return _render_failed(exc, dynamic_form, parsed)

    current_app.logger.info(f"Template rendered successfully ({len(output)} bytes output)")
    return render_template("result.html", output=output)


def _render_failed(exc: Exception, dynamic_form, parsed):
    """Put a rendering failure back on the form the values came from."""
    flash(f"Rendering failed: {exc}", "danger")
    return render_template("form.html", form=dynamic_form, items=_ordered_items(parsed)), 400
