"""Real provider adapter and deterministic scripted model; neither implements an Agent loop."""

import json
import os
import re
from copy import deepcopy
from urllib.parse import urlparse

from dotenv import load_dotenv
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    Model,
    OpenAIModel,
)
from smolagents.monitoring import TokenUsage

from domain import PauseRun, Status, StopReason, canonical


class ProviderFailure(Exception):
    """Safe error boundary before smolagents logs provider failures."""

    def __init__(self, original):
        self.status_code = getattr(original, "status_code", None)
        message = str(original)
        key = os.getenv("HARNESS_API_KEY", "").strip()
        if key:
            message = message.replace(key, "[REDACTED]")
        message = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", message)
        super().__init__(f"{type(original).__name__}: {message[:800]}")


class FakeModel(Model):
    def __init__(self, script: list[dict], cursor: int = 0):
        super().__init__(model_id="scripted-fake-model")
        self.script, self.cursor = deepcopy(script), cursor
        self.received_messages = []

    def generate(self, messages, **kwargs):
        self.received_messages.append(messages)
        index = self.cursor
        self.cursor += 1
        action = (
            self.script[index]
            if index < len(self.script)
            else {"name": "final_answer", "arguments": {"answer": "Script finished."}}
        )
        batch = action.get("batch", [action])
        calls = [
            ChatMessageToolCall(
                id=item.get("id", f"fake-{index + 1}-{n}"),
                type="function",
                function=ChatMessageToolCallFunction(
                    name=item["name"], arguments=deepcopy(item["arguments"])
                ),
            )
            for n, item in enumerate(batch)
        ]
        return ChatMessage(
            role="assistant",
            content="",
            tool_calls=calls,
            token_usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    def export_state(self):
        return {"kind": "fake", "script": self.script, "cursor": self.cursor}


def live_model() -> OpenAIModel:
    load_dotenv(override=False)
    key = os.getenv("HARNESS_API_KEY", "").strip()
    if not key:
        raise ValueError("Set HARNESS_API_KEY in the ignored .env file before real-model runs")
    provider_options = {}
    if urlparse(os.environ["HARNESS_BASE_URL"]).hostname == "api.deepseek.com":
        # DeepSeek defaults to thinking mode, which rejects smolagents' tool_choice='required'.
        provider_options["extra_body"] = {"thinking": {"type": "disabled"}}
    return OpenAIModel(
        model_id=os.environ["HARNESS_MODEL"],
        api_base=os.environ["HARNESS_BASE_URL"],
        api_key=key,
        client_kwargs={"timeout": 45, "max_retries": 0},
        temperature=0,
        max_tokens=2048,
        parallel_tool_calls=False,
        **provider_options,
    )


def model_from_state(data: dict):
    if data.get("kind") == "fake":
        return FakeModel(data["script"], data.get("cursor", 0))
    if data.get("kind") == "live":
        model = live_model()
        if model.model_id != data["model_id"] or os.environ["HARNESS_BASE_URL"] != data["base_url"]:
            raise ValueError("Resume must use the original provider/model configuration")
        return model
    raise ValueError("Unsupported model configuration")


def export_model(model):
    if isinstance(model, FakeModel):
        return model.export_state()
    return {
        "kind": "live",
        "model_id": model.model_id,
        "base_url": os.environ["HARNESS_BASE_URL"],
        "provider_options": model.kwargs.get("extra_body", {}),
    }


class BudgetModel(Model):
    def __init__(self, inner: Model, harness):
        super().__init__(model_id=inner.model_id)
        self.inner, self.harness = inner, harness

    def generate(self, messages, **kwargs):
        h, state = self.harness, self.harness.state
        if h.sandbox.cancel_event.is_set():
            state.status, state.stop_reason = Status.STOPPED, StopReason.CANCELLED
        elif state.step_count >= state.policy.max_steps:
            state.status, state.stop_reason = Status.STOPPED, StopReason.STEP_LIMIT
        elif state.token_count >= state.policy.max_tokens:
            state.status, state.stop_reason = Status.STOPPED, StopReason.TOKEN_LIMIT
        if state.status != Status.RUNNING:
            h.audit.append(
                "state",
                rule=state.stop_reason.value if state.stop_reason else state.status.value,
                decision="STOP",
            )
            h.gate.persist()
            raise PauseRun()
        state.step_count += 1
        h.gate.persist(safe=False)  # Requests consume budget even if the process/provider fails.
        try:
            message = self.inner.generate(messages, **kwargs)
        except Exception as exc:
            raise ProviderFailure(exc) from None
        if message.token_usage is not None:
            state.token_count += message.token_usage.total_tokens
        else:
            size = sum(len(m.model_dump_json()) for m in messages) + len(message.model_dump_json())
            state.token_count += max(1, (size + 3) // 4)
            state.estimated_usage = True
        state.model_state = export_model(self.inner)
        h.audit.append(
            "model",
            rule="MODEL_USAGE",
            decision="RECORDED",
            step=state.step_count,
            tokens=state.token_count,
            estimated=state.estimated_usage,
        )
        h.gate.persist(safe=False)
        if state.token_count > state.policy.max_tokens:
            state.status, state.stop_reason = Status.STOPPED, StopReason.TOKEN_LIMIT
            h.gate.persist()
            raise PauseRun()
        return message


def message_dict(message: ChatMessage) -> dict:
    return json.loads(message.model_dump_json())


def observation_message(value) -> ChatMessage:
    return ChatMessage(
        role="tool-response", content=[{"type": "text", "text": "Observation:\n" + canonical(value)}]
    )
