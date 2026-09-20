# Handoff: template reuse (include / import / extends / blocks)

You are picking up a feature that has been designed and evidence-checked but not started. No
code has been written. This file is the whole context; the conversation that produced it is gone.

Read `README.md` first — particularly **Template syntax**, **Template directory** and **Your own
Jinja logic** — then this.

## Goal

Let a DynaForm template reuse other templates from `TEMPLATE_DIR`:

```jinja
{% extends "_base.j2" %}
{% block server %}
{% include "_header.j2" %}
{{ super() }}
listen {{ N_port }};
{% endblock %}
```

`{% include %}`, `{% import %}`, `{% extends %}`, `{% block %}` and `{{ super() }}` should all
work, and the form should ask for every variable the graph actually uses.

## The core insight

**Give the Jinja environment a loader and the language stops being a problem.** Jinja resolves
inheritance natively; we do not reimplement or approximate any of it. An earlier design tried to
splice included text together and hit walls that a loader makes disappear (`without context` is
inexpressible by splicing, `import as` needs a module object, `extends` needs block resolution
plus `super()` inlining).

Crucially, **the edited template stays a string** — the loader only supplies what it *references*:

```python
env = SandboxedEnvironment(loader=LibraryLoader())
env.from_string(edited_source).render(**values)   # extends/include/import all resolve
```

So `template_source` remains plain text in the hidden field. No bundle, no new format, no change
to the two-request model.

What is left for us is **not rendering — it is discovery**: which fields to put on the form.

## Verified findings

Re-run any of these if you doubt them; they were all executed against this repo's Jinja
(`Jinja2==3.1.6`, `SandboxedEnvironment`).

**1. `from_string` + loader resolves everything.** With `base.j2` containing a `body` block and
`macros.j2` containing a `port` macro:

```python
EDITED = '''{% extends "base.j2" %}
{% import "macros.j2" as m with context %}
{% block body %}{% include "header.j2" %}{{ super() }}{{ m.port(N_port) }}{% endblock %}'''
env.from_string(EDITED).render(S_site="example", S_host="h1", N_port=8080, S_unused_default="D")
# 'site example\n\n# header for h1\ndefault D\nlisten 8080;\n\nend'
```

**2. Naive discovery collects dead fields.** A recursive union over the graph also collected
`S_unused_default`, which lives in a base block the child overrides. It never renders, and it
would be a **required** field. This is the main thing the discovery rules exist to prevent.

**3. What a child with `{% extends %}` actually renders** (base = `"B1 {% block a %}A-base
{{ S_a_base }}{% endblock %} B2 {% block b %}B-base{% endblock %} {{ S_base_top }}"`):

| child | renders | naive discovery collects |
|---|---|---|
| `stray {{ S_stray }}{% block a %}A-child {{ S_in_a }}{% endblock %}` | `B1 A-child INA B2 B-base BT` | `S_in_a`, `S_stray` (stray is **dead**) |
| `{% block nosuch %}{{ S_ghost }}{% endblock %}` | `B1 A-base AB B2 B-base BT` | `S_ghost` (**dead** — no such block in base) |
| `{% set S_base_top = S_set_src %}` | `B1 A-base AB B2 B-base SRC` | `S_set_src` (**live** — top-level set works) |

**4. Not everything outside a child's blocks is dead.** This one is a trap:

```
include at child top level   -> '(part P)[base A]'   <- RENDERS, before the base
include inside a block       -> '[base (part P)]'
import at child top level    -> '[base (part )]'     <- import is without context by default
```

So "ignore everything outside blocks in a child" would be **wrong**.

**5. `{% import %}` is *without context* by default.** A macro reading `{{ S_x }}`:

```
real {% import %}        -> 'x='        <- cannot see the form's values
real, with context       -> 'x=VALUE'
```

Variables inside a without-context template are therefore not fillable and must not be collected.

**6. `find_referenced_templates` returns `None`** for a name it cannot resolve statically
(`{% include S_choice %}`). It also does not expose context modifiers — walk the
`Extends` / `Include` / `Import` / `FromImport` nodes directly instead.

**7. An `extends` cycle raises `RecursionError`.** At render that is already a clean 400 (see
the blind catch in `render()`); the discovery walk needs its own cycle detection.

## Settled decisions — do not relitigate

| Decision | Choice |
|---|---|
| Editor content | **Child only.** Bases and partials are library files on disk. |
| `template_source` | Stays plain text. |
| Partial convention | Files beginning with `_` are **loadable but not listed in the picker**. |
| Field set changes between parse and render | **Refuse** with a clear message. |
| Depth / count limits | 10 deep, 50 templates. Provisional; easy to change. |
| `without context` | **Allowed**, but its variables are not collected. |
| Dynamic include names | Refused at parse. |

## Where things are now

- `app/routes.py` — `_RENDER_ENV = SandboxedEnvironment(loader=_NoOtherTemplates())`. That
  loader exists purely to refuse include/import/extends with a readable message; **replace it**.
  `render()` has a deliberate blind `except Exception` (with `# noqa: BLE001`) so a template can
  never 500 the route — keep that.
