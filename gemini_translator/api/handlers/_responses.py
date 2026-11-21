import json

from ..errors import NetworkError
from ._sse_stream import SSEStreamInterrupted


def parse_responses_result(result, on_usage):
    """Extract the final answer and finish reason from a Responses API object."""
    on_usage(result.get("usage"))
    status = result.get("status")
    if status not in ("completed", "incomplete"):
        error = result.get("error") or {}
        raise NetworkError(f"Ошибка Responses API ({status}): {error.get('message', '')}")

    parts = [
        part
        for item in result.get("output", [])
        if item.get("type") == "message" and item.get("phase") != "commentary"
        for part in item.get("content", [])
    ]
    text = "".join(part.get("text", "") for part in parts if part.get("type") == "output_text")
    incomplete_reason = (result.get("incomplete_details") or {}).get("reason")
    if incomplete_reason == "content_filter" or any(part.get("type") == "refusal" for part in parts):
        return text, "content_filter"
    if incomplete_reason == "max_output_tokens":
        return text, "length"
    if status == "incomplete":
        raise NetworkError(f"Responses API вернул незавершённый ответ: {incomplete_reason}")
    return text, "stop"


async def parse_responses_stream(response, capture_raw, on_usage):
    """Read Responses API SSE and preserve partial output on interruption."""
    chunks = []
    commentary_indexes = set()
    raw_lines = [] if capture_raw else None

    try:
        async for line in response.content:
            line = line.decode("utf-8").strip()
            if raw_lines is not None:
                raw_lines.append(line)
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            event = json.loads(line[5:])
            event_type = event.get("type")
            if event_type == "response.output_item.added" and event.get("item", {}).get("phase") == "commentary":
                commentary_indexes.add(event.get("output_index"))
            elif event_type == "response.output_text.delta" and event.get("output_index") not in commentary_indexes:
                chunks.append(event.get("delta", ""))
            elif event_type in ("response.completed", "response.incomplete", "response.failed"):
                result = event["response"]
                text, finish_reason = parse_responses_result(result, on_usage)
                if "output" not in result:
                    text = "".join(chunks)
                return text, finish_reason, raw_lines
            elif event_type == "error":
                raise NetworkError(f"Ошибка Responses API: {event.get('message', '')}")
        raise NetworkError("Поток Responses API завершился без финального события.")
    except Exception as error:
        if chunks:
            raise SSEStreamInterrupted(error, "".join(chunks), raw_lines=raw_lines) from error
        raise
