"""LLM adapters with one common shape, so the agent loop does not care which provider it uses.

Internal message format:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str | None, "tool_calls": [{"id", "name", "input": dict}]}
  {"role": "tool", "tool_call_id": str, "content": str}

System prompt: list of Segment(text, cache). Segments marked cache=True must not
change between requests (they form the cached prefix); put changing text last.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger("deukgeuni.llm")


@dataclass
class Segment:
    text: str
    cache: bool = False


@dataclass
class Tool:
    name: str
    description: str
    schema: dict  # JSON Schema for the input object


@dataclass
class Reply:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


class LLM:
    def chat(self, system: list[Segment], messages: list[dict], tools: list[Tool], user_key: str = "") -> Reply:
        raise NotImplementedError


# ---------------------------------------------------------------- Anthropic
class AnthropicLLM(LLM):
    def __init__(self, model: str, max_tokens: int = 1500, client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
        self.client, self.model, self.max_tokens = client, model, max_tokens

    @staticmethod
    def _messages(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)  # consecutive tool results share one user turn
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def chat(self, system, messages, tools, user_key=""):
        sys_blocks = [{"type": "text", "text": s.text} for s in system]
        last_cached = max((i for i, s in enumerate(system) if s.cache), default=None)
        if last_cached is not None:
            # stable prefix (tools + AGENTS.md + profile): keep for 1h — gym rest periods often exceed 5 min
            sys_blocks[last_cached]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=sys_blocks,
            tools=[{"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools],
            messages=self._messages(messages),
            cache_control={"type": "ephemeral"},  # automatic breakpoint on the growing conversation
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
        u = resp.usage
        usage = {k: getattr(u, k, None) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
        log.info("anthropic usage %s", usage)
        return Reply(text=text, tool_calls=calls, usage=usage)


# ---------------------------------------------------------------- OpenAI
class OpenAILLM(LLM):
    def __init__(self, model: str, max_tokens: int = 1500, client=None):
        if not model:
            raise SystemExit("OpenAI는 LLM_MODEL을 지정해야 함")
        if client is None:
            import openai
            client = openai.OpenAI()  # OPENAI_API_KEY from env
        self.client, self.model, self.max_tokens = client, model, max_tokens

    @staticmethod
    def _messages(system: list[Segment], messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": "\n\n".join(s.text for s in system)}]
        for m in messages:
            if m["role"] == "assistant":
                msg = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"], "arguments": json.dumps(c["input"], ensure_ascii=False)}}
                                         for c in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def chat(self, system, messages, tools, user_key=""):
        # OpenAI caches long identical prefixes automatically; prompt_cache_key improves hit rate per user
        resp = self.client.chat.completions.create(
            model=self.model,
            max_completion_tokens=self.max_tokens,
            messages=self._messages(system, messages),
            tools=[{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.schema}} for t in tools],
            **({"prompt_cache_key": user_key} if user_key else {}),
        )
        m = resp.choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "input": json.loads(c.function.arguments or "{}")} for c in (m.tool_calls or [])]
        usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                 "completion_tokens": getattr(resp.usage, "completion_tokens", None)}
        log.info("openai usage %s", usage)
        return Reply(text=m.content or "", tool_calls=calls, usage=usage)


def make_llm(provider: str, model: str, max_tokens: int) -> LLM:
    if provider == "anthropic":
        return AnthropicLLM(model, max_tokens)
    if provider == "openai":
        return OpenAILLM(model, max_tokens)
    raise SystemExit(f"알 수 없는 LLM_PROVIDER: {provider} (anthropic | openai)")
