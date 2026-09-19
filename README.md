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

### Your own Jinja logic

The prefix rule applies only to variables the template *doesn't define itself*. Anything Jinja
declares as it goes — `{% set %}`, loop variables, macro arguments, `{% with %}` — needs no prefix
and never becomes a form field. So does Jinja's own furniture: `range()`, `namespace()`, `dict()`,
`loop.index`, every filter and test.

That means ordinary templating works as you'd expect, with the form asking only for the
`S_`/`P_`/`N_`/`B_`/`R_` variables:

```jinja
{% set scheme = "https" if B_tls else "http" %}
upstream {{ S_service }} {
{% for port in [8080, 8443] %}
    server {{ S_host }}:{{ port }};
{% endfor %}
}
listen {{ scheme }}://{{ S_host }};
```

`scheme` and `port` are the template's own. The form asks for **Service**, **Host** and **Tls**.

One thing is refused: a variable that is never defined anywhere and carries no prefix.

```jinja
Hello {{ username }}
```

> Unrecognized variable name(s): username. Every variable must start with S_, P_, N_, B_, or R_.

That is deliberate — it's the check that catches a forgotten prefix, which would otherwise leave
you with a form missing a field and output with a silent blank in it. Either prefix the name so it
becomes a field (`S_username`), or define it in the template with `{% set %}`.

#### Templates from other systems

Variables belonging to something else — Ansible, Helm, a CI system — would be consumed by this
render and come out empty. Wrap them in `{% raw %}` to pass them through untouched:

```jinja
server {{ S_host }}
inventory {% raw %}{{ ansible_hostname }}{% endraw %}
```

renders as `server example.com` and `inventory {{ ansible_hostname }}`, leaving the second
template's variables for the second template's renderer.

#### What isn't available

`{% include %}`, `{% import %}` and `{% extends %}` don't work: DynaForm renders one template on
its own, and there is no template directory for them to reach into — which is also what stops a
template reading files off the host. Using them fails the render with a message saying so, rather
than producing anything.

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

### Default values

Give a variable a default with Jinja's own [`default`
filter](https://jinja.palletsprojects.com/en/stable/templates/#jinja-filters.default) (or its
`d` alias). There is no second file and no DynaForm-specific syntax — the template stays an
ordinary Jinja template, and renders the same way outside DynaForm:

```jinja
Hello {{ S_username | default("John Doe") }}, you are {{ N_user_age | default(45) }}.
```

The form arrives with those values already in the boxes. Edit them, or leave them as they are.
Clear one and submit, and the default is what gets rendered — DynaForm leaves the variable
undefined and lets the filter do its job, so the value you get is exactly the one the template
says, `default(x, true)` included.

**A default makes the field optional.** It has to: submitting the field blank is how you ask for
the default, so it can't also be required. Nothing in the template says so out loud, which is why
it is written down here — adding a default to tidy up a form also stops that field being
mandatory.

A few specifics:

- **Checkboxes and radios start in that state rather than falling back to it.**
  `{{ B_admin | default(true) }}` ships the box ticked; `{{ R_color_blue | default(true) }}`
  preselects Blue. They can't work as fallbacks: an unchecked box submits nothing at all, so a
  fallback would tick it straight back on and leave no way to turn it off.
- **The first default for a variable wins** in the form. Writing two is a slip, and a form can
  only show one. (Jinja applies each one where it stands, so such a template renders both.)
- **Only literals can be shown.** `{{ S_ref | default(S_name) }}` still makes the field optional
  and Jinja still resolves it at render time, but the form has nothing to prefill — it cannot know
  the value before rendering, and guessing would be worse than showing nothing.
- **The default applies where the filter is, not everywhere.** Leave a defaulted field blank and
  `{{ S_x | default("John") }}` renders `John`, while a bare `{{ S_x }}` elsewhere in the same
  template renders empty — the variable is undefined and only the filter fills it in. Repeat the
  filter, or fill the field in, if you use the variable more than once.

#### Defaults on password fields

You can default a `P_` field, and it works like any other. Be aware of where the value ends up:

```jinja
{{ P_token | default("from-template") }}
```

The password input itself never carries it — WTForms doesn't render a password's value, so the
box shows up empty with a note that a default is set. **But the default lives in the template
text, and the template text is on the page**: in the editor on the first page, and in the hidden
field that carries the source to the second. So the value appears in the HTML of both pages, in
browser history, and anywhere that caches them. DynaForm also has no authentication, so anyone who
can reach it can read the template and its defaults.

That is fine for a shared team value or a throwaway credential. It is not fine for anything you
would have to rotate if it leaked — type those in instead.

#### If you already use `default`

Before this existed, `default` in a DynaForm template did nothing: every variable was passed to
the renderer whether or not its field was filled in, and the filter only fires on variables that
are *undefined*. Such templates now behave as they read. Worth a look if you have any.

## Template directory

Point `TEMPLATE_DIR` at a directory of ready-made templates and DynaForm lists them in a picker
at the top of the first page. The container image sets `TEMPLATE_DIR=/templates` already, so all
you have to do is bind-mount a host directory there. The directory has to exist before the
container starts — `podman run` creates a missing one, but the Quadlet unit does not, which is why
its `Volume=` line ships commented out:

```bash
podman run --rm -p 8000:8000 \
  -v ~/dynaform-templates:/templates:ro,Z \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  localhost/dynaform:latest
```

