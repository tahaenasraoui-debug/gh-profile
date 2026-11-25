from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Callable

import httpx
import pytest

from gh_profile import api


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    real_async_client = httpx.AsyncClient

    def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(api.httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_get_github_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "  env-token-123  ")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert await api.get_github_token() == "env-token-123"


@pytest.mark.asyncio
async def test_get_github_token_from_gh_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"cli-token-456\n", b"")

    async def _fake_exec(*_args: object, **_kwargs: object) -> _FakeProc:
        return _FakeProc()

    monkeypatch.setattr(api.asyncio, "create_subprocess_exec", _fake_exec)
    assert await api.get_github_token() == "cli-token-456"


@pytest.mark.asyncio
async def test_get_github_token_handles_missing_cli_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    async def _missing_exec(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(api.asyncio, "create_subprocess_exec", _missing_exec)
    assert await api.get_github_token() is None

    killed = False

    class _HangingProc:
        returncode = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return (b"", b"")

        def kill(self) -> None:
            nonlocal killed
            killed = True

        async def wait(self) -> int:
            return -9

    async def _hanging_exec(*_args: object, **_kwargs: object) -> _HangingProc:
        return _HangingProc()

    async def _immediate_timeout(coro: object, **_kwargs: object) -> object:
        coro.close()  # type: ignore[union-attr]
        raise asyncio.TimeoutError

    monkeypatch.setattr(api.asyncio, "create_subprocess_exec", _hanging_exec)
    monkeypatch.setattr(api.asyncio, "wait_for", _immediate_timeout)
    assert await api.get_github_token() is None
    assert killed is True


@pytest.mark.asyncio
async def test_fetch_profile_data_url_encodes_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    seen_paths: list[str] = []
    readme_b64 = base64.b64encode(b"# Hello").decode("ascii")

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.raw_path.decode("ascii"))
        if request.url.host == "api.github.com" and request.url.path.startswith("/users/"):
            return httpx.Response(
                200,
                json={"login": "alice", "followers": 10, "following": 5, "public_repos": 3},
            )
        if request.url.path.endswith("/readme"):
            return httpx.Response(200, json={"content": readme_b64})
        if request.url.path == "/graphql":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "user": {
                            "contributionsCollection": {
                                "contributionCalendar": {
                                    "totalContributions": 4,
                                    "weeks": [
                                        {
                                            "contributionDays": [
                                                {
                                                    "date": "2025-01-05",
                                                    "contributionCount": 4,
                                                    "contributionLevel": "SECOND_QUARTILE",
                                                }
                                            ]
                                        }
                                    ],
                                }
                            }
                        }
                    }
                },
            )
        return httpx.Response(404)

    _patch_async_client(monkeypatch, _handler)
    profile = await api.fetch_profile_data("foo/bar?baz=1")
    assert profile.readme_raw == "# Hello"
    assert profile.contributions is not None
    assert profile.contributions.total == 4
    assert "/users/foo%2Fbar%3Fbaz%3D1" in seen_paths
    assert "/repos/foo%2Fbar%3Fbaz%3D1/foo%2Fbar%3Fbaz%3D1/readme" in seen_paths


@pytest.mark.asyncio
async def test_fetch_profile_data_user_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    _patch_async_client(monkeypatch, _handler)
    with pytest.raises(api.UserNotFoundError):
        await api.fetch_profile_data("nobody")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "body"),
    [
        (403, {"X-RateLimit-Remaining": "0"}, {"message": "API rate limit exceeded"}),
        (429, {}, {"message": "Too many requests"}),
        (
            403,
            {"X-RateLimit-Remaining": "50", "Retry-After": "60"},
            {"message": "Forbidden"},
        ),
        (
            403,
            {"X-RateLimit-Remaining": "50"},
            {"message": "You have exceeded a secondary rate limit."},
        ),
    ],
)
async def test_rate_limit_detection_primary_and_secondary(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    headers: dict[str, str],
    body: dict[str, str],
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, json=body)

    _patch_async_client(monkeypatch, _handler)
    with pytest.raises(api.RateLimitError):
        await api.fetch_profile_data("alice")


@pytest.mark.asyncio
async def test_non_rate_limit_403_raises_http_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "50"},
            json={"message": "Resource not accessible by integration"},
        )

    _patch_async_client(monkeypatch, _handler)
    with pytest.raises(httpx.HTTPStatusError):
        await api.fetch_profile_data("alice")


@pytest.mark.asyncio
async def test_graphql_errors_logged_and_fallback_to_scrape(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    scrape_html = (
        '<h2 id="js-contribution-activity-description">12 contributions in the last year</h2>'
        '<table><tr>'
        '<td class="ContributionCalendar-day" data-date="2025-01-05" data-level="2"></td>'
        '<td class="ContributionCalendar-day" data-date="2025-01-06" data-level="4"></td>'
        "</tr></table>"
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/alice":
            return httpx.Response(
                200,
                json={"login": "alice", "followers": 1, "following": 2, "public_repos": 0},
            )
        if request.url.path.endswith("/readme"):
            return httpx.Response(404)
        if request.url.path == "/graphql":
            return httpx.Response(
                200,
                json={"errors": [{"message": "Something went wrong"}], "data": {"user": None}},
            )
        if request.url.path == "/users/alice/contributions":
            return httpx.Response(200, text=scrape_html)
        return httpx.Response(404)

    _patch_async_client(monkeypatch, _handler)
    with caplog.at_level(logging.WARNING, logger="gh_profile.api"):
        profile = await api.fetch_profile_data("alice")

    assert profile.contributions is not None
    assert profile.contributions.total == 12
    assert len(profile.contributions.weeks) == 1
    assert len(profile.contributions.weeks[0]) == 2
    assert any("Something went wrong" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_graphql_rate_limit_and_optional_warnings(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/alice":
            return httpx.Response(
                200,
                json={
                    "login": "alice",
                    "avatar_url": "https://avatars.githubusercontent.com/u/1",
                    "followers": 1,
                    "following": 2,
                    "public_repos": 0,
                },
            )
        if request.url.host == "avatars.githubusercontent.com":
            raise httpx.ConnectError("network down", request=request)
        if request.url.path.endswith("/readme"):
            return httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "50", "Retry-After": "30"},
                json={"message": "secondary rate limit"},
            )
        if request.url.path == "/graphql":
            return httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "0"},
                json={"message": "rate limit exceeded"},
            )
        if request.url.path == "/users/alice/contributions":
            return httpx.Response(404)
        return httpx.Response(404)

    _patch_async_client(monkeypatch, _handler)
    with caplog.at_level(logging.WARNING, logger="gh_profile.api"):
        profile = await api.fetch_profile_data("alice")

    assert profile.avatar_bytes is None
    assert profile.readme_raw is None
    assert profile.contributions is None
    assert "Could not download avatar image." in profile.warnings
    assert "Rate limit hit while fetching profile README." in profile.warnings
    assert "Could not load contribution heatmap." in profile.warnings
    assert any("Rate limit hit during GraphQL" in rec.message for rec in caplog.records)
