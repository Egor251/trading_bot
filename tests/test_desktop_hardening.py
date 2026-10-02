"""The desktop operator shell must not ship with an open webview.

`tauri.conf.json` had `"csp": null`. Tauri's IPC bridge is injected into the
page, and the CSP is what constrains what a script in that page is allowed to do
— so a null CSP means any XSS in the operator UI escalates straight to Tauri IPC.
That matters more than usual here for three reasons:

* `withGlobalTauri: true` puts the IPC surface on `window`, reachable by any
  script that runs in the page;
* `tauri-plugin-shell` is registered;
* the UI renders `ai_analyst` output, i.e. text produced by a model behind an
  external API. That is untrusted input reaching the operator surface, and the
  operator surface can halt, resume and arm live trading.

The app is a local static export (`output: "export"` in next.config.ts) that
talks only to the BFF on loopback, so a strict policy is entirely feasible. These
tests parse the real config so the policy cannot be quietly relaxed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CONF = REPO / "ui" / "desktop" / "src-tauri" / "tauri.conf.json"


@pytest.fixture(scope="module")
def conf() -> dict:
    return json.loads(CONF.read_text(encoding="utf-8"))


def test_config_is_valid_json_and_has_the_expected_shape(conf: dict) -> None:
    assert conf["app"]["windows"][0]["label"] == "main"
    assert "security" in conf["app"]


def test_csp_is_not_disabled(conf: dict) -> None:
    csp = conf["app"]["security"].get("csp")
    assert csp, "csp must not be null/empty — that removes Tauri's XSS mitigation"
    assert isinstance(csp, dict), "expected a directive map, not a single string"


def test_default_source_is_self(conf: dict) -> None:
    assert conf["app"]["security"]["csp"]["default-src"] == "'self'"


def test_no_inline_or_remote_scripts(conf: dict) -> None:
    """`unsafe-inline` here would defeat the point of having a CSP."""
    assert conf["app"]["security"]["csp"]["script-src"] == "'self'"


def test_no_wildcard_in_any_directive(conf: dict) -> None:
    offenders = [
        name
        for name, value in conf["app"]["security"]["csp"].items()
        if "*" in str(value)
    ]
    assert not offenders, f"wildcard source allowed in {offenders}"


def test_plugin_and_base_uri_are_locked_down(conf: dict) -> None:
    csp = conf["app"]["security"]["csp"]
    assert csp["object-src"] == "'none'"
    assert csp["base-uri"] == "'self'"
    assert csp["form-action"] == "'none'"


def test_connect_src_covers_exactly_the_loopback_bff(conf: dict) -> None:
    """The web client uses ws://127.0.0.1:8787 and http://127.0.0.1:8787."""
    connect = conf["app"]["security"]["csp"]["connect-src"]
    assert "ws://127.0.0.1:8787" in connect
    assert "http://127.0.0.1:8787" in connect
    # Tauri routes IPC over these two pseudo-origins; they are not remote hosts.
    assert "ipc:" in connect
    assert "http://ipc.localhost" in connect
    # Nothing else: the UI is a local static export, so every other origin is a
    # bug or an exfiltration route.
    stripped = (
        connect.replace("http://127.0.0.1:8787", "")
        .replace("http://ipc.localhost", "")
        .replace("ws://127.0.0.1:8787", "")
        .replace("ipc:", "")
    )
    assert "http" not in stripped and "https" not in stripped
    assert "ws:" not in stripped.replace("ws:", "", 0) or "ws://" not in stripped


def test_asset_protocol_is_disabled(conf: dict) -> None:
    asset = conf["app"]["security"].get("asset_protocol") or {}
    assert asset.get("enable") is False
    assert not asset.get("scope")


def test_prototype_is_frozen(conf: dict) -> None:
    assert conf["app"]["security"].get("freeze_prototype") is True


def test_bundle_icons_are_all_real_files(conf: dict) -> None:
    icons_dir = CONF.parent / "icons"
    for entry in conf["bundle"]["icon"]:
        path = icons_dir.parent / entry
        assert path.is_file(), f"{entry} is listed but does not exist"
        assert path.stat().st_size > 0


#: `@` is legitimate in the conventional retina icon name; anything else that
#: looks like an address is not.
_LEGIT_AT_NAMES = {"128x128@2x.png"}


def test_no_email_address_in_icon_names(conf: dict) -> None:
    """A personal address was committed as an icon filename.

    `henry.w@example.net` was a real 256x256 PNG whose *filename* was somebody's
    email address — personal data in the repository, referenced from the bundle
    manifest. It is now `128x128@2x.png`, which is also the conventional name for
    that size.
    """
    for entry in conf["bundle"]["icon"]:
        name = entry.split("/", 1)[-1]
        if "@" in name:
            assert name in _LEGIT_AT_NAMES, f"{name} looks like an email address"
    for path in (CONF.parent / "icons").iterdir():
        if "@" in path.name:
            assert path.name in _LEGIT_AT_NAMES, f"{path.name} looks like an email address"


def test_node_modules_is_not_tracked() -> None:
    """`node_modules/` is in .gitignore but those files predate the rule.

    .gitignore does not untrack anything, so the Tauri CLI and its 40 MB platform
    binary stayed in the index until explicitly removed with `git rm --cached`.
    """
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", "--", "ui/desktop/node_modules"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    ).stdout.strip()
    assert out == "", f"node_modules is tracked again:\n{out}"


def test_icon_generator_matches_the_committed_names() -> None:
    """`gen_icons.py` is the source of truth for the icon set."""
    source = (REPO / "ui" / "desktop" / "gen_icons.py").read_text(encoding="utf-8")
    conf = json.loads(CONF.read_text(encoding="utf-8"))
    for entry in conf["bundle"]["icon"]:
        name = entry.split("/", 1)[-1]
        if name in {"icon.ico", "icon.icns"}:
            continue  # written by a separate branch of the script
        assert f'"{name}"' in source, f"{name} is bundled but gen_icons.py does not write it"
    assert "example.net" not in source, "generator still hardcodes an email filename"