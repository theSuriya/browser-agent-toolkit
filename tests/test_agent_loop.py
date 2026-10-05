"""The agent loop, driven by a scripted (fake) LLM so it runs without a network."""

import json

from app.agent import run_agent
from app.browser import manager


class FakeLLM:
    """Returns queued messages in order, recording what it was asked."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.seen_tools = False

    async def chat(self, messages, tools=None):
        self.seen_tools = tools is not None and len(tools) > 0
        return self.scripted.pop(0)


async def test_agent_calls_a_tool_then_answers(session, site_server):
    fake = FakeLLM(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "navigate",
                            "arguments": json.dumps({"url": f"{site_server}/good.html"}),
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "The page title is Good Site."},
        ]
    )

    outcome = await run_agent(session, "Open the site and report its title.", client=fake)

    assert outcome["ok"] is True
    assert "Good Site" in outcome["result"]
    assert fake.seen_tools is True
    assert [s["tool"] for s in outcome["transcript"]] == ["navigate"]
    assert outcome["transcript"][0]["result"]["ok"] is True


async def test_agent_loop_reports_tool_failure_without_crashing(session):
    fake = FakeLLM(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "click", "arguments": json.dumps({"selector": "#nope", "timeout_ms": 500})},
                    }
                ],
            },
            {"role": "assistant", "content": "I could not find that element."},
        ]
    )

    outcome = await run_agent(session, "Click a button that does not exist.", client=fake)
    assert outcome["ok"] is True
    assert outcome["transcript"][0]["result"]["ok"] is False


async def test_agent_stops_at_max_steps(session, site_server):
    def looping_call():
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_x",
                    "type": "function",
                    "function": {"name": "snapshot", "arguments": "{}"},
                }
            ],
        }

    fake = FakeLLM([looping_call() for _ in range(3)])
    outcome = await run_agent(session, "Never finish.", client=fake, max_steps=3)
    assert outcome["ok"] is False
    assert outcome["error"] == "max_steps"
