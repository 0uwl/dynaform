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


class TestLiteralDefaults:
    def _field(self, parsed, var_name):
        return next(f for f in parsed.fields if f.var_name == var_name)

    def test_string_default_is_extracted(self):
        parsed = parse_template('{{ S_name | default("John Doe") }}')
        assert self._field(parsed, "S_name").default == "John Doe"

    def test_number_default_keeps_its_type(self):
        parsed = parse_template("{{ N_age | default(45) }}")
        assert self._field(parsed, "N_age").default == 45

    def test_the_d_alias_works_too(self):
        parsed = parse_template('{{ S_nick | d("Jo") }}')
        assert self._field(parsed, "S_nick").default == "Jo"

    def test_a_field_without_a_default_has_none(self):
        parsed = parse_template("{{ S_name }}")
        assert self._field(parsed, "S_name").default is None

    def test_variable_is_still_discovered_through_the_filter(self):
        parsed = parse_template('{{ S_name | default("John") }}')
        assert [f.var_name for f in parsed.fields] == ["S_name"]

    def test_first_occurrence_wins(self):
        parsed = parse_template('{{ S_x | default("first") }} {{ S_x | default("second") }}')
        assert self._field(parsed, "S_x").default == "first"

    def test_non_literal_defaults_have_no_shown_value(self):
        # Jinja still honours `default(S_other)` when rendering; it just
        # cannot be shown in the form, so the form offers nothing rather than
        # guessing.
        parsed = parse_template('{{ S_name }}{{ S_ref | default(S_name) }}')
        assert self._field(parsed, "S_ref").default is None

    def test_non_literal_defaults_still_count_as_defaults(self):
        # Nothing to show, but the field is still optional and Jinja still
        # resolves it -- so it must not be treated as having no default.
        parsed = parse_template('{{ S_name }}{{ S_ref | default(S_name) }}')
        assert self._field(parsed, "S_ref").has_default is True

    def test_a_literal_default_also_sets_has_default(self):
        parsed = parse_template('{{ S_name | default("John") }}')
        assert self._field(parsed, "S_name").has_default is True

    def test_a_field_without_a_default_says_so(self):
        parsed = parse_template("{{ S_name }}")
        assert self._field(parsed, "S_name").has_default is False

    def test_default_on_an_expression_is_ignored(self):
        parsed = parse_template('{{ S_a }}{{ (S_a ~ "x") | default("y") }}')
        assert self._field(parsed, "S_a").default is None

    def test_default_survives_a_chained_filter(self):
        parsed = parse_template('{{ S_name | default("John") | upper }}')
        assert self._field(parsed, "S_name").default == "John"

    def test_default_inside_a_tag_is_found(self):
        parsed = parse_template('{% if S_name | default("John") %}hi{% endif %}')
        assert self._field(parsed, "S_name").default == "John"

    def test_a_keyword_form_default_still_counts(self):
        # `default(default_value="kw")` puts the literal in kwargs, so there is
        # nothing to prefill -- but the field still has a default.
        parsed = parse_template('{{ S_y | default(default_value="kw") }}')
        field = self._field(parsed, "S_y")
        assert field.has_default is True
        assert field.default is None

    def test_an_empty_string_default_is_a_default(self):
        parsed = parse_template('{{ S_z | default("") }}')
        field = self._field(parsed, "S_z")
        assert field.has_default is True
        assert field.default == ""

    def test_checkbox_default_is_the_initial_state(self):
        parsed = parse_template("{% if B_admin | default(true) %}x{% endif %}")
        assert self._field(parsed, "B_admin").default is True


