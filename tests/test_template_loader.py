"""Unit tests for app/template_loader.py: LibraryLoader.get_source."""
import pytest
from jinja2 import TemplateNotFound

from app.template_loader import LibraryLoader

TEMPLATE = "Hello {{ S_name }}\n"


class TestLibraryLoader:
    def test_no_directory_configured_raises_with_message(self, app):
        app.config["TEMPLATE_DIR"] = ""
        with app.test_request_context(), pytest.raises(
            TemplateNotFound, match="no template directory is configured"
        ):
            LibraryLoader().get_source(None, "base.j2")

    def test_unlisted_name_raises_with_message(self, app, library):
        (library / "real.j2").write_text(TEMPLATE)
        with app.test_request_context(), pytest.raises(
            TemplateNotFound, match="not found in the directory"
        ):
            LibraryLoader().get_source(None, "nope.j2")

    def test_symlink_escaping_the_root_is_not_loadable(self, app, library, tmp_path):
        outside = tmp_path.parent / "outside.j2"
        outside.write_text("secrets")
        (library / "escape.j2").symlink_to(outside)
        with app.test_request_context(), pytest.raises(TemplateNotFound):
            LibraryLoader().get_source(None, "escape.j2")

    def test_oversized_file_is_not_loadable(self, app, library):
        app.config["TEMPLATE_MAX_BYTES"] = 16
        (library / "huge.j2").write_text("x" * 64)
        with app.test_request_context(), pytest.raises(TemplateNotFound):
            LibraryLoader().get_source(None, "huge.j2")

    def test_underscore_prefixed_partial_is_loadable(self, app, library):
        (library / "_header.j2").write_text(TEMPLATE)
        with app.test_request_context():
            source, _filename, uptodate = LibraryLoader().get_source(None, "_header.j2")
            assert source == TEMPLATE
            assert uptodate()

    def test_a_listed_file_loads_its_exact_text(self, app, library):
        (library / "base.j2").write_text(TEMPLATE)
        with app.test_request_context():
            source, _filename, _uptodate = LibraryLoader().get_source(None, "base.j2")
            assert source == TEMPLATE
