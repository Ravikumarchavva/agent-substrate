"""`substrate up`/`substrate down` — the packaged local-dev-infra compose
file. Real, found-not-assumed motivation: `start_all_main` (a shipped
entry point, `start-all` in pyproject.toml's [project.scripts]) used to
shell out to `make infra-up-all`. `make` and the Makefile are never part
of the built wheel (verified this session: `uv build --wheel` then
`unzip -l` lists only `substrate/*`), so that call crashed for anyone who
`pip install`/`uv add`-ed agent-substrate rather than cloned its repo --
exactly the "child implementation project" case this exists for.

These tests check the packaged compose file directly rather than actually
running `docker compose up` (no Docker dependency in the test suite) --
the real end-to-end proof (building the wheel, installing it in an
isolated venv outside this repo, and running `substrate up`/`down` for
real against Docker) was done manually this session; this locks in the
part of that proof that doesn't need Docker to verify on every run.
"""

from __future__ import annotations

import yaml

from substrate.cli import _INFRA_SERVICES, _infra_compose_path


def test_packaged_compose_file_exists_and_is_valid_yaml() -> None:
    compose_file = _infra_compose_path()
    assert compose_file.exists(), (
        f"{compose_file} is missing -- substrate up/down and start-all "
        "would crash for anyone who installed the built package"
    )
    doc = yaml.safe_load(compose_file.read_text())
    assert "services" in doc


def test_every_infra_service_cmd_up_starts_is_actually_defined() -> None:
    """_INFRA_SERVICES is the exact service list `substrate up`/`down`
    pass to docker compose -- if one of these names doesn't exist in the
    compose file, `docker compose up -d <name>` fails outright."""
    doc = yaml.safe_load(_infra_compose_path().read_text())
    defined = set(doc["services"])
    for name in _INFRA_SERVICES:
        assert name in defined, f"{name!r} is in _INFRA_SERVICES but not in services:"


def test_every_relative_bind_mount_and_build_context_resolves_on_disk() -> None:
    """The compose file bind-mounts sibling config files and builds
    mcp-server from a sibling directory, all by relative path. Real bug
    class this catches: package data files that don't actually ship in
    the wheel (uv_build silently omits nothing under src/substrate/, but a
    future edit to the compose file referencing a new file that was never
    copied into the packaged deployment/docker/ dir would break `substrate
    up` for a real user with no signal until they hit it)."""
    compose_file = _infra_compose_path()
    base = compose_file.parent
    doc = yaml.safe_load(compose_file.read_text())

    for name, svc in doc["services"].items():
        for mount in svc.get("volumes", []):
            src = mount.split(":", 1)[0]
            if src.startswith("./"):
                assert (base / src).exists(), (
                    f"{name}: bind mount source {src!r} does not exist under "
                    f"the packaged compose dir"
                )
        build = svc.get("build")
        if isinstance(build, dict) and build.get("context", "").startswith("."):
            ctx = base / build["context"]
            assert ctx.is_dir(), f"{name}: build context {ctx} does not exist"
            dockerfile = ctx / build.get("dockerfile", "Dockerfile")
            assert dockerfile.exists(), f"{name}: {dockerfile} does not exist"


def test_cmd_start_all_flag_no_longer_shells_out_to_make() -> None:
    """Regression guard for the actual bug: the old separate `start-all`
    console script (start_all_main, since consolidated into `substrate
    start --all`) used to run subprocess.run(["make", "infra-up-all"], ...)
    -- never available in an installed (non-cloned) package. Checks the
    source directly since actually exercising --all would require a live
    Docker daemon."""
    import inspect

    from substrate.cli import cmd_start

    src = inspect.getsource(cmd_start)
    assert '"make"' not in src
    assert "cmd_up" in src


def test_start_and_start_all_scripts_are_gone_only_substrate_remains() -> None:
    """The `start`/`start-all` console-script entry points were removed --
    everything now goes through `substrate <command>` (`substrate start`,
    `substrate start --all`), so pyproject.toml should declare exactly one
    script."""
    import tomllib

    with open("pyproject.toml", "rb") as f:
        scripts = tomllib.load(f)["project"]["scripts"]
    assert scripts == {"substrate": "substrate.cli:main"}
