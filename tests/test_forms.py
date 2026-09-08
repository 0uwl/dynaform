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
