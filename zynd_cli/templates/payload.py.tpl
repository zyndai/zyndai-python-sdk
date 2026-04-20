"""
Request payload schema for __AGENT_NAME__.

Edit `RequestPayload` to declare the fields your agent expects. The JSON Schema
is automatically advertised at /.well-known/agent.json so callers can
discover what to send. Plain `{"content": "..."}` requests keep working as
long as you leave `content` in the model (or don't mark new fields required).

Examples (uncomment and adapt as needed):

    from typing import Literal
    from pydantic import Field

    class RequestPayload(AgentPayload):
        name: str
        email: str
        age: int
        gender: Literal["m", "f", "other"]

        # A required PDF upload with mime-type whitelist:
        resume: list[Attachment] = Field(
            default_factory=list,
            min_length=1,
            json_schema_extra={"accepted_mime_types": ["application/pdf"]},
        )
"""

from zyndai_agent import AgentPayload, Attachment


class RequestPayload(AgentPayload):
    """Schema for requests to this agent.

    Starts identical to the default AgentPayload so existing callers keep
    working. Add your own fields above the `pass` and they'll show up in
    /.well-known/agent.json automatically.

    File attachments are opt-in: declare a `list[Attachment]` field (any name
    you like) and the agent will advertise `accepts_files: true`. Without
    such a field, file support is not offered.
    """

    pass


# Cap on the total /webhook request body size. Bounds how big an inline
# base64 attachment can come through before Flask rejects with 413. Tune
# per your agent's needs — transcription agents handling audio/video may
# want 50+ MB; a form-filler probably wants 5 MB.
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
