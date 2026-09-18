# DynaForm

A small Flask web app that turns a Jinja2 template into an HTML form, then renders the
template with whatever you type in.

Give it a `.j2` file (or paste the text, or pick one from a directory you mounted), and
DynaForm reads the undeclared variables out of the template, builds a Bootstrap form with one
input per variable, and renders the filled-in result. The input type of each field comes from a
prefix on the variable name, so the template itself is the only thing you have to write.

A file you pick is read in the browser and shown in the editor, so you can look it over and
change it before the form is built -- with scripting enabled the file itself never leaves your
machine.

From the result page you can copy the rendered output to your clipboard or save it as a `.txt`
file. Both options run in the browser against the text already on the page, so the output never travels
back to the server.

Nothing is stored. There is no database, no session store, and no user content written to disk.
The template source is carried between the two requests in a hidden form field. The template
directory is only ever read from.

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
first word is capitalized (`N_user_age` -> "User age"). Fields appear in the order the variables
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

## Template directory

Point `TEMPLATE_DIR` at a directory of ready-made templates and DynaForm lists them in a picker
at the top of the first page. The container image sets `TEMPLATE_DIR=/templates` already, so all
you have to do is bind-mount a host directory there:

```bash
podman run --rm -p 8000:8000 \
  -v ~/dynaform-templates:/templates:ro,Z \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  localhost/dynaform:latest
```

The first page then offers two sources side by side -- **Choose a template**, the directory
listing, and **Upload a file** -- above the **Template text** editor that both of them fill.
Whatever is in that editor when you press **Parse template** is what gets parsed.

Choosing a template copies its text into the editor so you can read it and change it before
going on to the form. Those edits live in that browser page and in the request that follows it:
**the file on disk is never written to**, and the next visitor gets the original again. Mounting
the directory read-only (`:ro` above) makes that a property of the container, not just of the
code.

What gets listed:

- Files ending in `.j2` or `.txt`, including ones in subdirectories — a subdirectory shows up as
  part of the name (`linux/sshd.j2`).
- Not files or directories whose name starts with `.`, so a stray `.git` directory stays out of
  the list.
- Not files bigger than `TEMPLATE_MAX_BYTES` (64 KB by default), because a chosen template
  travels back to the server in the editor on the next request.
- A symlink is followed only if it lands inside the directory; one pointing at `/etc/passwd` is
  ignored, and a symlinked subdirectory is not descended into.

The directory is scanned per request, so a template you drop into it shows up on the next page
load without restarting anything. Leaving `TEMPLATE_DIR` unset (the default outside the
container) turns the whole picker off, and so does an empty directory.

Without JavaScript, pick a template and press **Load into editor**; the page comes back with the
text in place. With JavaScript, the button is hidden and the text loads as soon as you pick.
Either way, if you press **Parse template** with an empty editor while a template is selected,
that template is what gets parsed.

## Uploading a file

Choosing a file fills the editor the same way a directory template does: the browser reads it
with the File API and drops its text in, so you can read and edit it before going on to the form.
The file is then detached from the form -- the editor holds everything that matters, and sending
the original as well would mean your edits were parsed away, since an upload outranks the editor
text on the server.

Two cases the page handles rather than reading the file:

- A file that isn't `.j2` or `.txt` stays attached and is left to the server, so submitting gives
  the usual "Template files only" error instead of a file that was quietly ignored.
- A file over `MAX_CONTENT_LENGTH` is dropped with a note, because it could not be submitted
  anyway.

Without JavaScript none of this happens and the original behaviour stands: the file is uploaded
on submit and takes precedence over whatever is in the editor. The hint under the field says
which of the two you are getting.

## Configuration

All configuration is by environment variable.

| Variable             | Default    | Purpose                                                      |
|----------------------|------------|--------------------------------------------------------------|
| `SECRET_KEY`         | *(none)*   | Required. Signs CSRF tokens. The app refuses to start without it unless running in debug or test mode. |
| `MAX_CONTENT_LENGTH` | `262144`   | Max request body in bytes (256 KB).                          |
| `TEMPLATE_DIR`       | *(empty)*  | Directory of ready-made templates to list in the picker. Empty turns the picker off. The container image sets it to `/templates`. |
| `TEMPLATE_MAX_BYTES` | `65536`    | Files in `TEMPLATE_DIR` larger than this are not listed (64 KB). |
| `BIND_HOST`          | `0.0.0.0`  | Gunicorn bind address (container only).                      |
| `BIND_PORT`          | `8000`     | Gunicorn bind port (container only).                         |
| `GUNICORN_WORKERS`   | `2`        | Number of gunicorn workers (container only).                 |

