"""Unit tests for app/forms.py: dynamic WTForms construction."""
from wtforms import BooleanField, IntegerField, PasswordField, RadioField, StringField
from wtforms.validators import InputRequired, Optional

from app.forms import build_dynamic_form
from app.template_parser import parse_template


def _fields_of(form):
    return {name: field for name, field in form._fields.items()}


class TestFieldTypes:
    def test_prefix_maps_to_expected_wtforms_class(self, app):
        parsed = parse_template("{{ S_name }} {{ P_secret }} {{ N_age }} {{ B_flag }}")
        with app.test_request_context():
            form = build_dynamic_form(parsed)()
            fields = _fields_of(form)
            assert isinstance(fields["S_name"], StringField)
            assert isinstance(fields["P_secret"], PasswordField)
            assert isinstance(fields["N_age"], IntegerField)
            assert isinstance(fields["B_flag"], BooleanField)

    def test_field_label_comes_from_field_spec(self, app):
        parsed = parse_template("{{ N_user_age }}")
        with app.test_request_context():
            form = build_dynamic_form(parsed)()
            assert form.N_user_age.label.text == "User age"

    def test_radio_group_becomes_one_radio_field_with_choices(self, app):
        parsed = parse_template("{{ R_color_red }} {{ R_color_blue }}")
        with app.test_request_context():
            form = build_dynamic_form(parsed)()
            fields = _fields_of(form)
            assert isinstance(fields["R_color"], RadioField)
            assert form.R_color.choices == [("red", "Red"), ("blue", "Blue")]

    def test_every_dynamic_form_carries_hidden_template_source(self, app):
        parsed = parse_template("{{ S_name }}")
        with app.test_request_context():
            form = build_dynamic_form(parsed)()
            assert "template_source" in _fields_of(form)


class TestValidators:
    def test_top_level_text_field_is_required(self, app):
        parsed = parse_template("{{ S_name }}")
        with app.test_request_context():
            form = build_dynamic_form(parsed)()
            assert any(isinstance(v, InputRequired) for v in form.S_name.validators)

    def test_checkbox_has_no_required_validator(self, app):
        parsed = parse_template("{{ B_admin }}")
        with app.test_request_context():
            form = build_dynamic_form(parsed)()
            assert not any(isinstance(v, InputRequired) for v in form.B_admin.validators)

    def test_conditional_child_field_is_optional_not_required(self, app):
        parsed = parse_template("{% if B_admin %}{{ S_admin_name }}{% endif %}")
        with app.test_request_context():
            form = build_dynamic_form(parsed)()
            validators = form.S_admin_name.validators
            assert any(isinstance(v, Optional) for v in validators)
            assert not any(isinstance(v, InputRequired) for v in validators)

    def test_top_level_radio_group_is_required(self, app):
        parsed = parse_template("{{ R_color_red }} {{ R_color_blue }}")
        with app.test_request_context():
            form = build_dynamic_form(parsed)()
            assert any(isinstance(v, InputRequired) for v in form.R_color.validators)

    def test_conditional_radio_group_is_optional(self, app):
        template = "{{ B_admin }} {{ R_admin_light }} {{ R_admin_dark }}"
        parsed = parse_template(template)
        with app.test_request_context():
            form = build_dynamic_form(parsed)()
            assert any(isinstance(v, Optional) for v in form.R_admin.validators)
            assert not any(isinstance(v, InputRequired) for v in form.R_admin.validators)


class TestValidation:
    def test_valid_submission_passes(self, app):
        parsed = parse_template("{{ S_name }} {{ N_age }}")
        with app.test_request_context(
            method="POST",
            data={"S_name": "Alice", "N_age": "30", "template_source": "x"},
        ):
            form = build_dynamic_form(parsed)()
            assert form.validate() is True

    def test_missing_required_field_fails(self, app):
        parsed = parse_template("{{ S_name }} {{ N_age }}")
        with app.test_request_context(method="POST", data={"S_name": "Alice"}):
            form = build_dynamic_form(parsed)()
            assert form.validate() is False
            assert "N_age" in form.errors

    def test_missing_conditional_child_still_passes(self, app):
        parsed = parse_template("{% if B_admin %}{{ S_admin_name }}{% endif %}")
        with app.test_request_context(method="POST", data={"template_source": "x"}):
            form = build_dynamic_form(parsed)()
            assert form.validate() is True

    def test_non_numeric_value_rejected_for_number_field(self, app):
        parsed = parse_template("{{ N_age }}")
        with app.test_request_context(
            method="POST", data={"N_age": "not-a-number", "template_source": "x"}
        ):
            form = build_dynamic_form(parsed)()
            assert form.validate() is False
            assert "N_age" in form.errors


class TestLists:
    TEMPLATE = (
        "{% for loc in L_locations %}"
        "{{ loc.S_path }} {{ loc.N_port | default(80) }} {{ loc.B_ssl }}"
        "{% endfor %}"
    )

    def _form(self, app, data):
        from werkzeug.datastructures import MultiDict
        form_cls = build_dynamic_form(parse_template(self.TEMPLATE))
        with app.test_request_context(method="POST", data=MultiDict(data)):
            form = form_cls()
            return form, form.validate()

    def test_rows_validate_with_the_same_rules_as_top_level_fields(self, app):
        form, ok = self._form(app, {
            "template_source": "x",
            "L_locations-0-S_path": "/", "L_locations-0-N_port": "",
            "L_locations-3-S_path": "/api", "L_locations-3-N_port": "9000",
        })
        assert ok, form.errors
        assert form.L_locations.data == [
            {"S_path": "/", "N_port": None, "B_ssl": False, "present": "1"},
            {"S_path": "/api", "N_port": 9000, "B_ssl": False, "present": "1"},
        ]

    def test_conditional_row_field_is_optional(self, app):
        form_cls = build_dynamic_form(parse_template(
            "{% for i in L_ports %}{{ i.B_access }}{{ i.N_access_vlan }}{% endfor %}"
        ))
        row_field = form_cls.row_forms["L_ports"].N_access_vlan
        assert row_field.kwargs["validators"][0].__class__ is Optional

    def test_missing_required_row_field_fails(self, app):
        form, ok = self._form(app, {"template_source": "x", "L_locations-0-N_port": "1"})
        assert not ok
        assert "S_path" in form.L_locations.entries[0].errors

    def test_zero_rows_is_a_valid_empty_list(self, app):
        form, ok = self._form(app, {"template_source": "x"})
        assert ok and form.L_locations.data == []

    def test_row_count_is_capped_with_a_visible_error(self, app):
        from app.forms import MAX_LIST_ROWS
        data = {f"L_locations-{i}-S_path": "/" for i in range(MAX_LIST_ROWS + 5)}
        form, ok = self._form(app, {"template_source": "x", **data})
        assert not ok
        assert f"At most {MAX_LIST_ROWS} rows." in form.L_locations.errors

    def test_blank_row_carries_the_placeholder_index(self, app):
        form_cls = build_dynamic_form(parse_template(self.TEMPLATE))
        with app.test_request_context():
            row = form_cls(formdata=None).row_forms["L_locations"](prefix="L_locations-__INDEX__-")
            assert row.S_path.name == "L_locations-__INDEX__-S_path"
            assert row.N_port.data == 80
