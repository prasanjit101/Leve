"""Textual dev client (SPEC §8).

A thin chat client over the HTTP API — the TUI is *just a client*, driving the
agent through the same endpoints ``curl`` or CI would. It opens a session, sends
messages, and renders the normalized SSE event stream (model output, tool calls,
approval requests). Streaming text is buffered and flushed in order with tool
events so the transcript stays readable.
"""

from __future__ import annotations

import json

import httpx
from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog

from leve.server import API_PREFIX


class LeveTUI(App):
    """A minimal terminal chat client for a running ``leve dev`` server."""

    TITLE = "Leve"
    CSS = """
    RichLog { padding: 0 1; }
    Input { dock: bottom; }
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.session_id: str | None = None
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=None)

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", wrap=True, markup=True)
        yield Input(placeholder="Message the agent…", id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        try:
            resp = await self._client.post(f"{API_PREFIX}/session")
            resp.raise_for_status()
            self.session_id = resp.json()["session_id"]
            self._write(f"[dim]session {self.session_id}[/dim]")
        except Exception as exc:  # pragma: no cover - interactive path
            self._write(f"[red]Could not reach server: {escape(str(exc))}[/red]")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or not self.session_id:
            return
        event.input.value = ""
        self._write(f"[bold cyan]you[/bold cyan] {escape(text)}")
        self.send_turn(text)

    @work(exclusive=True)
    async def send_turn(self, text: str) -> None:  # pragma: no cover - interactive path
        url = f"{API_PREFIX}/session/{self.session_id}/message"
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                self._write("[bold green]agent[/bold green] " + escape("".join(buffer)))
                buffer.clear()

        try:
            async with self._client.stream("POST", url, json={"message": text}) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode()
                    self._write(f"[red]error {resp.status_code}: {escape(body)}[/red]")
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    self._render(json.loads(line[6:]), buffer, flush)
        except Exception as exc:
            self._write(f"[red]stream failed: {escape(str(exc))}[/red]")

    def _render(self, event: dict, buffer: list[str], flush) -> None:  # pragma: no cover
        kind = event.get("type")
        if kind == "model.delta":
            buffer.append(event.get("text", ""))
        elif kind == "model.message":
            # Reliable full output. If tokens already streamed, they are the
            # message — don't print it twice.
            if not buffer:
                self._write("[bold green]agent[/bold green] " + escape(event.get("text", "")))
        elif kind == "tool.call":
            flush()
            self._write(f"[yellow]→ {escape(event['tool'])}"
                        f"({escape(json.dumps(event.get('input')))})[/yellow]")
        elif kind == "tool.result":
            self._write(f"[dim]  {escape(event['tool'])} ⇒ "
                        f"{escape(str(event.get('output')))}[/dim]")
        elif kind == "approval.requested":
            flush()
            self._write(f"[magenta]approval requested: "
                        f"{escape(str(event['interrupt']))}[/magenta]")
        elif kind == "error":
            flush()
            self._write(f"[red]{escape(event['message'])}[/red]")
        elif kind == "turn.end":
            flush()

    def _write(self, markup: str) -> None:
        self.query_one("#log", RichLog).write(markup)

    async def on_unmount(self) -> None:
        await self._client.aclose()


def run_tui(base_url: str) -> None:  # pragma: no cover - interactive entrypoint
    LeveTUI(base_url).run()
