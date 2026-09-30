"""A Jinja loader scoped to the directory template_library already exposes.

Resolves a referenced name (``{% include %}``, ``{% import %}``, ``{% extends
%}``) the same way ``read_template`` does: matched against the directory scan.

Unlike the picker, this loader serves ``_``-prefixed files
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
                    "but no template directory is configured. There is "
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
