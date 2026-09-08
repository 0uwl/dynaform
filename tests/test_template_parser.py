"""Unit tests for app/template_parser.py: extraction, validation, grouping."""
import pytest

from app.template_parser import TemplateValidationError, parse_template


def test_fields_extracted_in_source_order():
    template = "{{ N_age }} and {{ S_name }} and {{ B_flag }}"
    parsed = parse_template(template)
    assert [f.var_name for f in parsed.fields] == ["N_age", "S_name", "B_flag"]
    assert [f.prefix for f in parsed.fields] == ["N", "S", "B"]


def test_label_capitalizes_only_first_word():
    parsed = parse_template("{{ N_user_age }} {{ S_username }}")
    labels = {f.var_name: f.label for f in parsed.fields}
    assert labels["N_user_age"] == "User age"
    assert labels["S_username"] == "Username"


def test_no_fields_for_static_template():
    parsed = parse_template("just static text, no variables here")
    assert parsed.fields == []
    assert parsed.radio_groups == []


def test_duplicate_variable_counted_once():
    parsed = parse_template("{{ S_name }} again {{ S_name }}")
    assert len(parsed.fields) == 1
    assert parsed.fields[0].var_name == "S_name"


class TestValidation:
    def test_rejects_variable_without_known_prefix(self):
        with pytest.raises(TemplateValidationError, match="Unrecognized variable"):
            parse_template("Hello {{ username }}")

    def test_rejects_variable_with_unknown_prefix_letter(self):
        with pytest.raises(TemplateValidationError, match="Unrecognized variable"):
            parse_template("{{ X_foo }}")

    def test_rejects_prefix_with_nothing_after_underscore(self):
        with pytest.raises(TemplateValidationError, match="Unrecognized variable"):
            parse_template("{{ S_ }}")

    def test_error_message_names_all_offending_variables(self):
        with pytest.raises(TemplateValidationError) as exc_info:
            parse_template("{{ foo }} {{ bar }}")
        message = str(exc_info.value)
        assert "foo" in message
        assert "bar" in message

    def test_rejects_syntax_errors(self):
        with pytest.raises(TemplateValidationError, match="syntax error"):
            parse_template("{% if %}")

    def test_rejects_unclosed_tag(self):
        with pytest.raises(TemplateValidationError):
            parse_template("{{ S_name")


class TestJinjaScoping:
    """Confirms find_undeclared_variables correctly excludes bound names."""

    def test_set_variables_are_not_required(self):
        parsed = parse_template("{% set total = 1 + 1 %}{{ total }}")
        assert parsed.fields == []

    def test_loop_variables_are_not_required(self):
        parsed = parse_template("{% for x in [1, 2] %}{{ x }}{% endfor %}")
        assert parsed.fields == []

    def test_variable_used_only_inside_loop_is_still_required(self):
        parsed = parse_template("{% for x in [1, 2] %}{{ S_label }}{% endfor %}")
        assert [f.var_name for f in parsed.fields] == ["S_label"]


class TestRadioGroups:
    def test_options_collected_in_source_order(self):
        parsed = parse_template("{{ R_color_red }} {{ R_color_blue }} {{ R_color_green }}")
        assert len(parsed.radio_groups) == 1
        group = parsed.radio_groups[0]
        assert group.name == "color"
        assert group.label == "Color"
        assert group.options == [("red", "Red"), ("blue", "Blue"), ("green", "Green")]

    def test_multiword_option_label(self):
        parsed = parse_template("{{ R_color_light_blue }}")
        assert parsed.radio_groups[0].options == [("light_blue", "Light blue")]

    def test_two_groups_stay_separate(self):
        parsed = parse_template("{{ R_color_red }} {{ R_size_small }}")
        names = {g.name for g in parsed.radio_groups}
        assert names == {"color", "size"}

    def test_group_without_option_is_rejected(self):
        with pytest.raises(TemplateValidationError, match="R_<group>_<option>"):
            parse_template("{{ R_color }}")

    def test_groups_ordered_by_first_option_occurrence(self):
        parsed = parse_template("{{ R_size_small }} {{ R_color_red }} {{ R_size_large }}")
        assert [g.name for g in parsed.radio_groups] == ["size", "color"]


class TestConditionalFields:
    def test_checkbox_child_fields_get_parent(self):
        parsed = parse_template("{% if B_admin %}{{ S_admin_name }} {{ P_admin_pass }}{% endif %}")
        by_name = {f.var_name: f for f in parsed.fields}
        assert by_name["S_admin_name"].parent == "admin"
        assert by_name["P_admin_pass"].parent == "admin"

    def test_checkbox_itself_has_no_parent(self):
        parsed = parse_template("{{ B_admin }} {{ S_admin_name }}")
        by_name = {f.var_name: f for f in parsed.fields}
        assert by_name["B_admin"].parent is None

    def test_unrelated_field_has_no_parent(self):
        parsed = parse_template("{{ B_admin }} {{ S_username }}")
        by_name = {f.var_name: f for f in parsed.fields}
        assert by_name["S_username"].parent is None

    def test_similarly_named_field_without_underscore_boundary_is_not_a_child(self):
        # B_admin should not claim S_administrator as a child.
        parsed = parse_template("{{ B_admin }} {{ S_administrator }}")
        by_name = {f.var_name: f for f in parsed.fields}
        assert by_name["S_administrator"].parent is None

    def test_longest_matching_checkbox_base_wins(self):
        template = "{{ B_admin }} {{ B_admin_role }} {{ S_admin_role_title }}"
        parsed = parse_template(template)
        by_name = {f.var_name: f for f in parsed.fields}
        assert by_name["S_admin_role_title"].parent == "admin_role"

    def test_radio_group_can_be_a_direct_checkbox_child(self):
        template = "{{ B_admin }} {{ R_admin_light }} {{ R_admin_dark }}"
        parsed = parse_template(template)
        assert parsed.radio_groups[0].name == "admin"
        assert parsed.radio_groups[0].parent == "admin"

    def test_radio_group_without_matching_checkbox_has_no_parent(self):
        parsed = parse_template("{{ R_color_red }} {{ R_color_blue }}")
        assert parsed.radio_groups[0].parent is None
