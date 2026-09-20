# Example templates

Two templates for testing template reuse (`{% extends %}` / `{% include %}` / `{% import %}`,
see README.md's "Reusing templates" section). Copy this whole directory's contents into your
`TEMPLATE_DIR`. Each example depends on its partials, and a partial left behind means the
example refuses to parse.

- **`nginx-site.j2`** -- extends `_base.j2`, which includes `_header.j2`. Demonstrates
  `{% block %}` + `{{ super() }}`: the child adds an optional `/ws` location on top of the
  base's own, rather than replacing it.
- **`systemd-service.j2`** -- imports `_macros.j2` with context and calls two macros, one of
  which reads a DynaForm variable directly from the caller's context rather than through a
  macro argument (only possible because the import is `with context`).

`_base.j2`, `_header.j2` and `_macros.j2` start with `_`, so they won't show up in the picker
themselves, only the two templates above do.