- `app/template_parser.py` — `parse_template(source)` is pure Python, no Flask import. Keep it
  that way: pass a `resolve` callable rather than importing `current_app`.
- `app/template_library.py` — `list_templates()` / `read_template()`. **The safety lives here**:
  resolve, confirm the path stays under the root, size cap (`TEMPLATE_MAX_BYTES`), skip dotfiles,
  `os.walk(followlinks=False)`. Never build a path from a submitted name; match against the scan.
- `tests/conftest.py` — the `library` fixture points `TEMPLATE_DIR` at a tmp dir.
- `tests/test_routes.py` — `_extract()` unescapes the hidden field (a browser does too; without
  it, a template containing `"` breaks the test, not the app).

## Plan

### Step 1 — `app/template_loader.py` (new, ~70 lines)

A `BaseLoader` whose `get_source` resolves a name **against `template_library`'s scan**, not by
joining it onto a path. Serves `_`-prefixed files; raises `TemplateNotFound` with a readable
message for anything unlisted, and when no directory is configured. Build the environment with
`cache_size=0` — small directory, low traffic, and a stale compiled template would be worse than
re-parsing.

Tests: unlisted name, symlink escaping the root, oversized file, no directory configured,
`_`-prefixed file loadable but absent from the picker.

### Step 2 — discovery in `app/template_parser.py` (~120 lines)

```python
def parse_template(source: str, resolve: Callable[[str], str] | None = None) -> ParsedTemplate
```

Walk from the edited source following `Extends` / `Include` / `Import` / `FromImport` nodes,
unioning `find_undeclared_variables` at each, with cycle and depth limits.

**Two reachability rules — both verified, both worth the code:**

- **Dead child blocks.** A `{% block %}` in the child whose name exists nowhere in the base chain
  never renders (finding 3, row 2). Skip it.
- **Overridden base blocks.** A base block the child overrides *without* calling `super()` never
  renders (finding 2). Skip the base's body. `super()` is detectable — look for a `Call` whose
  node is a `Name` named `super` inside the block.

**Deliberately not implemented, and why:**

- *Stray output outside a child's blocks.* Dead, but finding 4 shows `include` and `set` at the
  same position are **live**. A wrong rule here drops real fields; over-collecting only affects
  templates containing stray output, which Jinja silently discards anyway (an authoring mistake).
- *Cross-template `{% set %}` shadowing.* If the child sets a variable the base reads, the form
  asks for a value the set overrides. Small fix (drop variables assigned by a top-level `Assign`
  in the child) but unproven — add it later with tests rather than now.

Do not collect variables from a template referenced *without context* (finding 5).

### Step 3 — wire into `app/routes.py` (~40 lines)

`parse()` and `render()` both pass `resolve`. `render()` re-walks the graph and compares the field
set with the form it is validating; if the directory changed underneath, refuse — do not render
something the form was not built for.

### Step 4 — picker hygiene and docs

`list_templates()` keeps `_`-prefixed files out of the picker while the loader still serves them.
README: replace **What isn't available** with a **Reusing templates** section (worked base/child
example, the `_` convention, what is refused and why, the refuse-on-drift behaviour), and drop the
include item from **Ideas for later**, keeping `L_`.

### Refusals

| Situation | Behaviour |
|---|---|
| `{% include S_choice %}` | Refuse at parse — the fields cannot be known |
| Cycle | Refuse, naming the loop |
| Depth > 10 or > 50 templates | Refuse |
| Referenced template not in the directory | Refuse, naming it |
| No `TEMPLATE_DIR` configured | Refuse, saying a directory is needed |
| Field set changed between parse and render | Refuse, ask for a reparse |

## Security

Templates stay sandboxed. The loader's reach is the template directory, which the picker already
exposes in full — so no new exposure. **Document that `_` is a tidiness convention, not a
permission boundary**: hidden-from-picker files are still loadable by name.

## Traps in this repo

- **Ruff.** CI pins `ruff==0.16.7`; a 0.15.x binary may shadow it on `PATH`. Always run
  `python3 -m ruff check .`, never bare `ruff` — the versions disagree about `BLE001`.
- **No Docker daemon** in the dev container. Container changes cannot be built or scanned locally.
- **Chromium blocks some ports** (5061 is one). Use 5075+ for a dev server you intend to drive
  with Playwright (`executable_path="/opt/pw-browsers/chromium"`).
- **CI does not run on feature branches** — only pushes to `main` and pull requests. Run
  `python3 -m pytest -q` and `python3 -m ruff check .` yourself before pushing.
- **Conventions:** never log submitted field values (names only); both parsing and rendering go
  through `SandboxedEnvironment`; field classes come from the `FIELD_CLASSES` whitelist; modules
  carry type hints and `from __future__ import annotations`; comments explain *why*.

## Definition of done

- `python3 -m pytest -q` green (157 passed / 1 skipped before this feature).
- `python3 -m ruff check .` clean under 0.16.7.
- A base + child + partial in a real `TEMPLATE_DIR`, driven end to end: the form lists exactly the
  live variables, the render output is correct, and a dead field from an overridden block is
  absent.
- README no longer says include/import/extends are unavailable.
