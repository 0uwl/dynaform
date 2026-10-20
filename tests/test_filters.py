"""Metadata filters (label/help/placeholder/required/optional/group): parse -> form -> page."""
import pytest
from wtforms.validators import InputRequired, Optional

from app.forms import build_dynamic_form
from app.template_parser import TemplateValidationError, parse_template


def _field(source, name):
    return next(f for f in parse_template(source).fields if f.var_name == name)


class TestParsing:
    def test_text_filters_are_read(self):
        f = _field('{{ S_ip | label("IP address") | help("v4 only") | placeholder("10.0.0.1") }}', "S_ip")
        assert (f.label, f.help, f.placeholder) == ("IP address", "v4 only", "10.0.0.1")

    def test_default_is_still_found_behind_other_filters(self):
        f = _field('{{ S_x | label("X") | default("a") }}', "S_x")
        assert f.has_default and f.default == "a"

    def test_first_occurrence_wins(self):
        f = _field('{{ S_x | label("One") }} {{ S_x | label("Two") }}', "S_x")
        assert f.label == "One"

    @pytest.mark.parametrize("source", [
        "{{ S_x | label(S_y) }}",
        '{{ S_x | label("a", "b") }}',
        '{{ S_x | label(text="a") }}',
        '{{ S_x | label("") }}',
        '{{ S_x | required("yes") }}',
        '{{ S_x | required | optional }}',
        '{{ B_x | required }}',
        '{{ B_x | placeholder("p") }}',
        '{% for r in L_rows %}{{ r.S_a | group("G") }}{% endfor %}',
    ])
    def test_misuse_is_refused(self, source):
        with pytest.raises(TemplateValidationError):
            parse_template(source)

    def test_unknown_filter_is_a_validation_error_not_a_crash(self):
        with pytest.raises(TemplateValidationError):
            parse_template("{{ S_x | nosuchfilter }}")

    def test_row_field_metadata(self):
        parsed = parse_template(
            '{% for r in L_rows %}{{ r.S_a | label("Path") | default("/") }}{% endfor %}'
        )
        f = parsed.lists[0].fields[0]
        assert (f.label, f.default, f.has_default) == ("Path", "/", True)

    def test_radio_option_label_and_group(self):
        parsed = parse_template(
            '{{ R_c_red | label("Crimson") }}{{ R_c_blue | group("Look") }}'
        )
        g = parsed.radio_groups[0]
        assert g.options == [("red", "Crimson"), ("blue", "Blue")]
        assert g.group == "Look"

    def test_list_group_on_iterable(self):
        parsed = parse_template(
            '{% for r in L_rows | group("Data") %}{{ r.S_a }}{% endfor %}'
        )
        assert parsed.lists[0].group == "Data"

    def test_other_filters_on_a_list_iterable_still_refused(self):
        with pytest.raises(TemplateValidationError):
            parse_template("{% for r in L_rows | sort %}{{ r.S_a }}{% endfor %}")

    def test_conditional_child_inherits_the_checkbox_group(self):
        parsed = parse_template('{{ B_admin | group("Admin") }}{{ S_admin_name }}{{ S_other }}')
        groups = {f.var_name: f.group for f in parsed.fields}
        assert groups == {"B_admin": "Admin", "S_admin_name": "Admin", "S_other": None}


class TestForm:
    def _validators(self, app, source, name):
        with app.test_request_context():
            return getattr(build_dynamic_form(parse_template(source))(), name).validators

    def test_required_beats_default(self, app):
        v = self._validators(app, '{{ S_x | required | default("a") }}', "S_x")
        assert isinstance(v[0], InputRequired)

    def test_optional_without_default(self, app):
        v = self._validators(app, "{{ S_x | optional }}", "S_x")
        assert isinstance(v[0], Optional)

    def test_placeholder_reaches_the_input(self, app):
        with app.test_request_context():
            form = build_dynamic_form(parse_template('{{ S_x | placeholder("hi") }}'))()
            assert 'placeholder="hi"' in form.S_x()


SOURCE = (
    '{{ S_host | group("Server") }}\n{{ S_note | help("<b>hint</b>") }}\n'
    '{{ N_port | group("Server") }}\n{{ S_user | group("Login") }}\n'
)


class TestPage:
    def _page(self, client, source=SOURCE):
        return client.post("/", data={"template_text": source, "submit": "Parse template"}).data.decode()

    def test_group_members_gather_in_one_fieldset_at_first_member(self, client):
        page = self._page(client)
        assert page.count("<legend class=\"float-none w-auto px-2 fs-6\">Server</legend>") == 1
        server, login = page.index(">Server</legend>"), page.index(">Login</legend>")
        assert server < page.index('name="S_host"') < page.index('name="N_port"') < page.index('name="S_note"')
        assert page.index('name="N_port"') < login < page.index('name="S_user"')

    def test_help_text_is_escaped(self, client):
        page = self._page(client)
        assert "&lt;b&gt;hint&lt;/b&gt;" in page and "<b>hint</b>" not in page

    def test_filters_do_not_change_the_render(self, client):
        import html
        import re
        source = '{{ S_x | label("X") | default("fallback") | group("G") }}'
        page = self._page(client, source)
        src = html.unescape(re.search(r'name="template_source"[^>]*value="([^"]*)"', page).group(1))
        resp = client.post("/render", data={"template_source": src, "S_x": ""})
        assert resp.status_code == 200 and b"fallback" in resp.data