Logs go to stdout only. Template accepted/rejected, validation failures, render outcomes, and
the name of a template loaded from the directory are logged -- but never the submitted values.

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
export SECRET_KEY="$(openssl rand -base64 32)"
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
(with a matching `-p`) to change the defaults, and `-v ~/dynaform-templates:/templates:ro,Z` to
fill the template picker (see [Template directory](#template-directory)).

### With the Quadlet unit

`dynaform.container` is a [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
unit: systemd generates the service from it, so there's no `.service` file to write.

It mounts `~/dynaform-templates` at `/templates` for the template picker, so create that
directory (or change the `Volume=` line, or delete it if you don't want the picker):

```bash
mkdir -p ~/dynaform-templates
```

It reads `SECRET_KEY` from a Podman secret rather than a plain environment line, so create the
secret next:

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
- `tests/test_template_library.py` — scanning the template directory: extensions, subdirectories,
  hidden files, the size cap, and symlinks aimed outside it.
- `tests/test_forms.py` — dynamic WTForms construction: field classes, labels, validators.
- `tests/test_routes.py` — the full HTTP path: parse → dynamic form → render.

`tests/conftest.py` sets a test `SECRET_KEY` and disables CSRF for the test client.

## Continuous integration

`.github/workflows/cicd.yml` is the whole pipeline. What runs depends on the event:

| Event                    | Lint | Test | Scan image | Scan published image | Publish |
|--------------------------|:----:|:----:|:----------:|:--------------------:|:-------:|
| Pull request to `main`   |  ✓   |  ✓   |     ✓      |                      |         |
| Push to `main`           |  ✓   |  ✓   |     ✓      |                      |         |
| Push of a `v*` tag       |  ✓   |  ✓   |            |                      |    ✓    |
| Weekly schedule / manual |      |      |            |          ✓           |         |

### Cutting a release

Push a tag, and the pipeline does the rest:

```bash
git tag -a v1.4.0 -m "Release 1.4.0"
git push origin v1.4.0
```

That builds the image, scans it, pushes it to `ghcr.io/<owner>/dynaform`, and *then* creates the
GitHub release — in that order, so a release existing means the image behind it passed. `v1.4.0`
publishes `:1.4.0`, `:1.4` and `:latest`; a prerelease tag (`v1.4.0-rc1`, anything with a hyphen)
publishes only `:1.4.0-rc1`, is marked as a pre-release, and never moves `:latest` — which matters
because `:latest` is what `dynaform.container` resolves by default.

Drafting a release in the GitHub web UI also works: that pushes the tag, which triggers the same
run. The workflow notices the release already exists and leaves your notes alone.

### Vulnerability scanning

Every image is scanned with [Trivy](https://github.com/aquasecurity/trivy) before it can be
published, and a failing scan stops the push. The policy lives in one place — the `SCAN_SEVERITY`
and `SCAN_VULN_TYPE` variables at the top of the workflow — so the pull-request gate cannot drift
from the release gate.

Two deliberate choices there:

- **`MEDIUM` is included**, which is wider than Trivy's own example. Every Jinja2 sandbox escape
  that has a fix (CVE-2024-56201, CVE-2024-56326, CVE-2025-27516) is rated MEDIUM by CVSS, and
  this app's security model *is* the sandbox. A CRITICAL/HIGH-only gate would wave through the one
  bug class that actually breaks DynaForm.
- **Unfixed vulnerabilities are ignored.** A Debian base always carries CVEs with no patch
  available; failing every release on those teaches people to bypass the gate rather than fix
  anything.

Note what that second choice does *not* cover. The findings that actually fail this gate are the
*fixable* ones, and most of them come from the base image rather than from anything in this
repository: `python:3.12-slim` is rebuilt on its own schedule, so between rebuilds its packages
fall behind Debian's security archive while patched versions sit in the archive unused. That is
why the `Containerfile` applies `apt-get upgrade` and upgrades `pip` — without it, a scan of an
otherwise untouched base image fails on tens of CVEs that have nothing to do with the change being
reviewed. Expect it to recur: each time Debian publishes updates ahead of a base-image rebuild,
the next build picks them up, and the weekly scan is what tells you an already-published image has
fallen behind.

The weekly run scans the *published* `:latest` image rather than a fresh build. That is the one
thing a build-time gate cannot do: catch a CVE disclosed after the image shipped, when nothing in
the repository has changed but the image on your host is newly vulnerable. It can also be run on
demand from the Actions tab.

Because the scan is a gate on pull requests too, a CVE disclosed against the base image overnight
can fail a pull request that had nothing to do with it. That is the intended trade — finding out
on a pull request beats finding out mid-release — and the fix is usually to rebuild on a fresher
base rather than to change anything in the diff.

## Project layout

```
app/
  __init__.py           application factory, logging hookup
  config.py             environment-driven config
  routes.py             GET / , POST / (parse or load), GET /template-library, POST /render
  forms.py              upload/picker form + dynamic form builder
  template_parser.py    parsing, validation, grouping
  template_library.py   read-only scan of TEMPLATE_DIR
  templates/            base, index, form, result
  static/js/            conditional-field toggle, editor sources, output copy/download
  static/vendor/        vendored Bootstrap 5
tests/
.github/workflows/
  cicd.yml              lint, test, scan, publish
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
  disk. `TEMPLATE_DIR` is read-only -- edits to a chosen template belong to the request that
  carries them, never to the file.
- Nothing from a request is ever joined onto a filesystem path. `read_template()` matches the
  submitted name against the directory scan instead, so traversal has nothing to traverse.
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
