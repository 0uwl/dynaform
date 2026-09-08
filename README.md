# DynaForm

A small Flask web app that turns a Jinja2 template into an HTML form, then renders the
template with whatever you type in.

Give it a `.j2` file (or paste the text), and DynaForm reads the undeclared variables out of
the template, builds a Bootstrap form with one input per variable, and renders the filled-in
result. The input type of each field comes from a prefix on the variable name, so the template
itself is the only thing you have to write.

From the result page you can copy the rendered output to your clipboard or save it as a `.txt`
file. Both run in the browser against the text already on the page, so the output never travels
back to the server.

Nothing is stored. There is no database, no session store, and no user content written to disk.
The template source is carried between the two requests in a hidden form field.

## Template syntax

Every undeclared variable in the template must be named `<PREFIX>_<name>`. A template containing
a variable that doesn't match is rejected with an error naming the offending variables.

| Prefix | Form field | Example        |
|--------|------------|----------------|
| `S_`   | text       | `S_username`   |
| `P_`   | password   | `P_password`   |
| `N_`   | number     | `N_user_age`   |
| `B_`   | checkbox   | `B_admin`      |
| `R_`   | radio      | `R_color_red`  |

Labels are derived from the name: the prefix is dropped, underscores become spaces, and only the
first word is capitalized (`N_user_age` → "User age"). Fields appear in the order the variables
first occur in the template source.

### Radio groups

Radio variables are named `R_<group>_<option>`. The group name is the single word right after
`R_` and may not contain underscores. All variables sharing a group render as one radio set, and
in the rendered template exactly one of them is true:

```jinja
Favourite colour:
{% if R_color_red %}red{% elif R_color_blue %}blue{% elif R_color_green %}green{% endif %}
```

That produces one "Color" group with Red / Blue / Green options.

### Conditional fields

A field is treated as a child of checkbox `B_<base>` when its own name (after the prefix) starts
with `<base>_`. Children are hidden until the checkbox is ticked, and they're optional rather
than required. If several checkboxes match, the longest base wins.

```jinja
Hello {{ S_username }}, age {{ N_user_age }}.
{% if B_admin %}
Admin: {{ S_admin_name }} / {{ P_admin_pass }}
{% endif %}
```

Ticking **Admin** unfolds the "Admin name" and "Admin pass" inputs.

## Configuration

All configuration is by environment variable.

| Variable             | Default    | Purpose                                                      |
|----------------------|------------|--------------------------------------------------------------|
| `SECRET_KEY`         | *(none)*   | Required. Signs CSRF tokens. The app refuses to start without it unless running in debug or test mode. |
| `MAX_CONTENT_LENGTH` | `262144`   | Max request body in bytes (256 KB).                          |
| `BIND_HOST`          | `0.0.0.0`  | Gunicorn bind address (container only).                      |
| `BIND_PORT`          | `8000`     | Gunicorn bind port (container only).                         |
| `GUNICORN_WORKERS`   | `2`        | Number of gunicorn workers (container only).                 |

Logs go to stdout only. Template accepted/rejected, validation failures, and render outcomes are
logged; submitted values never are.

## Running it locally

Python 3.12 or newer.

```bash
git clone git@github.com:0uwl/dynaform.git
cd dynaform
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Development server (`--debug` supplies a throwaway `SECRET_KEY`):

```bash
flask --app app run --debug
```

Then open <http://127.0.0.1:5000>.

To run it the way the container does, set a real key and use gunicorn:

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
gunicorn -w 2 -b 127.0.0.1:8000 "app:create_app()"
```

## Running it in a container

Build the image:

```bash
podman build -t dynaform:latest .
```

The image is `python:3.12-slim`, runs as a non-root user, and has Bootstrap 5 baked in, so nothing
is fetched from a CDN at runtime.

### With plain podman

```bash
podman run --rm -p 8000:8000 \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  --name dynaform \
  localhost/dynaform:latest
```

The app is then on <http://localhost:8000>. Add `-e GUNICORN_WORKERS=4` or `-e BIND_PORT=9000`
(with a matching `-p`) to change the defaults.

### With the Quadlet unit

`dynaform.container` is a [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
unit: systemd generates the service from it, so there's no `.service` file to write.

It reads `SECRET_KEY` from a Podman secret rather than a plain environment line, so create the
secret first:

```bash
python -c 'import secrets; print(secrets.token_hex(32))' \
  | podman secret create dynaform-secret-key -
```

Then install the unit. Rootless (recommended):

```bash
mkdir -p ~/.config/containers/systemd
cp dynaform.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start dynaform
```

For a system-wide service, copy to `/etc/containers/systemd/` instead and drop `--user` from the
`systemctl` commands (the secret then has to be created as root too).

Check on it:

```bash
systemctl --user status dynaform
journalctl --user -u dynaform -f
```

Container logs land in the journal automatically, which is why the app logs to stdout only.

systemd starts Quadlet units through the generated service, so there is nothing to
`systemctl enable`. The unit's `WantedBy=default.target` already handles start-on-login. For a
rootless service that should survive logout, enable lingering:

```bash
loginctl enable-linger "$USER"
```

The unit expects `localhost/dynaform:latest` to exist locally, so rebuild the image before
restarting the service after a code change:

```bash
podman build -t dynaform:latest .
systemctl --user restart dynaform
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite has no external dependencies and needs no running server:

- `tests/test_template_parser.py` — variable extraction, prefix validation, source ordering,
  radio grouping, conditional-parent detection.
- `tests/test_forms.py` — dynamic WTForms construction: field classes, labels, validators.
- `tests/test_routes.py` — the full HTTP path: parse → dynamic form → render.

`tests/conftest.py` sets a test `SECRET_KEY` and disables CSRF for the test client.

## Project layout

```
app/
  __init__.py           application factory, logging hookup
  config.py             environment-driven config
  routes.py             GET / , POST / (parse), POST /render
  forms.py              upload form + dynamic form builder
  template_parser.py    parsing, validation, grouping
  templates/            base, index, form, result
  static/js/            conditional-field toggle, output copy/download
  static/vendor/        vendored Bootstrap 5
tests/
Containerfile
dynaform.container      Quadlet unit
dynaform.md             original design note
plan.md                 implementation plan and rationale
```

## Contributing

The design note (`dynaform.md`) and the implementation plan (`plan.md`) explain why things are
the way they are. Read them before changing the parser or the syntax.

A few conventions the existing code follows:

- Both parsing and rendering of user-supplied templates go through Jinja2's
  `SandboxedEnvironment`. Keep it that way.
- Form field classes come from the fixed `FIELD_CLASSES` whitelist in `app/forms.py`, keyed by the
  validated prefix letter. Never build a field class from user input.
- The rendered output is escaped by Flask's own Jinja environment in `result.html`, which is what
  prevents reflected XSS. It is deliberately a different environment from the sandboxed one.
- Never log submitted field values; a `P_` field is a password by definition.
- No persistence: no database, no session storage of template content, no writing user content to
  disk.
- Modules carry type hints and `from __future__ import annotations`.

To contribute:

1. Fork and branch off `main`.
2. Add or update tests alongside the change. Parser changes belong in
   `tests/test_template_parser.py`, and anything touching a route needs a `tests/test_routes.py`
   case.
3. Run `pytest` and check the change in a browser end to end (a template using every prefix,
   including a radio group and a `B_`-conditional pair).
4. Open a pull request describing what changed and why.

## License

MIT. See [LICENSE](LICENSE).
