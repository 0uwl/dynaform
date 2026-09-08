# DynaForm — Implementation Plan

## Jinja2 research summary (source-verified, v3.1.2 installed locally)

**Variable extraction** — `jinja2.meta.find_undeclared_variables(ast)`:
- Parse untrusted source with `env.parse(source)` → AST. This does **not** execute
  anything, just compiles to an AST, so it's safe to call on arbitrary uploaded text.
  Raises `jinja2.TemplateSyntaxError` on bad syntax — must catch this.
- `find_undeclared_variables` walks the AST via the code generator and returns the
  **set** of names that would be looked up from the render context — i.e. exactly
  the variables the caller needs to supply. It correctly excludes names bound by
  `{% set %}`, loop variables, macro args, etc. — it does NOT just regex `{{ }}`.
- Caveat: it returns a `set`, so **source order is lost**. We need order for a
  sane form layout → re-sort the extracted names by their first regex match
  (`\bname\b`) offset in the raw source text.

**Safe rendering** — `jinja2.sandbox.SandboxedEnvironment`:
- Drop-in subclass of `Environment` that blocks attribute/method access to
  unsafe internals (`__class__`, `mro`, generator frames, mutation of "known
  mutable" builtins, etc.) and raises `SecurityError` when a template tries an
  unsafe operation.
- Since DynaForm renders templates supplied by the same person filling the
  form, RCE-via-SSTI isn't a cross-user threat here, but the sandbox is free,
  well-tested, and closes the door if the app is ever exposed to multiple
  users or if template content is shared — use it for both `parse()` and
  the final `render()`, not just Environment.
- We will **not** enable `autoescape` on this environment: DynaForm outputs
  arbitrary text (configs, code, etc.), not HTML, so escaping would corrupt
  output. XSS risk is handled separately (see Security).

No other Jinja2 feature (extensions, loaders, i18n) is needed — this is a
single `parse()` + `find_undeclared_variables()` + `render()` pipeline.

## Variable syntax (from the note, extended for the two "desired features")

Prefix → form field, split on `_`:

| Prefix | Field         |
|--------|---------------|
| `S_`   | text          |
| `P_`   | password      |
| `N_`   | number        |
| `B_`   | checkbox      |
| `R_`   | radio (new, see below) |

Any undeclared variable that doesn't match `^(S|P|N|B|R)_[A-Za-z0-9]+$` →
reject the whole template with a clear error naming the offending variable(s).

Label rule: strip the prefix, split remaining `_`-joined words, capitalize
only the first word, join with spaces (`N_user_age` → "User age").

**Radio groups** (spec says "somehow group them" — proposing a convention
consistent with the existing scheme rather than a new syntax mini-language):
`R_<group>_<option>`, group name is the single word right after `R_`, option
is everything after that. All `R_` variables sharing the same `<group>`
render as one `<input type=radio name=group>` set; `<option>` becomes the
label (same capitalization rule) and the submitted value.
Example: `R_color_red`, `R_color_blue` → one "Color" radio group, options
Red/Blue. Only one of the group's variables is truthy in the render context
(the selected one, or model it as a single string variable — see below).

Open design point: a Jinja template referencing `R_color_red` as a
standalone boolean is awkward for "pick one of N". Better mapping: expose
**one** context variable per group, named after the group
(`color = "red"`), so the template author writes `{{ color }}` /
`{% if color == "red" %}`. This means the "variable" the parser must accept
is just `R_<group>` used inside the template (e.g. `{{ R_color }}`), and the
*options* are declared as a Jinja comment or simply inferred from... there's
no way to know the option list without them appearing in the template.
→ **Decision:** keep the flat `R_<group>_<option>` scheme (each option is
its own declared variable in the template, e.g.
`{% if R_color_red %}...{% endif %}`) since that needs no new syntax beyond
prefixes, matches how `B_` already works, and only the UI groups them into
radio buttons. Confirm with user before building if they want the
single-string-variable style instead.

**Conditional fields** (checkbox unfolds children): a field is a child of
checkbox `B_<base>` if its own name (after prefix) starts with `<base>_`.
`B_admin` → children are any `S_admin_*`, `P_admin_*`, `N_admin_*`,
`R_admin_*`. Children are wrapped in a `<div data-parent="admin" hidden>`;
a small vanilla-JS listener on the checkbox toggles the `hidden` attribute.
No JS framework needed — Bootstrap 5 ships without jQuery already.

## Request flow (stateless — "stores no information")

1. `GET /` — `index.html`: file upload (`.j2`/`.txt`) OR paste-into-textarea,
   one submit button.
2. `POST /` — parse & validate:
   - Read uploaded file or textarea content (whichever is present; reject if
     both empty).
   - `SandboxedEnvironment().parse(source)` → catch `TemplateSyntaxError` →
     re-render `index.html` with a flashed error.
   - `find_undeclared_variables(ast)`, order by first-occurrence, validate
     prefixes → on any bad name, flash error listing them, re-render
     `index.html`.
   - Build field specs (type, label, group, parent) from the validated names.
   - Dynamically build a `FlaskForm` subclass (see below) and render
     `form.html`, **plus a hidden field carrying the raw template source**
     so the next POST doesn't need server-side state (no session/DB).
3. `POST /render` — rebuild the *same* field specs from the hidden template
   text (deterministic — same parse+validate as step 2), bind submitted
   data into the dynamic form, validate, then
   `SandboxedEnvironment().from_string(source).render(**values)` and show
   `result.html` with the output in a `<pre>`.

Rebuilding the form from the hidden template text on both legs means there's
exactly one code path for "template text → field specs", not two to keep in
sync.

## Dynamic WTForms construction

```python
FIELD_MAP = {
    "S": (StringField, {}),
    "P": (PasswordField, {}),
    "N": (IntegerField, {}),   # or FloatField — decide: plain N_ = int, fine for MVP
    "B": (BooleanField, {}),
    "R": (RadioField-per-group...),  # handled separately, grouped
}

def build_form_class(field_specs: list[FieldSpec]) -> type[FlaskForm]:
    attrs = {}
    for spec in field_specs:
        field_cls, kwargs = FIELD_MAP[spec.prefix]
        attrs[spec.attr_name] = field_cls(spec.label, **kwargs)
    attrs["template_source"] = HiddenField()
    return type("DynamicForm", (FlaskForm,), attrs)
```

Building form classes with `type()` from a **fixed whitelist** of WTForms
field classes (never `eval`/`getattr` on attacker input) is safe — the
attacker only controls field *names* and *labels* (strings), never code.

`R_` groups: collect all options per group, emit one `RadioField(group_label,
choices=[(option, label), ...])` per group instead of one field per variable.

## File layout

```
dynaform/
├── dynaform.md
├── plan.md
├── requirements.txt
├── Containerfile
├── dynaform.container       # Quadlet unit
└── app/
    ├── __init__.py           # create_app() application factory
    ├── config.py             # SECRET_KEY from env, MAX_CONTENT_LENGTH
    ├── template_parser.py    # parse/validate/extract, FieldSpec, build_form_class
    ├── forms.py              # static UploadForm (index page)
    ├── routes.py             # blueprint: index, parse, render
    ├── static/
    │   └── vendor/bootstrap-5.3.x/{css,js}/...   # vendored, no CDN
    │   └── js/conditional.js                     # checkbox show/hide
    └── templates/
        ├── base.html
        ├── index.html
        ├── form.html
        └── result.html
```

## Security checklist

- CSRF: `FlaskForm` gives every form (including the dynamically-built one) a
  `csrf_token` automatically once `SECRET_KEY` is set — no extra code.
- `SandboxedEnvironment` for both `parse()` and `render()` of user-supplied
  templates (defense in depth against SSTI).
- Field construction uses a fixed whitelist dict, never dynamic
  `eval`/`import`/`getattr(module, name)` on attacker-controlled strings.
- Upload limits: `MAX_CONTENT_LENGTH` in Flask config (e.g. 256KB — these are
  form templates, not large files) to block oversized-body DoS; restrict
  accepted upload extension to `.j2`/`.txt` (advisory only, content is what's
  actually parsed).
- Output display: the rendered result string is inserted into `result.html`
  via normal `{{ result }}` in Flask's own (non-sandboxed, autoescape-on)
  Jinja environment for the app's UI — so whatever text the user's template
  produced is HTML-escaped before it reaches the browser. This is a
  **separate** Jinja environment/instance from the sandboxed one used to
  render the user's template, and is what actually prevents reflected XSS.
- `SECRET_KEY` required from environment at container start; fail fast (raise
  at `create_app()`) if unset in production config.
- Password fields: `autocomplete="off"`, never logged, never persisted
  (matches "stores no information").
- No database, no session store, no filesystem writes of user content.

## Logging

- Log important events to **stdout** only (no log files — container-native):
  template accepted/rejected (with reason, never field values), syntax
  errors, render success/failure, `SecurityError` from the sandbox. Never
  log submitted form values (a `P_` field is, by definition, a password).
- Hook the app logger into gunicorn's own logger instead of configuring a
  separate handler, so under gunicorn everything shares one formatter/stream
  and log lines aren't duplicated or split across two pipelines:

```python
import logging

def create_app():
    app = Flask(__name__)
    gunicorn_logger = logging.getLogger("gunicorn.error")
    if gunicorn_logger.handlers:          # running under gunicorn
        app.logger.handlers = gunicorn_logger.handlers
        app.logger.setLevel(gunicorn_logger.level)
    ...
```

  Gunicorn itself must be told to send its own logs to stdout:
  `--access-logfile - --error-logfile -` added to the `CMD` (`-` means
  stdout). Falling back to Flask's default logger (also stdout, via
  `app.run()`) when `gunicorn_logger.handlers` is empty keeps `flask run`
  usable for local dev without gunicorn.

