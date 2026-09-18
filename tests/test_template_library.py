"""Unit tests for app/template_library.py: scanning, filtering, containment."""
import os

import pytest

from app.template_library import library_root, list_templates, read_template

TEMPLATE = "Hello {{ S_name }}\n"


def _names(app):
    with app.test_request_context():
        return [template.name for template in list_templates()]


class TestLibraryRoot:
    def test_unset_template_dir_disables_the_library(self, app):
        app.config["TEMPLATE_DIR"] = ""
        with app.test_request_context():
            assert library_root() is None
            assert list_templates() == []

    def test_missing_directory_is_not_an_error(self, app, tmp_path):
        app.config["TEMPLATE_DIR"] = str(tmp_path / "nope")
        with app.test_request_context():
            assert library_root() is None
            assert list_templates() == []

    def test_file_instead_of_directory_is_ignored(self, app, tmp_path):
        target = tmp_path / "a-file.j2"
        target.write_text(TEMPLATE)
        app.config["TEMPLATE_DIR"] = str(target)
        with app.test_request_context():
            assert library_root() is None


class TestListing:
    def test_lists_j2_and_txt_files_sorted(self, app, library):
        (library / "b.j2").write_text(TEMPLATE)
        (library / "a.txt").write_text(TEMPLATE)
        assert _names(app) == ["a.txt", "b.j2"]

    def test_other_extensions_are_skipped(self, app, library):
        (library / "notes.md").write_text(TEMPLATE)
        (library / "script.sh").write_text(TEMPLATE)
        assert _names(app) == []

    def test_subdirectories_are_listed_by_relative_path(self, app, library):
        (library / "linux").mkdir()
        (library / "linux" / "sshd.j2").write_text(TEMPLATE)
        assert _names(app) == ["linux/sshd.j2"]

    def test_hidden_files_and_directories_are_skipped(self, app, library):
        (library / ".secret.j2").write_text(TEMPLATE)
        (library / ".git").mkdir()
        (library / ".git" / "config.txt").write_text(TEMPLATE)
        assert _names(app) == []

    def test_files_over_the_size_limit_are_skipped(self, app, library):
        app.config["TEMPLATE_MAX_BYTES"] = 16
        (library / "small.j2").write_text("{{ S_x }}")
        (library / "huge.j2").write_text("x" * 64)
        assert _names(app) == ["small.j2"]

    def test_label_matches_the_relative_path(self, app, library):
        (library / "linux").mkdir()
        (library / "linux" / "sshd.j2").write_text(TEMPLATE)
        with app.test_request_context():
            assert [t.label for t in list_templates()] == ["linux/sshd.j2"]

    def test_new_file_appears_without_a_restart(self, app, library):
        assert _names(app) == []
        (library / "later.j2").write_text(TEMPLATE)
        assert _names(app) == ["later.j2"]


class TestSymlinks:
    def test_symlink_pointing_outside_the_root_is_not_listed(self, app, library, tmp_path):
        outside = tmp_path.parent / "outside.j2"
        outside.write_text("secrets")
        (library / "escape.j2").symlink_to(outside)
        assert _names(app) == []

    def test_symlink_inside_the_root_is_listed(self, app, library):
        (library / "real.j2").write_text(TEMPLATE)
        (library / "alias.j2").symlink_to(library / "real.j2")
        assert _names(app) == ["alias.j2", "real.j2"]

    def test_symlinked_directory_is_not_descended_into(self, app, library, tmp_path):
        outside = tmp_path.parent / "outside-dir"
        outside.mkdir(exist_ok=True)
        (outside / "escape.j2").write_text("secrets")
        (library / "link").symlink_to(outside, target_is_directory=True)
        assert _names(app) == []

    def test_broken_symlink_is_skipped(self, app, library):
        (library / "dangling.j2").symlink_to(library / "gone.j2")
        assert _names(app) == []


class TestReadTemplate:
    def test_reads_a_listed_template(self, app, library):
        (library / "greet.j2").write_text(TEMPLATE)
        with app.test_request_context():
            assert read_template("greet.j2") == TEMPLATE

    def test_unknown_name_returns_none(self, app, library):
        with app.test_request_context():
            assert read_template("nope.j2") is None

    @pytest.mark.parametrize(
        "name",
        ["../outside.j2", "../../etc/passwd", "/etc/passwd", "", "sub/../../outside.j2"],
    )
    def test_traversal_attempts_find_nothing(self, app, library, tmp_path, name):
        (tmp_path.parent / "outside.j2").write_text("secrets")
        (library / "greet.j2").write_text(TEMPLATE)
        with app.test_request_context():
            assert read_template(name) is None

    def test_undecodable_bytes_do_not_raise(self, app, library):
        (library / "binary.j2").write_bytes(b"\xff\xfe{{ S_x }}")
        with app.test_request_context():
            assert "{{ S_x }}" in read_template("binary.j2")

    def test_unreadable_file_returns_none(self, app, library):
        if os.geteuid() == 0:
            pytest.skip("root ignores file permissions")
        blocked = library / "blocked.j2"
        blocked.write_text(TEMPLATE)
        blocked.chmod(0o000)
        with app.test_request_context():
            assert read_template("blocked.j2") is None
