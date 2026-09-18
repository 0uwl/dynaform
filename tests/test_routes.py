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
        assert b"Choose a template, paste template text, or upload a file." in resp.data

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


class TestTemplateLibrary:
    def test_index_has_no_picker_when_no_directory_configured(self, client):
        html = client.get("/").data.decode()
        assert 'name="template_choice"' not in html

    def test_index_lists_directory_templates_in_a_select(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        html = client.get("/").data.decode()
        assert 'name="template_choice"' in html
        assert 'value="greeting.j2"' in html

    def test_empty_directory_shows_no_picker(self, client, library):
        html = client.get("/").data.decode()
        assert 'name="template_choice"' not in html

    def test_sources_come_before_the_editor_they_fill(self, client, library):
        # Both sources sit side by side at the top; the editor they fill is
        # last, right above the submit button.
        (library / "greeting.j2").write_text(TEMPLATE)
        html = client.get("/").data.decode()
        assert (
            html.index('name="template_choice"')
            < html.index('name="template_file"')
            < html.index('name="template_text"')
        )

    def test_load_button_fills_the_editor_without_js(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        resp = client.post(
            "/", data={"template_choice": "greeting.j2", "load": "Load into editor"}
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Still on the first page, with the file's text in the textarea.
        assert 'name="template_text"' in html
        assert "Hello {{ S_username }}" in html

    def test_loading_does_not_skip_ahead_to_the_form(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        resp = client.post(
            "/", data={"template_choice": "greeting.j2", "load": "Load into editor"}
        )
        assert b'name="S_username"' not in resp.data

    def test_load_without_a_choice_is_rejected(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        resp = client.post("/", data={"template_choice": "", "load": "Load into editor"})
        assert resp.status_code == 400
        assert b"Choose a template to load" in resp.data

    def test_unlisted_choice_is_rejected(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        resp = client.post(
            "/", data={"template_choice": "../../etc/passwd", "load": "Load into editor"}
        )
        assert resp.status_code == 400

    def test_template_deleted_between_load_and_submit_reports_cleanly(self, client, library):
        target = library / "greeting.j2"
        target.write_text(TEMPLATE)
        form = client.get("/")  # picker built while the file still exists
        assert b'value="greeting.j2"' in form.data
        # WTForms rejects a choice that has vanished from the directory.
        target.unlink()
        resp = client.post(
            "/", data={"template_choice": "greeting.j2", "load": "Load into editor"}
        )
        assert resp.status_code == 400

    def test_chosen_template_parses_when_the_editor_is_left_empty(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        resp = client.post(
            "/",
            data={
                "template_choice": "greeting.j2",
                "template_text": "",
                "submit": "Parse template",
            },
        )
        assert resp.status_code == 200
        assert b'name="S_username"' in resp.data

    def test_edited_text_wins_over_the_chosen_template(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        resp = client.post(
            "/",
            data={
                "template_choice": "greeting.j2",
                "template_text": "Edited {{ S_edited }}",
                "submit": "Parse template",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="S_edited"' in html
        assert 'name="S_username"' not in html

    def test_editing_a_loaded_template_never_touches_the_file(self, client, library):
        target = library / "greeting.j2"
        target.write_text(TEMPLATE)
        template_source = TEMPLATE.replace("Hello", "Edited")
        client.post(
            "/render",
            data={
                "template_source": template_source,
                "S_username": "Alice",
                "N_user_age": "30",
                "R_color": "red",
                "submit": "Render template",
            },
        )
        assert target.read_text() == TEMPLATE

    def test_endpoint_serves_a_template_as_plain_text(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        resp = client.get("/template-library?name=greeting.j2")
        assert resp.status_code == 200
        assert resp.mimetype == "text/plain"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.data.decode() == TEMPLATE

    def test_endpoint_rejects_unknown_and_traversal_names(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        for name in ["nope.j2", "../../etc/passwd", "/etc/passwd", ""]:
            assert client.get("/template-library", query_string={"name": name}).status_code == 404

    def test_endpoint_is_404_when_no_directory_configured(self, client):
        assert client.get("/template-library?name=greeting.j2").status_code == 404

    def test_index_loads_the_picker_script(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        assert b"js/template_sources.js" in client.get("/").data

    def test_load_button_ships_visible_for_no_js_use(self, client, library):
        (library / "greeting.j2").write_text(TEMPLATE)
        html = client.get("/").data.decode()
        assert re.search(r'id="load-template"(?![^>]*\bhidden\b)', html)


class TestUploadPreview:
    def test_editor_script_loads_without_a_template_directory(self, client):
        # The file preview is independent of the picker, so the script has to
        # be there even when no directory is configured.
        assert b"js/template_sources.js" in client.get("/").data

    def test_file_input_announces_what_it_accepts(self, client):
        # WTForms renders attributes in alphabetical order, so match the whole
        # tag rather than assuming accept follows name.
        html = client.get("/").data.decode()
        tag = re.search(r'<input[^>]*name="template_file"[^>]*>', html).group(0)
        assert 'accept=".j2,.txt"' in tag

    def test_file_input_carries_the_request_size_limit(self, app, client):
        html = client.get("/").data.decode()
        assert f'data-max-bytes="{app.config["MAX_CONTENT_LENGTH"]}"' in html

    def test_hint_and_status_targets_are_present(self, client):
        html = client.get("/").data.decode()
        assert 'id="template-file-hint"' in html
        assert 'id="template-file-status"' in html

    def test_page_describes_the_no_js_behaviour_by_default(self, client):
        # The served hint is the truth without scripting: the file is uploaded
        # and wins over the editor. The script rewrites it in the browser.
        html = client.get("/").data.decode()
        assert "An uploaded file takes precedence over the editor text." in html

    def test_uploading_still_works_without_js(self, client):
        # Unchanged server path: no script, so the file really is uploaded.
        data = {
            "template_file": (io.BytesIO(TEMPLATE.encode()), "greeting.j2"),
            "submit": "Parse template",
        }
        resp = client.post("/", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        assert b'name="S_username"' in resp.data

    def test_uploaded_file_still_beats_editor_text_without_js(self, client):
        data = {
            "template_file": (io.BytesIO(TEMPLATE.encode()), "greeting.j2"),
            "template_text": "Edited {{ S_edited }}",
            "submit": "Parse template",
        }
        resp = client.post("/", data=data, content_type="multipart/form-data")
        html = resp.data.decode()
        assert 'name="S_username"' in html
        assert 'name="S_edited"' not in html