## Container

- `python:3.12-slim` base, non-root user, single stage (no build step needed
  — pure Python + vendored static assets committed to the repo).
- `pip install --no-cache-dir -r requirements.txt`.
- Bootstrap 5 vendored once under `app/static/vendor/` at dev time (download
  the official dist zip, commit the CSS/JS) — no CDN fetch at container
  runtime, matches "locally baked in".
- `CMD gunicorn -w ${GUNICORN_WORKERS:-2} -b ${BIND_HOST:-0.0.0.0}:${BIND_PORT:-8000} --access-logfile - --error-logfile - "app:create_app()"`
  — gunicorn calls the factory itself (no entrypoint file); shell-form `CMD`
  lets the container runtime substitute `$BIND_HOST`/`$BIND_PORT`/
  `$GUNICORN_WORKERS` from `-e`/`--env` at `podman run` time, falling back to
  sane defaults if unset. No `gunicorn.conf.py` needed for just this.
  `--access-logfile - --error-logfile -` sends gunicorn's own logs to
  stdout, which the app logger then piggybacks on (see Logging).
- `requirements.txt`: `Flask`, `Flask-WTF`, `WTForms`, `Jinja2` (Flask dep,
  pin anyway), `gunicorn`. Nothing else — no ORM, no session backend needed.

