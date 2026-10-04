# Example templates

Templates for trying DynaForm out. The first two test template reuse (`{% extends %}` /
`{% include %}` / `{% import %}`, see README.md's "Reusing templates" section), the third `L_`
lists. Copy this whole directory's contents into your `TEMPLATE_DIR`. An example that uses
partials refuses to parse if one is left behind.

- **`nginx-site.j2`** -- extends `_base.j2`, which includes `_header.j2`. Demonstrates
  `{% block %}` + `{{ super() }}`: the child adds its own locations on top of the
  base's, rather than replacing it -- any number from the `L_locations` list (path and port per
  row), plus an optional `/ws` one.
- **`systemd-service.j2`** -- imports `_macros.j2` with context and calls two macros, one of
  which reads a DynaForm variable directly from the caller's context rather than through a
  macro argument (only possible because the import is `with context`).
- **`iosxe-config.j2`** -- standalone, no partials. Demonstrates `L_` lists: VLANs and
  interfaces are each a list of rows, and every interface row chooses access or trunk. A trunk's
  allowed VLANs come from looping over `L_vlans` again inside the interface loop.

`_base.j2`, `_header.j2` and `_macros.j2` start with `_`, so they won't show up in the picker
themselves, only the templates above do.
