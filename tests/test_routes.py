"""Integration tests for the HTTP routes: parse -> dynamic form -> render."""
import io
import re

TEMPLATE = """\
Hello {{ S_username }}, age {{ N_user_age }}.
{% if B_admin %}
Admin: {{ S_admin_name }} / {{ P_admin_pass }}
{% endif %}
Color: {{ R_color_red }}{{ R_color_blue }}{{ R_color_green }}
"""


def _extract(html, name):
    match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', html)
    return match.group(1) if match else None


class TestIndex:
    def test_get_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"DynaForm" in resp.data


class TestParse:
    def test_pasted_text_builds_dynamic_form(self, client):
        resp = client.post(
            "/", data={"template_text": TEMPLATE, "submit": "Parse template"}
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="S_username"' in html
        assert 'name="N_user_age"' in html
        assert 'name="B_admin"' in html
        assert 'name="R_color"' in html

    def test_uploaded_file_builds_dynamic_form(self, client):
        data = {
            "template_file": (io.BytesIO(TEMPLATE.encode()), "greeting.j2"),
            "submit": "Parse template",
        }
        resp = client.post("/", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        assert b'name="S_username"' in resp.data

    def test_empty_submission_rejected(self, client):
        resp = client.post("/", data={"submit": "Parse template"})
        assert resp.status_code == 400
        assert b"Provide a template" in resp.data

    def test_template_with_no_variables_rejected(self, client):
        resp = client.post(
            "/", data={"template_text": "just static text", "submit": "Parse template"}
        )
        assert resp.status_code == 400
        assert b"no DynaForm variables" in resp.data

    def test_invalid_variable_name_rejected(self, client):
        resp = client.post(
            "/", data={"template_text": "Hello {{ username }}", "submit": "Parse template"}
        )
        assert resp.status_code == 400
        assert b"Unrecognized variable" in resp.data

    def test_syntax_error_rejected(self, client):
        resp = client.post(
            "/", data={"template_text": "{% if %}", "submit": "Parse template"}
        )
        assert resp.status_code == 400
        assert b"syntax error" in resp.data.lower()

    def test_conditional_field_wrapper_marked_for_js_toggle(self, client):
        resp = client.post(
            "/", data={"template_text": TEMPLATE, "submit": "Parse template"}
        )
        assert b'data-parent="admin"' in resp.data

    def test_wrong_file_extension_rejected(self, client):
        data = {
            "template_file": (io.BytesIO(b"{{ S_x }}"), "greeting.exe"),
            "submit": "Parse template",
        }
        resp = client.post("/", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400


class TestRender:
    def _parse(self, client, template=TEMPLATE):
        resp = client.post("/", data={"template_text": template, "submit": "Parse template"})
        html = resp.data.decode()
        return _extract(html, "template_source")

    def test_full_round_trip_renders_expected_output(self, client):
        template_source = self._parse(client)
        resp = client.post(
            "/render",
            data={
                "template_source": template_source,
                "S_username": "Alice",
                "N_user_age": "34",
                "B_admin": "y",
                "S_admin_name": "root",
                "P_admin_pass": "hunter2",
                "R_color": "blue",
                "submit": "Render template",
            },
        )
        assert resp.status_code == 200
        out = resp.data.decode()
        assert "Hello Alice, age 34." in out
        assert "Admin: root / hunter2" in out
        assert "Color: FalseTrueFalse" in out

    def test_unchecked_checkbox_hides_conditional_output(self, client):
        template_source = self._parse(client)
        resp = client.post(
            "/render",
            data={
                "template_source": template_source,
                "S_username": "Bob",
                "N_user_age": "20",
                "R_color": "red",
                "submit": "Render template",
            },
        )
        assert resp.status_code == 200
        out = resp.data.decode()
        assert "Admin:" not in out

    def test_missing_required_field_redisplays_form_with_errors(self, client):
        template_source = self._parse(client)
        resp = client.post(
            "/render",
            data={
                "template_source": template_source,
                "R_color": "red",
                "submit": "Render template",
            },
        )
        assert resp.status_code == 400
        assert b"invalid-feedback" in resp.data

    def test_missing_template_source_redirects_to_index(self, client):
        resp = client.post("/render", data={"submit": "Render template"})
        assert resp.status_code == 400
        assert b"Session expired" in resp.data

    def test_tampered_template_source_revalidated(self, client):
        # /render re-parses+validates the hidden template text itself, so a
        # tampered value with an invalid variable name is still rejected.
        resp = client.post(
            "/render",
            data={"template_source": "{{ not_a_valid_name }}", "submit": "Render template"},
        )
        assert resp.status_code == 400
        assert b"Unrecognized variable" in resp.data

    def test_password_values_never_appear_in_logs(self, client, caplog):
        template_source = self._parse(client, template="{{ P_password }}")
        with caplog.at_level("INFO"):
            client.post(
                "/render",
                data={
                    "template_source": template_source,
                    "P_password": "super-secret-value",
                    "submit": "Render template",
                },
            )
        assert "super-secret-value" not in caplog.text


class TestOutputActions:
    def _render(self, client):
        resp = client.post("/", data={"template_text": TEMPLATE, "submit": "Parse template"})
        template_source = _extract(resp.data.decode(), "template_source")
        return client.post(
            "/render",
            data={
                "template_source": template_source,
                "S_username": "Alice",
                "N_user_age": "34",
                "R_color": "blue",
                "submit": "Render template",
            },
        )

    def test_result_page_offers_copy_and_download(self, client):
        html = self._render(client).data.decode()
        assert 'id="copy-output"' in html
        assert 'id="download-output"' in html
        assert "js/output_actions.js" in html

    def test_output_carries_id_the_actions_read_from(self, client):
        html = self._render(client).data.decode()
        assert 'id="output"' in html

    def test_actions_hidden_until_script_reveals_them(self, client):
        # Buttons ship hidden so they are never dead controls without JS.
        html = self._render(client).data.decode()
        assert re.search(r'id="output-actions"[^>]*hidden', html)

    def test_output_is_escaped_in_the_page(self, client):
        # The copy/download buttons read textContent, so the escaped markup
        # here round-trips back to the original text in the browser.
        resp = client.post("/", data={"template_text": "{{ S_x }}", "submit": "Parse template"})
        template_source = _extract(resp.data.decode(), "template_source")
        resp = client.post(
            "/render",
            data={
                "template_source": template_source,
                "S_x": "<script>alert(1)</script>",
                "submit": "Render template",
            },
        )
        html = resp.data.decode()
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


class TestRenderSandboxing:
    def test_sandbox_blocks_attribute_access_ssti(self, client):
        template_source = "{{ S_x.__class__.__init__.__globals__ }}"
        resp = client.post(
            "/render",
            data={
                "template_source": template_source,
                "S_x": "hi",
                "submit": "Render template",
            },
        )
        assert resp.status_code == 400
        assert b"Rendering failed" in resp.data
