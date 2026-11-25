from __future__ import annotations

from typing import ClassVar

import httpx
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, LoadingIndicator, Markdown, Static

from gh_profile.api import (
    GitHubError,
    ProfileData,
    RateLimitError,
    UserNotFoundError,
    fetch_profile_data,
)
from gh_profile.avatar import render_avatar_ascii
from gh_profile.heatmap import render_heatmap
from gh_profile.readme import Badge, preprocess_readme, render_badges_row


class GitHubProfileApp(App[None]):
    """Multi-pane GitHub Profile TUI."""

    TITLE = "gh-profile"
    CSS = """
    Screen {
        background: #0d1117;
        color: #c9d1d9;
    }

    #top-row {
        height: 1fr;
        width: 100%;
    }

    #sidebar {
        width: 30%;
        min-width: 34;
        height: 100%;
        border: round #30363d;
        padding: 1 2;
        background: #0d1117;
    }

    #readme-pane {
        width: 70%;
        height: 100%;
        border: round #30363d;
        padding: 1 2;
        background: #0d1117;
    }

    #heatmap-pane {
        height: 12;
        width: 100%;
        border: round #30363d;
        padding: 0 2;
        background: #0d1117;
        content-align: center middle;
    }

    #avatar-view {
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }

    .badge-row {
        margin: 1 0;
    }

    .status-error {
        color: #f85149;
        text-style: bold;
        padding: 1 2;
    }

    .status-muted {
        color: #8b949e;
        text-style: italic;
        padding: 1 0;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("j", "scroll_readme_down", "Scroll Down"),
        Binding("k", "scroll_readme_up", "Scroll Up"),
    ]

    def __init__(self, username: str) -> None:
        super().__init__()
        self.username = username
        self.sub_title = f"@{username}"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="top-row"):
            with VerticalScroll(id="sidebar"):
                yield LoadingIndicator(id="sidebar-loader")
            with VerticalScroll(id="readme-pane"):
                yield LoadingIndicator(id="readme-loader")
        with Vertical(id="heatmap-pane"):
            yield Static(
                "Loading contribution graph...",
                id="heatmap-content",
                classes="status-muted",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#sidebar").border_title = "Profile"
        self.query_one("#readme-pane").border_title = f"{self.username}/{self.username} README.md"
        self.query_one("#heatmap-pane").border_title = "Contributions"
        self.load_profile()

    def action_refresh(self) -> None:
        self.load_profile()

    def action_scroll_readme_down(self) -> None:
        self.query_one("#readme-pane", VerticalScroll).scroll_down(animate=False)

    def action_scroll_readme_up(self) -> None:
        self.query_one("#readme-pane", VerticalScroll).scroll_up(animate=False)

    @work(exclusive=True)
    async def load_profile(self) -> None:
        sidebar = self.query_one("#sidebar", VerticalScroll)
        readme_pane = self.query_one("#readme-pane", VerticalScroll)
        heatmap_widget = self.query_one("#heatmap-content", Static)

        await sidebar.remove_children()
        await readme_pane.remove_children()
        await sidebar.mount(LoadingIndicator())
        await readme_pane.mount(LoadingIndicator())
        heatmap_widget.update("Loading contribution graph...")

        try:
            data = await fetch_profile_data(self.username)
        except UserNotFoundError as exc:
            await self._show_error(str(exc))
            return
        except RateLimitError as exc:
            await self._show_error(str(exc))
            return
        except GitHubError as exc:
            await self._show_error(f"GitHub error: {exc}")
            return
        except (httpx.HTTPError, OSError, ValueError) as exc:
            await self._show_error(f"Network or unexpected error: {exc}")
            return

        await self._render_sidebar(data)
        await self._render_readme(data)
        heatmap_widget.update(render_heatmap(data.contributions))

    async def _show_error(self, message: str) -> None:
        sidebar = self.query_one("#sidebar", VerticalScroll)
        readme_pane = self.query_one("#readme-pane", VerticalScroll)
        heatmap_widget = self.query_one("#heatmap-content", Static)

        await sidebar.remove_children()
        await readme_pane.remove_children()
        await sidebar.mount(Static(message, classes="status-error"))
        await readme_pane.mount(
            Static("Press 'r' to retry or 'q' to quit.", classes="status-muted")
        )
        heatmap_widget.update("")

    async def _render_sidebar(self, data: ProfileData) -> None:
        sidebar = self.query_one("#sidebar", VerticalScroll)
        await sidebar.remove_children()

        avatar_art = render_avatar_ascii(data.avatar_bytes, width=28, height_rows=14)
        await sidebar.mount(Static(avatar_art, id="avatar-view"))

        info = Text()
        if data.name:
            info.append(f"{data.name}\n", style="bold #f0f6fc")
        handle_line = f"@{data.username}"
        if data.pronouns:
            handle_line += f" · {data.pronouns}"
        info.append(f"{handle_line}\n\n", style="#8b949e")

        if data.bio:
            info.append(f"{data.bio}\n\n", style="#c9d1d9")

        info.append("Followers: ", style="dim")
        info.append(f"{data.followers:,}", style="bold #58a6ff")
        info.append("  Following: ", style="dim")
        info.append(f"{data.following:,}\n\n", style="bold #58a6ff")

        meta_rows = [
            ("Company", data.company),
            ("Location", data.location),
            ("Email", data.email),
            ("Web", data.blog),
            ("X/Twitter", f"@{data.twitter}" if data.twitter else None),
        ]
        for label, value in meta_rows:
            if value:
                info.append(f"{label}: ", style="bold #8b949e")
                info.append(f"{value}\n", style="#c9d1d9")

        if data.warnings:
            info.append("\n")
            for warn in data.warnings:
                info.append(f"! {warn}\n", style="italic #d29922")

        await sidebar.mount(Static(info))

    async def _render_readme(self, data: ProfileData) -> None:
        readme_pane = self.query_one("#readme-pane", VerticalScroll)
        await readme_pane.remove_children()

        if not data.readme_raw:
            await readme_pane.mount(
                Static(
                    f"No profile README found ({data.username}/{data.username}/README.md).",
                    classes="status-muted",
                )
            )
            return

        segments = preprocess_readme(data.readme_raw)
        if not segments:
            await readme_pane.mount(Static("Profile README is empty.", classes="status-muted"))
            return

        for seg in segments:
            if seg.kind == "badges" and isinstance(seg.content, list):
                badges: list[Badge] = seg.content
                await readme_pane.mount(Static(render_badges_row(badges), classes="badge-row"))
            elif seg.kind == "markdown" and isinstance(seg.content, str):
                await readme_pane.mount(Markdown(seg.content))
