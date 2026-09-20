"""A Jinja loader scoped to the directory template_library already exposes.

Resolves a referenced name (``{% include %}``, ``{% import %}``, ``{% extends
%}``) the same way ``read_template`` does: matched against the directory scan,
never joined onto a path. That scan is where containment, the size cap and the
dotfile skip already live -- this loader reuses it rather than duplicating it.

Unlike the picker, this loader serves ``_``-prefixed files: the leading
underscore is a listing convention (see ``template_library.list_templates``),
not a permission boundary. Anything in the directory is already readable
through the picker, so a partial being loadable-but-unlisted grants no new
reach.
"""
from __future__ import annotations

from jinja2 import BaseLoader, TemplateNotFound

from .template_library import library_root, read_template


class LibraryLoader(BaseLoader):
    """Serves templates from TEMPLATE_DIR by name, or refuses readably."""

    def get_source(self, environment, template):
        if library_root() is None:
            raise TemplateNotFound(
                template,
                message=(
                    f"this template refers to another template ({template}), "
                    "but no template directory is configured -- there is "
                    "nothing to include, import or extend"
                ),
            )
        source = read_template(template)
        if source is None:
            raise TemplateNotFound(
                template,
                message=f"referenced template not found in the directory: {template}",
            )
        return source, None, lambda: True