## Quadlet unit (run as a systemd service via Podman)

- `dynaform.container` at the repo root (Quadlet reads `.container` files
  and generates the systemd unit — no hand-written `.service` file needed).
  Deployed to `~/.config/containers/systemd/` (rootless) or
  `/etc/containers/systemd/` (system), then `systemctl daemon-reload`.

```ini
[Unit]
Description=DynaForm
After=network-online.target

[Container]
Image=localhost/dynaform:latest
PublishPort=%h:8000:8000
Environment=BIND_HOST=0.0.0.0
Environment=BIND_PORT=8000
Environment=GUNICORN_WORKERS=2
Environment=SECRET_KEY=%t/dynaform.secret
Secret=dynaform-secret-key,type=env,target=SECRET_KEY

[Service]
Restart=on-failure

[Install]
WantedBy=default.target
```

  Notes:
  - `SECRET_KEY` should come from a Podman secret
    (`podman secret create dynaform-secret-key -`), not a plain `Environment=`
    line committed to the repo — the example above shows the `Secret=` form;
    drop the redundant `Environment=SECRET_KEY=...` line once the secret is
    set up.
  - Container logs land in the **journal** automatically under Podman/Quadlet
    (`journalctl --user -u dynaform`), which is why stdout-only logging
    (above) is the right target — no extra log-shipping config needed.
  - `PublishPort`/env values here should track the `BIND_HOST`/`BIND_PORT`
    defaults from the Containerfile `CMD`.

## Build order

1. `app/template_parser.py` — parsing, prefix validation, `FieldSpec`,
   ordering-by-source-offset, radio grouping, conditional-parent detection.
   Pure functions, easiest to unit-test in isolation (no Flask needed).
2. `app/forms.py` + dynamic form builder — depends on (1).
3. `app/routes.py` + `create_app()` — wires parser + forms + templates.
4. Templates (`base.html` with vendored Bootstrap, `index.html`,
   `form.html` with conditional-field `data-parent` markup, `result.html`).
5. `static/js/conditional.js` — checkbox toggle logic.
6. `Containerfile` + `requirements.txt`, gunicorn/app logging hookup.
7. `dynaform.container` Quadlet unit.
8. Smoke test: a `.j2` sample using every prefix (`S_`, `P_`, `N_`, `B_`,
   `R_` group, and a `B_`-conditional pair) end to end in a browser; verify
   logs appear via `journalctl --user -u dynaform` when run through Quadlet.

## Open questions for the user before/while building

1. Radio groups: confirm the flat `R_<group>_<option>` scheme above (each
   option is its own template variable) vs. a single `R_<group>` string
   variable with option list declared some other way.
2. `N_` fields: integer-only, or should some support decimals (`FloatField`)?
   Proposing integer-only for MVP since the note doesn't distinguish.
3. Multiple checkboxes unfolding the *same* child field name — not handled;
   assuming child field names are unique per checkbox base name.
