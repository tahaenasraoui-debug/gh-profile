from __future__ import annotations

import base64
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"

LEVEL_MAP = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


class GitHubError(Exception):
    """Base error for GitHub data fetching."""


class UserNotFoundError(GitHubError):
    """Raised when the requested GitHub user does not exist."""


class RateLimitError(GitHubError):
    """Raised when the GitHub API rate limit is exceeded."""


@dataclass
class ContributionDay:
    date: date
    count: int
    level: int  # 0..4


@dataclass
class ContributionCalendar:
    total: int
    weeks: list[list[ContributionDay]] = field(default_factory=list)


@dataclass
class ProfileData:
    username: str
    name: str | None
    bio: str | None
    avatar_bytes: bytes | None
    followers: int
    following: int
    public_repos: int
    email: str | None
    location: str | None
    blog: str | None
    twitter: str | None
    company: str | None
    pronouns: str | None
    readme_raw: str | None
    contributions: ContributionCalendar | None
    warnings: list[str] = field(default_factory=list)


def get_github_token() -> str | None:
    """Resolve a GitHub token from environment variables or the `gh` CLI."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return None


def _build_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "gh-profile-tui/0.1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _check_rate_limit(response: httpx.Response) -> None:
    if response.status_code in (403, 429):
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining == "0" or response.status_code == 429:
            raise RateLimitError(
                "GitHub API rate limit exceeded. Set GITHUB_TOKEN or run `gh auth login`."
            )


async def fetch_profile_data(username: str) -> ProfileData:
    """Fetch user profile, avatar, README, and contribution calendar."""
    token = get_github_token()
    headers = _build_headers(token)
    warnings: list[str] = []

    async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
        # 1. Fetch core user profile
        user_resp = await client.get(f"{GITHUB_API}/users/{username}")
        _check_rate_limit(user_resp)
        if user_resp.status_code == 404:
            raise UserNotFoundError(f"GitHub user '@{username}' was not found.")
        user_resp.raise_for_status()
        user: dict[str, Any] = user_resp.json()

        # 2. Fetch avatar image bytes
        avatar_bytes: bytes | None = None
        avatar_url = user.get("avatar_url")
        if avatar_url:
            try:
                img_resp = await client.get(avatar_url)
                if img_resp.status_code == 200:
                    avatar_bytes = img_resp.content
            except httpx.HTTPError:
                warnings.append("Could not download avatar image.")

        # 3. Fetch profile README ({username}/{username})
        readme_raw: str | None = None
        try:
            readme_resp = await client.get(f"{GITHUB_API}/repos/{username}/{username}/readme")
            _check_rate_limit(readme_resp)
            if readme_resp.status_code == 200:
                payload = readme_resp.json()
                encoded = payload.get("content", "")
                if encoded:
                    readme_raw = base64.b64decode(encoded).decode("utf-8", errors="replace")
        except RateLimitError:
            warnings.append("Rate limit hit while fetching profile README.")
        except httpx.HTTPError:
            warnings.append("Failed to fetch profile README.")

        # 4. Fetch contribution calendar (GraphQL if token available, else HTML scrape)
        contributions: ContributionCalendar | None = None
        if token:
            contributions = await _fetch_contributions_graphql(client, username)
        if contributions is None:
            contributions = await _fetch_contributions_scrape(client, username)
        if contributions is None:
            warnings.append("Could not load contribution heatmap.")

        return ProfileData(
            username=user.get("login", username),
            name=user.get("name"),
            bio=user.get("bio"),
            avatar_bytes=avatar_bytes,
            followers=user.get("followers", 0),
            following=user.get("following", 0),
            public_repos=user.get("public_repos", 0),
            email=user.get("email"),
            location=user.get("location"),
            blog=user.get("blog") or None,
            twitter=user.get("twitter_username"),
            company=user.get("company"),
            pronouns=user.get("pronouns"),
            readme_raw=readme_raw,
            contributions=contributions,
            warnings=warnings,
        )


async def _fetch_contributions_graphql(
    client: httpx.AsyncClient, username: str
) -> ContributionCalendar | None:
    query = """
    query($username: String!) {
      user(login: $username) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
                contributionLevel
              }
            }
          }
        }
      }
    }
    """
    try:
        resp = await client.post(
            GITHUB_GRAPHQL,
            json={"query": query, "variables": {"username": username}},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        cal = (
            data.get("data", {})
            .get("user", {})
            .get("contributionsCollection", {})
            .get("contributionCalendar")
        )
        if not cal:
            return None

        weeks: list[list[ContributionDay]] = []
        for week in cal.get("weeks", []):
            days = [
                ContributionDay(
                    date=date.fromisoformat(d["date"]),
                    count=int(d["contributionCount"]),
                    level=LEVEL_MAP.get(d["contributionLevel"], 0),
                )
                for d in week.get("contributionDays", [])
            ]
            if days:
                weeks.append(days)

        return ContributionCalendar(total=int(cal.get("totalContributions", 0)), weeks=weeks)
    except Exception:
        return None


async def _fetch_contributions_scrape(
    client: httpx.AsyncClient, username: str
) -> ContributionCalendar | None:
    """Fallback scraper for https://github.com/users/{username}/contributions."""
    try:
        resp = await client.get(
            f"https://github.com/users/{username}/contributions",
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "text/html"},
        )
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract total contributions from heading
        total = 0
        heading = soup.find("h2", id="js-contribution-activity-description") or soup.find("h2")
        if heading:
            match = re.search(r"([\d,]+)\s+contribution", heading.get_text(" ", strip=True))
            if match:
                total = int(match.group(1).replace(",", ""))

        # Extract all day cells
        day_cells = soup.find_all("td", class_="ContributionCalendar-day")
        days_list: list[ContributionDay] = []
        for cell in day_cells:
            date_str = cell.get("data-date")
            level_str = cell.get("data-level", "0")
            if not date_str:
                continue
            try:
                day_date = date.fromisoformat(str(date_str))
                level = max(0, min(4, int(str(level_str))))
                days_list.append(ContributionDay(date=day_date, count=0, level=level))
            except ValueError:
                continue

        if not days_list:
            return None

        # Sort chronologically and group into Sunday-Saturday weeks
        days_list.sort(key=lambda d: d.date)
        weeks: list[list[ContributionDay]] = []
        current_week: list[ContributionDay] = []
        for day in days_list:
            # Python weekday(): Mon=0..Sun=6 -> GitHub starts weeks on Sunday
            if day.date.weekday() == 6 and current_week:
                weeks.append(current_week)
                current_week = []
            current_week.append(day)
        if current_week:
            weeks.append(current_week)

        return ContributionCalendar(total=total, weeks=weeks)
    except Exception:
        return None
