from pathlib import Path

import pytest


def test_systemd_uses_overridable_persistent_data_directory() -> None:
    unit = Path("contrib/systemd/mafia-api.service").read_text()

    assert "Environment=MAFIA_DATA_DIR=/var/lib/mafia" in unit
    assert unit.index("Environment=MAFIA_DATA_DIR=/var/lib/mafia") < unit.index(
        "EnvironmentFile=/etc/mafia/mafia.env"
    )


def test_deployment_documents_environment_file_override() -> None:
    deployment = Path("docs/deployment.md").read_text()

    assert "`/etc/mafia/mafia.env` can override that default" in deployment


@pytest.mark.parametrize(
    "path",
    [
        "contrib/incus/personal.env.example",
        "contrib/incus/frostyard.env.example",
    ],
)
def test_incus_examples_do_not_advertise_ignored_execution_mode(path: str) -> None:
    example = Path(path).read_text()

    assert "MAFIA_EXECUTION_MODE" not in example
    assert "configured per project" in example


def test_release_script_removes_python_caches_after_copying_migrations() -> None:
    script = Path("scripts/build-release.sh").read_text()
    migrations_copy = 'cp -a apps/api/migrations "$staging/$release_name/apps/api/migrations"'
    cache_directory_removal = (
        'find "$staging/$release_name/apps/api/migrations" -type d '
        '-name __pycache__ -prune -exec rm -rf {} +'
    )
    bytecode_removal = (
        'find "$staging/$release_name/apps/api/migrations" -type f '
        "\\( -name '*.pyc' -o -name '*.pyo' \\) -delete"
    )

    assert migrations_copy in script
    assert cache_directory_removal in script
    assert bytecode_removal in script
    assert script.index(migrations_copy) < script.index(cache_directory_removal)
    assert script.index(cache_directory_removal) < script.index(bytecode_removal)


def test_release_includes_an_executable_reset_command() -> None:
    reset_script = Path("packaging/bin/reset-data")
    release_script = Path("scripts/build-release.sh").read_text()

    assert reset_script.is_file()
    assert reset_script.stat().st_mode & 0o111
    assert 'cp -a packaging/bin/. "$staging/$release_name/bin/"' in release_script
    assert 'chmod +x "$staging/$release_name"/bin/*' in release_script


def test_deployment_assets_do_not_configure_removed_agent_transports() -> None:
    environment = Path(".env.example").read_text()
    caddyfile = Path("contrib/Caddyfile").read_text()

    assert "AGENT_URL" not in environment
    assert "COPILOTKIT" not in environment
    assert "/ag-ui" not in caddyfile
    assert "copilotkit" not in caddyfile.lower()
    assert "flush_interval" not in caddyfile
    assert "@api path /api/* /readyz" in caddyfile
