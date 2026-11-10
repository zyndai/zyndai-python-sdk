"""
Tests for the structured payload format: AgentPayload schema, Attachment
model, in-memory attachment resolution, and the advertised agent.json. Also
covers backward compatibility — plain string payloads without attachments
continue to parse exactly as before.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field, ValidationError

from zyndai_agent import AgentMessage, AgentPayload, Attachment
from zyndai_agent.payload import build_payload_card_fields
from zyndai_agent.webhook_communication import WebhookCommunicationManager


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager():
    """Manager with the background server start mocked out."""
    with patch.object(WebhookCommunicationManager, "start_webhook_server"):
        mgr = WebhookCommunicationManager(
            entity_id="payload-test",
            webhook_host="127.0.0.1",
            webhook_port=16000,
            webhook_url="http://127.0.0.1:16000/webhook",
            identity_credential=None,
            keypair=None,
            price=None,
            pay_to_address=None,
            max_file_size_bytes=1024 * 1024,  # 1 MB
        )
        mgr.is_running = True
        yield mgr


@pytest.fixture
def client(manager):
    manager.flask_app.config["TESTING"] = True
    return manager.flask_app.test_client()


# ---------------------------------------------------------------------------
# Backward compatibility — legacy payloads must still parse unchanged
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_plain_content_payload(self):
        msg = AgentMessage.from_dict({"content": "hi"})
        assert msg.content == "hi"
        assert msg.attachments == []
        assert msg.message_type == "query"

    def test_prompt_alias_still_maps_to_content(self):
        msg = AgentMessage.from_dict({"prompt": "legacy"})
        assert msg.content == "legacy"

    def test_prompt_takes_precedence_over_content(self):
        msg = AgentMessage.from_dict({"prompt": "P", "content": "C"})
        assert msg.content == "P"

    def test_empty_dict_still_builds_a_default_message(self):
        msg = AgentMessage.from_dict({})
        assert msg.content == ""
        assert msg.sender_id == "unknown"
        assert msg.attachments == []

    def test_unknown_fields_do_not_raise(self):
        # Unknown keys must be tolerated so old clients can evolve without
        # the agent rejecting their requests.
        msg = AgentMessage.from_dict({
            "content": "hi",
            "sender_id": "u",
            "custom_field_added_by_caller": 42,
        })
        assert msg.content == "hi"

    def test_roundtrip_dict_without_attachments(self):
        original = AgentMessage.from_dict({"content": "abc", "sender_id": "u"})
        restored = AgentMessage.from_dict(original.to_dict())
        assert restored.content == "abc"
        assert restored.sender_id == "u"


# ---------------------------------------------------------------------------
# AgentPayload default schema
# ---------------------------------------------------------------------------


class TestAgentPayloadSchema:
    def test_full_model_still_includes_internal_fields(self):
        # The internal-routing fields stay on the Pydantic model so parsing
        # and serialization keep working; they're only hidden from the
        # advertised card schema.
        schema = AgentPayload.model_json_schema()
        props = set(schema["properties"].keys())
        assert {
            "content", "sender_id", "message_type", "message_id", "metadata",
        }.issubset(props)

    def test_advertised_schema_hides_internal_fields(self):
        # The caller-facing schema on /.well-known/agent.json should only
        # surface things a caller actually fills in.
        fields = build_payload_card_fields(AgentPayload)
        props = set(fields["input_schema"]["properties"].keys())
        assert "sender_id" not in props
        assert "message_id" not in props
        assert "conversation_id" not in props
        assert "in_reply_to" not in props
        assert "message_type" not in props
        # Default payload is attachments-free; caller-facing surface is
        # content + metadata only.
        assert props == {"content", "metadata"}

    def test_default_schema_has_no_attachment_defs(self):
        # Agents that don't declare attachments shouldn't advertise the
        # Attachment type at all.
        fields = build_payload_card_fields(AgentPayload)
        assert "$defs" not in fields["input_schema"]
        assert fields["accepts_files"] is False

    def test_default_schema_has_no_required_fields(self):
        # Default must be permissive — any caller can send anything.
        schema = AgentPayload.model_json_schema()
        assert schema.get("required", []) == []

    def test_custom_subclass_required_fields_surface_in_schema(self):
        class Signup(AgentPayload):
            name: str
            age: int

        schema = Signup.model_json_schema()
        assert set(schema["required"]) == {"name", "age"}

    def test_custom_subclass_validates_incoming_payload(self):
        class Signup(AgentPayload):
            name: str
            age: int

        with pytest.raises(ValidationError):
            AgentMessage.from_dict({"content": "hi"}, payload_model=Signup)

        msg = AgentMessage.from_dict(
            {"content": "ok", "name": "Ada", "age": 36},
            payload_model=Signup,
        )
        assert msg.content == "ok"


# ---------------------------------------------------------------------------
# Attachment model — inline base64, validation, fetch helpers
# ---------------------------------------------------------------------------


class TestAttachmentModel:
    def test_inline_base64_decodes_back_to_original_bytes(self):
        payload = b"\x00\x01\x02binary contents"
        att = Attachment(
            filename="f.bin",
            mime_type="application/octet-stream",
            data=base64.b64encode(payload).decode(),
        )
        assert att.decode_data() == payload

    def test_url_only_attachment_parses(self):
        att = Attachment(filename="vid.mp4", url="https://example.com/v.mp4")
        assert att.url == "https://example.com/v.mp4"
        assert att.data is None

    def test_rejects_attachment_with_no_source(self):
        with pytest.raises(ValidationError):
            Attachment(filename="x")

    def test_rejects_attachment_with_multiple_sources(self):
        with pytest.raises(ValidationError):
            Attachment(filename="x", data="aGk=", url="http://x")

    def test_decode_data_on_non_inline_attachment_errors(self):
        att = Attachment(filename="x", url="http://example.com/x")
        with pytest.raises(ValueError):
            att.decode_data()

    def test_invalid_base64_surfaces_clear_error(self):
        att = Attachment(filename="x", data="!!!not-valid-base64!!!")
        with pytest.raises(ValueError):
            att.decode_data()

    def test_fetch_url_rejects_disallowed_scheme(self):
        att = Attachment(filename="pwd", url="file:///etc/passwd")
        with pytest.raises(ValueError, match="scheme"):
            att.fetch_url()


# ---------------------------------------------------------------------------
# Attachments flow through AgentMessage
# ---------------------------------------------------------------------------


class _PayloadWithAttachments(AgentPayload):
    attachments: list[Attachment] = Field(default_factory=list)


class TestMessageAttachments:
    def test_inline_attachment_roundtrips_through_from_dict(self):
        raw = b"report content"
        encoded = base64.b64encode(raw).decode()
        msg = AgentMessage.from_dict(
            {
                "content": "see attached",
                "sender_id": "u",
                "attachments": [
                    {"filename": "r.pdf", "mime_type": "application/pdf", "data": encoded}
                ],
            },
            payload_model=_PayloadWithAttachments,
        )
        assert len(msg.attachments) == 1
        assert msg.attachments[0].decode_data() == raw

    def test_dict_roundtrip_preserves_attachments(self):
        raw = b"x"
        msg = AgentMessage.from_dict(
            {
                "sender_id": "u",
                "attachments": [{"filename": "a", "data": base64.b64encode(raw).decode()}],
            },
            payload_model=_PayloadWithAttachments,
        )
        restored = AgentMessage.from_dict(
            msg.to_dict(), payload_model=_PayloadWithAttachments
        )
        assert restored.attachments[0].decode_data() == raw

    def test_default_payload_silently_drops_attachments(self):
        # An agent that hasn't declared attachments shouldn't surface them
        # even if a caller sends the field; it falls into extras and
        # AgentMessage.attachments stays empty.
        msg = AgentMessage.from_dict(
            {"content": "hi", "attachments": [{"filename": "x", "data": "aGk="}]}
        )
        assert msg.attachments == []


# ---------------------------------------------------------------------------
# Attachment resolution: data + url, both handled in memory
# ---------------------------------------------------------------------------


@pytest.fixture
def http_server():
    """Lightweight local HTTP server for URL-attachment tests."""
    payload = b"url-fetched bytes"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/file", payload
    srv.shutdown()


class TestAttachmentResolution:
    def test_resolves_inline_data(self, manager):
        raw = b"inline bytes"
        att = Attachment(
            filename="x", data=base64.b64encode(raw).decode()
        )
        assert manager.resolve_attachment(att) == raw

    def test_resolves_http_url(self, manager, http_server):
        url, expected = http_server
        att = Attachment(filename="from-url", url=url)
        assert manager.resolve_attachment(att) == expected


# ---------------------------------------------------------------------------
# Multipart /webhook — raw binary file parts + JSON `payload` part
# ---------------------------------------------------------------------------


class _PayloadWithPdfs(AgentPayload):
    pdfs: list[Attachment] = Field(default_factory=list)


class TestValidationErrorResponses:
    """A bad payload should reach the caller as a structured 422, never a 500.
    The developer's handler should not be invoked at all on invalid input."""

    class _StrictPayload(AgentPayload):
        name: str
        age: int

    def _mgr(self):
        with patch.object(WebhookCommunicationManager, "start_webhook_server"):
            mgr = WebhookCommunicationManager(
                entity_id="strict-test",
                webhook_host="127.0.0.1",
                webhook_port=16200,
                webhook_url="http://127.0.0.1:16200/webhook",
                identity_credential=None,
                keypair=None,
                price=None,
                pay_to_address=None,
            )
            mgr.payload_model = self._StrictPayload
            mgr.is_running = True
            return mgr

    def test_missing_required_field_returns_422(self):
        mgr = self._mgr()
        handler_calls = []
        mgr.add_message_handler(lambda msg, topic: handler_calls.append(msg))
        client = mgr.flask_app.test_client()

        resp = client.post("/webhook", json={"name": "Alice"})  # missing age
        assert resp.status_code == 422
        body = resp.get_json()
        assert body["error"] == "validation_failed"
        assert any(err["loc"] == ["age"] for err in body["details"])
        assert handler_calls == []  # handler never invoked on invalid data

    def test_wrong_type_returns_422(self):
        mgr = self._mgr()
        client = mgr.flask_app.test_client()
        resp = client.post("/webhook", json={"name": "Alice", "age": "not-an-int"})
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "validation_failed"

    def test_unsupported_content_type_returns_400(self):
        mgr = self._mgr()
        client = mgr.flask_app.test_client()
        resp = client.post("/webhook", data="raw", content_type="text/plain")
        assert resp.status_code == 400

    def test_valid_payload_still_reaches_handler(self):
        mgr = self._mgr()
        called = {}

        def h(msg, topic):
            called["msg"] = msg
            mgr.set_response(msg.message_id, "ok")

        mgr.add_message_handler(h)
        client = mgr.flask_app.test_client()
        resp = client.post("/webhook/sync", json={"name": "Alice", "age": 30})
        assert resp.status_code == 200
        assert called["msg"].payload.name == "Alice"
        assert called["msg"].payload.age == 30


