"""HTTP routes: choose/paste/upload -> parse & validate -> dynamic form -> render."""
from __future__ import annotations

from flask import Blueprint, Response, current_app, flash, render_template, request
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

from .forms import UploadForm, build_dynamic_form
from .template_library import LibraryLoader, read_template
from .template_parser import TemplateValidationError, parse_template

bp = Blueprint("dynaform", __name__)

# A directory template is small so the cache can be turned off to 
# avoid stale includes/extends etc.
_RENDER_ENV = SandboxedEnvironment(loader=LibraryLoader(), cache_size=0)


def _ordered_items(parsed):
    """Merge fields, radio groups and lists into one list in template source order."""
    items = [("field", f.source_pos, f) for f in parsed.fields]
    items += [("radio", g.source_pos, g) for g in parsed.radio_groups]
    items += [("list", lst.source_pos, lst) for lst in parsed.lists]
    items.sort(key=lambda item: item[1])
    return [{"kind": kind, "spec": spec} for kind, _pos, spec in items]


def _values_for(specs, submitted):
    """Turn submitted values into render context, keyed by variable name.

    Shared by top-level fields and each row of an L_ list, where the row's
    dict becomes ``loc`` in ``{% for loc in L_x %}``.
    """
    context = {}
    for spec in specs:
        value = submitted.get(spec.var_name)
        current_app.logger.debug(f"  Retrieved a value for variable '{spec.var_name}'")

        if spec.prefix != "B" and spec.has_default and value in (None, ""):
            # Leave it undefined so Jinja's own `default` filter supplies
            # the value, rather than substituting spec.default here. Both give
            # the same output for this field (the filter is in the template
            # and runs either way) but leaving it undefined is what bare
            # Jinja does, so a variable used a second time *without* the filter
            # renders empty here exactly as it would anywhere else.
            #
            # Checkboxes are excluded on purpose, an unchecked box submits
            # nothing, so omitting it would let default(true) tick it back on
            # and leave the user no way to turn it off. For B_ (and for radio
            # groups below) a default can only mean the state the form starts
            # in.
            current_app.logger.debug(f"  Leaving '{spec.var_name}' to its template default")
            continue

        if value is None:
            value = False if spec.prefix == "B" else ""
        context[spec.var_name] = value
    return context


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
        current_app.logger.info("Loading content from selected template")
        source = read_template(form.template_choice.data) or ""
    else:
        # Nothing uploaded, typed or picked
        source = ""

    if not source.strip():
        current_app.logger.info("Empty or incorrect content was sent")
        flash("Choose a template, paste template text, or upload a file.", "danger")
        return render_template("index.html", form=UploadForm()), 400

    try:
        parsed = parse_template(source, resolve=read_template)
    except TemplateValidationError as exc:
        current_app.logger.warning(f"Template rejected: {exc}")
        flash(str(exc), "danger")
        return render_template("index.html", form=UploadForm()), 400

    if not parsed.fields and not parsed.radio_groups and not parsed.lists:
        current_app.logger.warning("Template does not contain any valid variables")
        flash("Template has no DynaForm variables (S_/P_/N_/B_/R_/L_) to fill in.", "warning")
        return render_template("index.html", form=UploadForm()), 400

    current_app.logger.info(f"Template accepted: {len(parsed.fields)} field(s), {len(parsed.radio_groups)} radio group(s), {len(parsed.lists)} list(s)")

    dynamic_form = build_dynamic_form(parsed)(formdata=None, template_source=source)
    # One row to start from; a list may still be emptied and submitted with
    # none, so this is not a min_entries.
    for lst in parsed.lists:
        getattr(dynamic_form, lst.name).append_entry()
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
        parsed = parse_template(source, resolve=read_template)
    except TemplateValidationError as exc:
        current_app.logger.warning(f"Re-validation failed on render: {exc}")
        flash(str(exc), "danger")
        return render_template("index.html", form=UploadForm()), 400

    dynamic_form = build_dynamic_form(parsed)()
    if not dynamic_form.validate_on_submit():
        current_app.logger.warning("Submitted form failed validation")
        return render_template("form.html", form=dynamic_form, items=_ordered_items(parsed)), 400

    current_app.logger.info("Retrieving data from dynamic form")
    context = _values_for(parsed.fields, dynamic_form.data)

    for lst in parsed.lists:
        rows = getattr(dynamic_form, lst.name).data
        current_app.logger.debug(f"  Retrieved {len(rows)} row(s) for list '{lst.name}'")
        context[lst.name] = [_values_for(lst.fields, row) for row in rows]

    for group in parsed.radio_groups:
        selected = getattr(dynamic_form, "R_" + group.name).data
        current_app.logger.debug(f"  Read the selection from group '{group.name}'")
        for option, _label in group.options:
            context[f"R_{group.name}_{option}"] = option == selected

    try:
        output = _RENDER_ENV.from_string(source).render(**context)
    except SecurityError as exc:
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
