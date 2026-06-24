#!/usr/bin/env python3
"""Validate and resolve a *cross-channel* submission described in a GitHub issue.

Where `build-request` triggers builds of packages already present in *this*
channel, a submission proposes a package that lives in a **different** channel
repo and asks this channel (mip-core) to test it and, on acceptance, adopt it.

Issue title (the sole source of the submission; the body is free-form context):

    submit <owner>/<channel>/<name>@<release>

which names the package release `packages/<name>/<release>` in the source repo
`<owner>/mip-<channel>` (e.g. `submit mip-org/staging/fmm2d@main` → repo
`mip-org/mip-staging`, path `packages/fmm2d/main`).

The flow (driven by the `submit-package-request.yml` workflow):

  1. On issue open, `validate` confirms the source repo has
     `packages/<name>/<release>/source.yaml` and posts admin instructions.
  2. An admin comments `build`; `resolve` lists the architectures the package
     declares and the workflow dispatches `build-package.yml` per arch with
     `source_repo=<owner>/mip-<channel>` and `upload=false` — the build/test
     run exactly as usual but publish nothing; the `.mhl` survives only as a
     build artifact to download and install locally.
  3. An admin comments `accept`; the workflow copies the package folder onto
     mip-core `main`, which runs the normal build+release pipeline.

Subcommands:

    submit-package-request validate --output-file PATH
        Render the comment to post on issue-open. The issue title is left
        as the submitter wrote it (it must stay parseable as a submission).

    submit-package-request resolve --dispatch-file PATH [--errors-file PATH] [--for-promotion]
        Re-parse the title and write one TSV row per architecture
        (`<package_path>\\t<arch>\\t<source_repo>`). With --for-promotion the
        source_repo column is left empty (the package is built from mip-core's
        own tree after the copy, not from the source repo).

    submit-package-request spec --output-file PATH
        Write `owner`, `channel`, `name`, `release`, `source_repo`,
        `package_path` as `key=value` lines (for the workflow's copy step).

Architectures and the existence check are read from the source repo over the
GitHub contents API (honouring $GH_TOKEN / $GITHUB_TOKEN for rate limits and
private repos).
"""

import os
import re
import sys
import base64
from pathlib import Path

import requests
import yaml

from .build_request import (
    SUPPORTED_ARCHITECTURES,
    arches_from_mip_config,
)

# `submit <owner>/<channel>/<name>@<release>`. owner/channel use GitHub's
# repo/owner character class; name/release match the on-disk folder class
# (same as build_request.PACKAGE_REF_RE).
SUBMIT_TITLE_RE = re.compile(
    r"^\s*submit\s+"
    r"([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/"
    r"([A-Za-z0-9._+\-]+)@([A-Za-z0-9._+\-]+)\s*$",
    re.IGNORECASE,
)

GITHUB_API = "https://api.github.com"

TITLE_HINT = "    submit <owner>/<channel>/<name>@<release>"


def get_title():
    """The issue title — the sole source of the submission reference."""
    return os.environ.get("ISSUE_TITLE", "")


def parse_submit_title(title):
    """Parse a submit title into a spec dict, or return None.

    Keys: owner, channel, name, release, source_repo, package_path.
    """
    m = SUBMIT_TITLE_RE.match(title or "")
    if not m:
        return None
    owner, channel, name, release = m.groups()
    return {
        "owner": owner,
        "channel": channel,
        "name": name,
        "release": release,
        "source_repo": f"{owner}/mip-{channel}",
        "package_path": f"packages/{name}/{release}",
    }


