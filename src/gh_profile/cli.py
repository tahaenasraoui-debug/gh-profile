from __future__ import annotations

import argparse
import sys

from gh_profile.app import GitHubProfileApp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="gh-profile",
        description="Display a GitHub user's profile in a multi-pane terminal UI.",
    )
    parser.add_argument("username", help="GitHub username (e.g., torvalds, willmcgugan)")
    args = parser.parse_args(argv)

    username = args.username.lstrip("@").strip()
    if not username:
        print("Error: Please provide a valid GitHub username.", file=sys.stderr)
        sys.exit(1)

    app = GitHubProfileApp(username=username)
    app.run()


if __name__ == "__main__":
    main()
