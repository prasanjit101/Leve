"""Rich dev client (SPEC §8).

A thin chat client over the HTTP API — the TUI is *just a client*, driving the
agent through the same endpoints ``curl`` or CI would. It opens a session, sends
messages, and renders the normalized SSE event stream (model output, tool calls,
approval requests). Streaming text is flushed token-by-token, in order with tool
events, so the transcript stays readable.

Rendering is handled by Rich's :class:`~rich.console.Console`; user input is read
off the event loop via a worker thread so prompting and streaming cooperate.
"""

from __future__ import annotations

import asyncio
import json

import httpx
from rich.console import Console
from rich.markup import escape

from leve.serving.server import API_PREFIX

# Commands that end the session from the prompt.
_QUIT_COMMANDS = {"/quit", "/exit", "/q"}


class LeveTUI:
    """A minimal terminal chat client for a running ``leve dev`` server."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_id: str | None = None
        self.console = Console()

    async def run(self) -> None:  # pragma: no cover - interactive path
        self.console.print("[bold]Leve[/bold] — type a message, or /quit to exit.")
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as client:
            if not await self._open_session(client):
                return
            await self._chat_loop(client)

    async def _open_session(self, client: httpx.AsyncClient) -> bool:
        try:
            resp = await client.post(f"{API_PREFIX}/session")
            resp.raise_for_status()
            self.session_id = resp.json()["session_id"]
        except Exception as exc:  # pragma: no cover - interactive path
            self.console.print(f"[red]Could not reach server: {escape(str(exc))}[/red]")
            return False
        self.console.print(f"[dim]session {self.session_id}[/dim]")
        return True

    async def _chat_loop(self, client: httpx.AsyncClient) -> None:
        while True:
            try:
                # Read input off the event loop so it never blocks streaming.
                raw = await asyncio.to_thread(
                    self.console.input, "[bold cyan]you[/bold cyan] "
                )
            except (EOFError, KeyboardInterrupt):
                break
            text = raw.strip()
            if not text:
                continue
            if text.lower() in _QUIT_COMMANDS:
                break
            await self._send_turn(client, text)

    async def _send_turn(self, client: httpx.AsyncClient, text: str) -> None:
        url = f"{API_PREFIX}/session/{self.session_id}/message"
        state = _TurnState()
        try:
            async with client.stream("POST", url, json={"message": text}) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode()
                    self.console.print(
                        f"[red]error {resp.status_code}: {escape(body)}[/red]"
                    )
                    return
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        self._render(json.loads(line[6:]), state)
        except Exception as exc:
            state.end_stream(self.console)
            self.console.print(f"[red]stream failed: {escape(str(exc))}[/red]")

    def _render(self, event: dict, state: _TurnState) -> None:
        kind = event.get("type")
        if kind == "model.delta":
            state.delta(self.console, event.get("text", ""))
        elif kind == "model.message":
            # Reliable full output. If tokens already streamed, they are the
            # message — don't print it twice.
            if not state.streamed:
                self.console.print(
                    "[bold green]agent[/bold green] " + escape(event.get("text", ""))
                )
        elif kind == "tool.call":
            state.end_stream(self.console)
            self.console.print(
                f"[yellow]→ {escape(event['tool'])}"
                f"({escape(json.dumps(event.get('input')))})[/yellow]"
            )
        elif kind == "tool.result":
            self.console.print(
                f"[dim]  {escape(event['tool'])} ⇒ {escape(str(event.get('output')))}[/dim]"
            )
        elif kind == "approval.requested":
            state.end_stream(self.console)
            self.console.print(
                f"[magenta]approval requested: {escape(str(event['interrupt']))}[/magenta]"
            )
        elif kind == "error":
            state.end_stream(self.console)
            self.console.print(f"[red]{escape(event['message'])}[/red]")
        elif kind == "turn.end":
            state.end_stream(self.console)


class _TurnState:
    """Tracks in-progress streaming so deltas render inline and tidy up.

    Streamed tokens are printed without a trailing newline; the first delta emits
    the ``agent`` label and ``end_stream`` closes the line before any other event.
    """

    def __init__(self) -> None:
        self.streamed = False

    def delta(self, console: Console, text: str) -> None:
        if not self.streamed:
            console.print("[bold green]agent[/bold green] ", end="")
            self.streamed = True
        # Raw token text: no markup interpretation, no syntax highlighting.
        console.print(text, end="", markup=False, highlight=False, soft_wrap=True)

    def end_stream(self, console: Console) -> None:
        if self.streamed:
            console.print()  # terminate the streamed line
            self.streamed = False


def run_tui(base_url: str) -> None:  # pragma: no cover - interactive entrypoint
    asyncio.run(LeveTUI(base_url).run())