class TestMultipartWebhook:
    def _configured_manager(self, payload_model):
        """Manager wired up to validate against the given payload model."""
        with patch.object(WebhookCommunicationManager, "start_webhook_server"):
            mgr = WebhookCommunicationManager(
                entity_id="mp-test",
                webhook_host="127.0.0.1",
                webhook_port=16100,
                webhook_url="http://127.0.0.1:16100/webhook",
                identity_credential=None,
                keypair=None,
                price=None,
                pay_to_address=None,
                max_file_size_bytes=2 * 1024 * 1024,
            )
            mgr.payload_model = payload_model
            mgr.is_running = True
            return mgr

    def test_single_multipart_file_goes_through_webhook(self):
        import io
        raw = b"%PDF-1.4 fake pdf content"
        captured = {}

        mgr = self._configured_manager(_PayloadWithPdfs)

        def handler(msg, topic):
            captured["msg"] = msg
            mgr.set_response(msg.message_id, "ok")

        mgr.add_message_handler(handler)
        client = mgr.flask_app.test_client()

        resp = client.post(
            "/webhook/sync",
            data={
                "payload": '{"content":"please check","sender_id":"u"}',
                "pdfs": (io.BytesIO(raw), "doc.pdf", "application/pdf"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        msg = captured["msg"]
        assert msg.content == "please check"
        # Custom attachment fields live on msg.payload.<field_name>.
        assert len(msg.payload.pdfs) == 1
        assert msg.payload.pdfs[0].filename == "doc.pdf"
        assert msg.payload.pdfs[0].mime_type == "application/pdf"
        assert msg.payload.pdfs[0].decode_data() == raw

    def test_multiple_files_same_field_append_to_list(self):
        import io
        mgr = self._configured_manager(_PayloadWithPdfs)
        captured = {}

        def handler(msg, topic):
            captured["msg"] = msg
            mgr.set_response(msg.message_id, "ok")

        mgr.add_message_handler(handler)
        client = mgr.flask_app.test_client()

        resp = client.post(
            "/webhook/sync",
            data={
                "payload": "{}",
                "pdfs": [
                    (io.BytesIO(b"%PDF-1 a"), "a.pdf", "application/pdf"),
                    (io.BytesIO(b"%PDF-1 b"), "b.pdf", "application/pdf"),
                ],
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        atts = captured["msg"].payload.pdfs
        assert [a.filename for a in atts] == ["a.pdf", "b.pdf"]
        assert atts[0].decode_data() == b"%PDF-1 a"
        assert atts[1].decode_data() == b"%PDF-1 b"

    def test_multipart_without_payload_part_still_works(self):
        """Caller can send only file parts; the agent defaults typed fields."""
        import io
        mgr = self._configured_manager(_PayloadWithPdfs)
        captured = {}

        def handler(msg, topic):
            captured["msg"] = msg
            mgr.set_response(msg.message_id, "ok")

        mgr.add_message_handler(handler)
        client = mgr.flask_app.test_client()

        resp = client.post(
            "/webhook/sync",
            data={"pdfs": (io.BytesIO(b"just bytes"), "x.pdf", "application/pdf")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        assert captured["msg"].content == ""  # defaulted
        assert captured["msg"].payload.pdfs[0].decode_data() == b"just bytes"

    def test_unsupported_content_type_returns_400(self, client):
        resp = client.post(
            "/webhook",
            data="raw text body",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    def test_agent_json_advertises_multipart_when_accepts_files(self):
        fields = build_payload_card_fields(_PayloadWithPdfs)
        assert fields["accepts_files"] is True
        assert fields["accepts_multipart"] is True

    def test_agent_json_omits_multipart_when_no_attachments(self):
        fields = build_payload_card_fields(AgentPayload)
        assert fields["accepts_files"] is False
        assert "accepts_multipart" not in fields


# ---------------------------------------------------------------------------
# /.well-known/agent.json advertisement reflects payload schema
# ---------------------------------------------------------------------------


class TestOutputSchemaValidation:
    """The SDK validates handler responses against `output_model` when
    declared, serializes Pydantic instances cleanly, and converts validation
    failures into a structured error response."""

    class _Out(BaseModel):
        ok: bool
        message: str

    def _mgr(self, output_model=None):
        with patch.object(WebhookCommunicationManager, "start_webhook_server"):
            mgr = WebhookCommunicationManager(
                entity_id="out-test",
                webhook_host="127.0.0.1",
                webhook_port=16300,
                webhook_url="http://127.0.0.1:16300/webhook",
                identity_credential=None, keypair=None,
                price=None, pay_to_address=None,
            )
            mgr.output_model = output_model
            mgr.is_running = True
            return mgr

    def test_legacy_string_response_passes_through(self):
        mgr = self._mgr(output_model=self._Out)
        mgr.set_response("m1", "plain string")
        assert mgr.pending_responses["m1"] == "plain string"

    def test_dict_response_validated_and_serialized(self):
        mgr = self._mgr(output_model=self._Out)
        mgr.set_response("m2", {"ok": True, "message": "done"})
        body = json.loads(mgr.pending_responses["m2"])
        assert body == {"ok": True, "message": "done"}

    def test_invalid_dict_response_becomes_error(self):
        mgr = self._mgr(output_model=self._Out)
        mgr.set_response("m3", {"ok": "not-a-bool"})  # wrong type + missing message
        body = json.loads(mgr.pending_responses["m3"])
        assert body["error"] == "handler_output_invalid"
        assert len(body["details"]) >= 1

    def test_pydantic_instance_response_serialized(self):
        mgr = self._mgr(output_model=self._Out)
        mgr.set_response("m4", self._Out(ok=True, message="yay"))
        body = json.loads(mgr.pending_responses["m4"])
        assert body == {"ok": True, "message": "yay"}

    def test_dict_without_output_model_still_json_dumps(self):
        mgr = self._mgr(output_model=None)
        mgr.set_response("m5", {"anything": 1})
        assert json.loads(mgr.pending_responses["m5"]) == {"anything": 1}


class TestAgentJsonAdvertisement:
    def test_default_model_does_not_advertise_files(self):
        # Attachments are opt-in — agents that don't declare any
        # Attachment-typed field advertise accepts_files: false.
        fields = build_payload_card_fields(AgentPayload)
        assert fields["accepts_files"] is False

    def test_developer_edits_flow_into_card_fields(self):
        class Signup(AgentPayload):
            name: str
            email: str

        fields = build_payload_card_fields(Signup)
        props = fields["input_schema"]["properties"]
        assert "name" in props and "email" in props
        assert set(fields["input_schema"]["required"]) == {"name", "email"}

    def test_mime_whitelist_propagates_from_field_metadata(self):
        class Resume(AgentPayload):
            resume: list[Attachment] = Field(
                default_factory=list,
                json_schema_extra={"accepted_mime_types": ["application/pdf"]},
            )

        fields = build_payload_card_fields(Resume)
        assert fields["accepts_files"] is True
        assert fields["accepted_mime_types"] == ["application/pdf"]

    def test_output_schema_advertised_when_output_model_set(self):
        class Req(AgentPayload):
            pass
        class Res(BaseModel):
            status: str
            count: int

        fields = build_payload_card_fields(Req, output_model=Res)
        assert "output_schema" in fields
        assert set(fields["output_schema"]["properties"].keys()) == {"status", "count"}
        assert set(fields["output_schema"].get("required", [])) == {"status", "count"}

    def test_output_schema_absent_when_no_output_model(self):
        fields = build_payload_card_fields(AgentPayload)
        assert "output_schema" not in fields

    def test_mime_types_aggregate_across_multiple_attachment_fields(self):
        class Multi(AgentPayload):
            resume: list[Attachment] = Field(
                default_factory=list,
                json_schema_extra={"accepted_mime_types": ["application/pdf"]},
            )
            id_photo: list[Attachment] = Field(
                default_factory=list,
                json_schema_extra={"accepted_mime_types": ["image/png", "image/jpeg"]},
            )

        fields = build_payload_card_fields(Multi)
        assert set(fields["accepted_mime_types"]) == {
            "application/pdf", "image/png", "image/jpeg"
        }