class TestTemplateReuse:
    """extends / include / import discovery -- see HANDOFF.md for the cases
    these reproduce; `resolve` here is a plain dict lookup standing in for
    template_library.read_template.
    """

    def _resolve(self, files):
        return files.get

    def test_extends_and_include_and_import_all_resolve(self):
        # HANDOFF.md finding 1.
        files = {
            "base.j2": "{% block body %}base{% endblock %}",
            "macros.j2": "{% macro port(p) %}{{ S_host }}:{{ p }}{% endmacro %}",
            "header.j2": "header for {{ S_host }}",
        }
        template = (
            '{% extends "base.j2" %}'
            '{% import "macros.j2" as m with context %}'
            '{% block body %}{% include "header.j2" %}{{ super() }}'
            "{{ m.port(N_port) }}{% endblock %}"
        )
        parsed = parse_template(template, resolve=self._resolve(files))
        assert {f.var_name for f in parsed.fields} == {"S_host", "N_port"}

    def test_overridden_base_block_without_super_drops_base_fields(self):
        files = {"base.j2": "{% block a %}{{ S_dead }}{% endblock %}"}
        template = '{% extends "base.j2" %}{% block a %}{{ S_live }}{% endblock %}'
        parsed = parse_template(template, resolve=self._resolve(files))
        assert {f.var_name for f in parsed.fields} == {"S_live"}

    def test_super_pulls_in_the_base_blocks_own_fields(self):
        files = {"base.j2": "{% block a %}{{ S_base }}{% endblock %}"}
        template = '{% extends "base.j2" %}{% block a %}{{ super() }}{{ S_child }}{% endblock %}'
        parsed = parse_template(template, resolve=self._resolve(files))
        assert {f.var_name for f in parsed.fields} == {"S_base", "S_child"}

    def test_child_block_absent_from_base_is_dead(self):
        files = {"base.j2": "{{ S_base_top }}"}
        template = '{% extends "base.j2" %}{% block nosuch %}{{ S_ghost }}{% endblock %}'
        parsed = parse_template(template, resolve=self._resolve(files))
        assert {f.var_name for f in parsed.fields} == {"S_base_top"}

    def test_import_without_context_hides_its_variables(self):
        files = {"m.j2": "{{ S_hidden }}"}
        template = '{% import "m.j2" as m %}{{ m }}{{ S_visible }}'
        parsed = parse_template(template, resolve=self._resolve(files))
        assert {f.var_name for f in parsed.fields} == {"S_visible"}

    def test_import_with_context_exposes_its_variables(self):
        files = {"m.j2": "{{ S_shown }}"}
        template = '{% import "m.j2" as m with context %}{{ m }}'
        parsed = parse_template(template, resolve=self._resolve(files))
        assert {f.var_name for f in parsed.fields} == {"S_shown"}

    def test_a_top_level_import_is_visible_inside_the_templates_own_block(self):
        # Jinja compiles a block as its own frame; without _declared_names
        # this would misread the import's target `m` as an undeclared field.
        files = {"base.j2": "{% block a %}{% endblock %}"}
        template = (
            '{% extends "base.j2" %}{% import "base.j2" as m %}'
            "{% block a %}{{ m }}{{ S_x }}{% endblock %}"
        )
        parsed = parse_template(template, resolve=self._resolve(files))
        assert {f.var_name for f in parsed.fields} == {"S_x"}

    def test_diamond_shaped_reuse_is_not_a_cycle(self):
        files = {
            "base.j2": '{% block body %}{% include "shared.j2" %}{% endblock %}',
            "shared.j2": "{{ S_shared }}",
        }
        template = (
            '{% extends "base.j2" %}{% include "shared.j2" %}'
            "{% block body %}{{ super() }}{% endblock %}"
        )
        parsed = parse_template(template, resolve=self._resolve(files))
        assert {f.var_name for f in parsed.fields} == {"S_shared"}

    def test_dynamic_include_name_is_refused(self):
        with pytest.raises(TemplateValidationError, match="dynamic"):
            parse_template("{% include S_choice %}", resolve=self._resolve({}))

    def test_extends_cycle_is_refused(self):
        files = {"a.j2": '{% extends "b.j2" %}', "b.j2": '{% extends "a.j2" %}'}
        with pytest.raises(TemplateValidationError, match="cycle"):
            parse_template('{% extends "a.j2" %}', resolve=self._resolve(files))

    def test_missing_referenced_template_is_refused(self):
        with pytest.raises(TemplateValidationError, match="nope.j2"):
            parse_template('{% include "nope.j2" %}', resolve=self._resolve({}))

    def test_no_resolver_refuses_any_reference(self):
        with pytest.raises(TemplateValidationError):
            parse_template('{% include "anything.j2" %}')

    def test_too_deep_a_chain_is_refused(self):
        files = {}
        for i in range(15):
            nxt = f'{{% include "d{i + 1}.j2" %}}' if i < 14 else "{{ S_deep }}"
            files[f"d{i}.j2"] = nxt
        with pytest.raises(TemplateValidationError, match="deep"):
            parse_template('{% include "d0.j2" %}', resolve=self._resolve(files))

    def test_too_many_referenced_templates_is_refused(self):
        files = {f"t{i}.j2": "x" for i in range(60)}
        template = "".join(f'{{% include "t{i}.j2" %}}' for i in range(60))
        with pytest.raises(TemplateValidationError, match="More than 50"):
            parse_template(template, resolve=self._resolve(files))

    def test_a_referenced_templates_syntax_error_is_refused(self):
        files = {"bad.j2": "{% if %}"}
        with pytest.raises(TemplateValidationError, match="syntax error"):
            parse_template('{% include "bad.j2" %}', resolve=self._resolve(files))


class TestRadioDefaults:
    def test_truthy_default_preselects_that_option(self):
        template = "{{ R_color_red }}{{ R_color_blue | default(true) }}{{ R_color_green }}"
        parsed = parse_template(template)
        assert parsed.radio_groups[0].default == "blue"

    def test_falsy_default_selects_nothing(self):
        # `default(false)` says what the option is worth when missing, not
        # which option the group should start on.
        template = "{{ R_color_red }}{{ R_color_blue | default(false) }}"
        assert parse_template(template).radio_groups[0].default is None

    def test_no_default_selects_nothing(self):
        parsed = parse_template("{{ R_color_red }}{{ R_color_blue }}")
        assert parsed.radio_groups[0].default is None

    def test_earliest_truthy_option_wins(self):
        template = "{{ R_color_red | default(true) }}{{ R_color_blue | default(true) }}"
        assert parse_template(template).radio_groups[0].default == "red"

    def test_group_options_are_unaffected_by_the_default(self):
        template = "{{ R_color_red }}{{ R_color_blue | default(true) }}"
        parsed = parse_template(template)
        assert parsed.radio_groups[0].options == [("red", "Red"), ("blue", "Blue")]