def _auth_headers():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_remote_file(repo, path):
    """Return the decoded text of `path` in `repo`, or None if absent.

    Uses the contents API at the repo's default branch. Raises for transport
    errors other than 404 so a flaky lookup is not silently treated as
    "missing".
    """
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    resp = requests.get(url, headers=_auth_headers(), timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8")
    return data.get("content", "")


def remote_package_exists(spec):
    """True iff the source repo has `<package_path>/source.yaml`."""
    return fetch_remote_file(
        spec["source_repo"], f"{spec['package_path']}/source.yaml"
    ) is not None


def remote_arches(spec):
    """Architectures the source package declares, intersected with supported.

    Returns (arches, error). `error` is a human-readable string (markdown
    bullet body) when the package has no usable channel-side mip.yaml or
    declares no supported architecture.
    """
    text = fetch_remote_file(
        spec["source_repo"], f"{spec['package_path']}/mip.yaml"
    )
    if text is None:
        return [], (
            f"- `{spec['package_path']}/mip.yaml` not found in "
            f"`{spec['source_repo']}`; cannot determine architectures."
        )
    config = yaml.safe_load(text) or {}
    arches = arches_from_mip_config(config)
    if not arches:
        valid = ", ".join(f"`{a}`" for a in SUPPORTED_ARCHITECTURES)
        return [], (
            f"- `{spec['name']}@{spec['release']}` declares no supported "
            f"architecture in its mip.yaml. Supported: {valid}."
        )
    return arches, None


def render_invalid_comment():
    return (
        "The issue title is not a valid submission.\n\n"
        "Use a title of the form:\n\n"
        f"{TITLE_HINT}\n\n"
        "For example, `submit mip-org/staging/fmm2d@main` proposes "
        "`packages/fmm2d/main` from `mip-org/mip-staging`.\n"
    )


def render_validation_comment(spec, exists, arches, arch_error,
                              auto_approved=False):
    if not exists:
        return (
            f"`{spec['package_path']}` was not found in "
            f"`{spec['source_repo']}` (no `source.yaml` there).\n\n"
            "Check the owner, channel, package name, and release in the "
            "title, then edit it or open a new issue.\n"
        )

    lines = [
        f"Found `{spec['package_path']}` in `{spec['source_repo']}`.",
        "",
    ]
    if arch_error:
        lines += [
            "But its architectures could not be resolved:",
            "",
            arch_error,
            "",
            "Fix the package's `mip.yaml` in the source repo, then comment "
            "to re-check.",
        ]
        return "\n".join(lines) + "\n"

    arch_list = ", ".join(f"`{a}`" for a in arches)
    lines += [f"Declared architectures: {arch_list}.", ""]

    who = (
        "You have write access on this repo, so you can"
        if auto_approved
        else "An admin (anyone with write access on this repo) can"
    )
    lines += [
        f"{who}:",
        "",
        "- Comment `build` (on its own line) to run **test builds** for "
        "every architecture above. Each build runs the full build+test "
        "pipeline but publishes nothing — the resulting `.mhl` is uploaded "
        "as a workflow artifact you can download and `mip install` locally.",
        "- Comment `accept` (on its own line) to **promote** the package: "
        "its folder is copied into this channel's `packages/` on `main`, "
        "which runs the normal build-and-release pipeline.",
    ]
    return "\n".join(lines) + "\n"


def cmd_validate(args):
    title = get_title()
    spec = parse_submit_title(title)
    if spec is None:
        Path(args.output_file).write_text(render_invalid_comment())
        return 0

    exists = remote_package_exists(spec)
    arches, arch_error = ([], None)
    if exists:
        arches, arch_error = remote_arches(spec)

    Path(args.output_file).write_text(
        render_validation_comment(
            spec, exists, arches, arch_error, args.auto_approved)
    )
    return 0


def cmd_resolve(args):
    title = get_title()
    spec = parse_submit_title(title)
    errors = []
    if spec is None:
        errors.append(
            "- Title is not a valid submission "
            f"(`{TITLE_HINT.strip()}`)."
        )
    elif not remote_package_exists(spec):
        errors.append(
            f"- `{spec['package_path']}` not found in "
            f"`{spec['source_repo']}`."
        )
    else:
        arches, arch_error = remote_arches(spec)
        if arch_error:
            errors.append(arch_error)

    if errors:
        Path(args.dispatch_file).write_text("")
        if args.errors_file:
            Path(args.errors_file).write_text("\n".join(errors) + "\n")
        return 1

    source_repo = "" if args.for_promotion else spec["source_repo"]
    rows = [
        f"{spec['package_path']}\t{arch}\t{source_repo}\n"
        for arch in arches
    ]
    Path(args.dispatch_file).write_text("".join(rows))
    if args.errors_file:
        Path(args.errors_file).write_text("")
    return 0


def cmd_spec(args):
    title = get_title()
    spec = parse_submit_title(title)
    if spec is None:
        print("Title is not a valid submission.", file=sys.stderr)
        return 1
    lines = [f"{k}={spec[k]}" for k in (
        "owner", "channel", "name", "release", "source_repo", "package_path")]
    Path(args.output_file).write_text("\n".join(lines) + "\n")
    return 0


def register(subparsers):
    p = subparsers.add_parser(
        "submit-package-request",
        help="Validate or resolve a cross-channel package submission.")
    sub = p.add_subparsers(dest="mode", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--output-file", required=True)
    v.add_argument(
        "--auto-approved", action="store_true",
        help="Issue author is an admin, so phrase instructions accordingly.")
    v.set_defaults(func=cmd_validate)

    r = sub.add_parser("resolve")
    r.add_argument("--dispatch-file", required=True)
    r.add_argument("--errors-file", default=None)
    r.add_argument(
        "--for-promotion", action="store_true",
        help="Leave the source_repo column empty (build from this channel's "
             "own tree after the package is copied in).")
    r.set_defaults(func=cmd_resolve)

    s = sub.add_parser("spec")
    s.add_argument("--output-file", required=True)
    s.set_defaults(func=cmd_spec)