The first page then offers two sources side by side: **Choose a template**, the directory
listing, and **Upload a file**, above the **Template text** editor that both of them fill.
Whatever is in that editor when you press **Parse template** is what gets sent to the server
and parsed.

Choosing a template copies its text into the editor so you can read it and change it before
continuing to the form. Those edits are not saved to the file, they are entirely temporary for
this request.

What gets listed from the directory:

- Files ending in `.j2` or `.txt`, including ones in subdirectories. A subdirectory shows up as
  part of the name (`linux/sshd.j2`).
- No files or directories whose name starts with `.`, so a stray `.git` directory stays out of
  the list.
- No files bigger than `TEMPLATE_MAX_BYTES` (64 KB by default), because a chosen template
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
The file is then detached from the form and not sent to the server.

Two cases the page handles rather than reading the file:

- A file that isn't `.j2` or `.txt` stays attached and is left to the server, so submitting gives
  the usual "Template files only" error instead of a file that was quietly ignored.
- A file over `MAX_CONTENT_LENGTH` is dropped with a note, because it could not be submitted
  anyway.

Without JavaScript none of this happens and the original behaviour stands: the file is uploaded
on submit and takes precedence over whatever is in the editor

## Configuration

All configuration is by environment variable.

| Variable             | Default    | Purpose                                                      |
|----------------------|------------|--------------------------------------------------------------|
| `SECRET_KEY`         | *(none)*   | Required. Signs CSRF tokens. The app refuses to start without it (and logs how to set one) unless running in debug or test mode. |
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

The container runs as a non-root user, and has Bootstrap 5 baked in, so nothing
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

It points at the published image, so there is nothing to build. Rootless (recommended):

```bash
mkdir -p ~/.config/containers/systemd
curl -fsSL -o ~/.config/containers/systemd/dynaform.container \
  https://raw.githubusercontent.com/0uwl/dynaform/main/dynaform.container
```

Then set the signing key, which is the one thing you have to do by hand. `SECRET_KEY` signs the
CSRF token on every form and DynaForm refuses to start without it. Generate one:

```bash
openssl rand -base64 32
```

and paste it into the unit's empty `SECRET_KEY` line:

```ini
Environment=SECRET_KEY=<the generated key>
```

Now start it:

```bash
systemctl --user daemon-reload
systemctl --user start dynaform
```

The service won't come up without the `SECRET_KEY`. `journalctl --user -u dynaform` will have these same
instructions.

The template picker is off by default: the unit's `Volume=` line is commented out. Podman doesn't
create the source of a bind mount for a Quadlet unit, so a line pointing at a directory that isn't
there stops the service from starting rather than just leaving the picker empty. To turn it on,
make the directory first, then uncomment the line and restart:

```bash
mkdir -p ~/.local/share/dynaform/templates
systemctl --user daemon-reload
systemctl --user restart dynaform
```

An empty directory is fine once it exists — it just means the picker has nothing to list.

For a system-wide service, copy the unit to `/etc/containers/systemd/` instead and drop `--user` from the
`systemctl` commands. Note that `%h` in the `Volume=` line then resolves to root's home rather
than yours, and that the unit holds your key, so keep it readable only by root (`chmod 600`).

#### Pinning a version

`Image=` tracks `ghcr.io/0uwl/dynaform:latest`, which moves to each new full release. 
A `systemctl --user restart` after a `podman pull` therefore picks up the latest release if a new one has 
been released. To have more control over the version, replace the tag with the version you want:

```ini
Image=ghcr.io/0uwl/dynaform:1.4.0
```

`:1.4` works too, and follows patch releases within that minor. The tags a release publishes are
listed under [Cutting a release](#cutting-a-release).

#### Keeping the secret key out of the unit file

A secret key in the unit is fine for a host you are the only user of. It is worth knowing where it ends 
up, though: Quadlet turns `Environment=` into an `--env` argument on the generated `podman run` 
command line, so it is visible to anyone who can read the unit, run `systemctl --user cat dynaform`, 
or catch the process in `ps`, and it travels with the file into backups.

To hand it to Podman instead, create the secret first:

```bash
openssl rand -base64 32 | podman secret create dynaform-secret-key -
```

then delete or comment out the `Environment=SECRET_KEY=` line, and add this under `[Container]`:

```ini
Secret=dynaform-secret-key,type=env,target=SECRET_KEY
```

Secrets belong to the user who created them, so a system-wide service needs its own. Either way
the value is an environment variable inside the container, so anyone who can `podman exec` into
DynaForm can read it.

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

#### Running your own build instead

Point `Image=` at the image `podman build` leaves in local storage, and Podman stops reaching for
the registry:

```ini
Image=localhost/dynaform:latest
```

Then rebuild before restarting after a code change:

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
why the `Containerfile` applies `apt-get upgrade` and upgrades `pip`. Without it, a scan of an
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

A few conventions the existing code follows:

- Both parsing and rendering of user-supplied templates go through Jinja2's
  `SandboxedEnvironment`. Keep it that way.
- Form field classes come from the fixed `FIELD_CLASSES` whitelist in `app/forms.py`, keyed by the
  validated prefix letter. Never build a field class from user input.
- The rendered output is escaped by Flask's own Jinja environment in `result.html`, which is what
  prevents reflected XSS. It is deliberately a different environment from the sandboxed one.
- Never log submitted field values; a `P_` field is a password by definition.
- No persistence: no database, no session storage of template content, no writing user content to
  disk. `TEMPLATE_DIR` is read-only. Edits to a chosen template are never written to the file.
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
