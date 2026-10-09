"""Scoped bootstrap regressions: synthetic credentials and mocked HTTP only."""

import json
from contextlib import contextmanager

import pytest
import responses
from responses import matchers

from agent.secret_sources.base import (
    ErrorKind,
    get_source_environment,
    reset_source_environment,
    set_source_environment,
)
from agent.secret_sources.registry import (
    _reset_registry_for_tests,
    apply_all,
    register_source,
)

from infisical_plugin.auth import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    STATIC_TOKEN_ENV,
    resolve_token,
)
from infisical_plugin.errors import InfisicalError
from infisical_plugin.sources import InfisicalBulkSource, InfisicalSource

HOST = "https://infisical.example.com"
LOGIN_URL = f"{HOST}/api/v1/auth/universal-auth/login"
LAUNCH = {
    STATIC_TOKEN_ENV: "synthetic-launch-token",
    CLIENT_ID_ENV: "synthetic-launch-id",
    CLIENT_SECRET_ENV: "synthetic-launch-secret",
}
SCOPED = {
    STATIC_TOKEN_ENV: "synthetic-scoped-token",
    CLIENT_ID_ENV: "synthetic-scoped-id",
    CLIENT_SECRET_ENV: "synthetic-scoped-secret",
}
LOGIN_BODY = {
    "clientId": SCOPED[CLIENT_ID_ENV],
    "clientSecret": SCOPED[CLIENT_SECRET_ENV],
}


@contextmanager
def source_environment(environ):
    token = set_source_environment(environ)
    try:
        yield
    finally:
        reset_source_environment(token)


@pytest.fixture(autouse=True)
def synthetic_launch_environment(monkeypatch):
    for name, value in LAUNCH.items():
        monkeypatch.setenv(name, value)


def test_static_token_uses_scoped_view_and_trims_whitespace():
    with source_environment({STATIC_TOKEN_ENV: "  synthetic-scoped-token\n"}):
        assert resolve_token({"method": "token"}, HOST) == SCOPED[STATIC_TOKEN_ENV]
    assert resolve_token({"method": "token"}, HOST) == LAUNCH[STATIC_TOKEN_ENV]


@responses.activate
def test_universal_auth_posts_exact_scoped_credentials():
    responses.post(LOGIN_URL, json={"accessToken": "synthetic-login-token"})
    with source_environment({name: f"  {value}\n" for name, value in SCOPED.items()}):
        assert resolve_token({"method": "universal-auth"}, HOST) == "synthetic-login-token"
    assert len(responses.calls) == 1
    assert json.loads(responses.calls[0].request.body) == LOGIN_BODY


@pytest.mark.parametrize("environ", [{}, {"UNRELATED": "value"}, {STATIC_TOKEN_ENV: " \n"}])
def test_missing_scoped_token_never_falls_back_to_launch(environ):
    with source_environment(environ), pytest.raises(InfisicalError) as exc:
        resolve_token({"method": "token"}, HOST)
    assert exc.value.kind is ErrorKind.NOT_CONFIGURED
    assert not any(value in str(exc.value) for value in LAUNCH.values())


@responses.activate
@pytest.mark.parametrize("environ", [
    {},
    {"UNRELATED": "value"},
    {CLIENT_ID_ENV: SCOPED[CLIENT_ID_ENV]},
    {CLIENT_SECRET_ENV: SCOPED[CLIENT_SECRET_ENV]},
    {CLIENT_ID_ENV: " \n", CLIENT_SECRET_ENV: SCOPED[CLIENT_SECRET_ENV]},
    {CLIENT_ID_ENV: SCOPED[CLIENT_ID_ENV], CLIENT_SECRET_ENV: " \n"},
])
def test_missing_scoped_credentials_fail_before_login(environ):
    # If a regression authenticates with launch credentials, it still cannot
    # reach a live endpoint; the unexpected mocked request is asserted below.
    responses.post(LOGIN_URL, json={"accessToken": "synthetic-unexpected-token"})
    with source_environment(environ), pytest.raises(InfisicalError) as exc:
        resolve_token({"method": "universal-auth"}, HOST)
    assert exc.value.kind is ErrorKind.NOT_CONFIGURED
    assert len(responses.calls) == 0
    assert not any(value in str(exc.value) for value in LAUNCH.values())


@responses.activate
@pytest.mark.parametrize("environ", [{}, {CLIENT_ID_ENV: " \n", CLIENT_SECRET_ENV: ""}])
def test_scoped_missing_credentials_still_use_configured_files(tmp_path, environ):
    id_path = tmp_path / "client-id"
    secret_path = tmp_path / "client-secret"
    id_path.write_text("synthetic-file-id\n", encoding="utf-8")
    secret_path.write_text("synthetic-file-secret\n", encoding="utf-8")
    responses.post(LOGIN_URL, json={"accessToken": "synthetic-file-token"})
    cfg = {
        "method": "universal-auth",
        "client_id_path": str(id_path),
        "client_secret_path": str(secret_path),
    }
    with source_environment(environ):
        assert resolve_token(cfg, HOST) == "synthetic-file-token"
    assert json.loads(responses.calls[0].request.body) == {
        "clientId": "synthetic-file-id", "clientSecret": "synthetic-file-secret",
    }


@responses.activate
def test_scoped_credentials_take_precedence_over_files(tmp_path):
    cfg = {
        "method": "universal-auth",
        "client_id_path": str(tmp_path / "missing-id"),
        "client_secret_path": str(tmp_path / "missing-secret"),
    }
    responses.post(LOGIN_URL, json={"accessToken": "synthetic-login-token"})
    with source_environment(SCOPED.copy()):
        assert resolve_token(cfg, HOST) == "synthetic-login-token"
    assert json.loads(responses.calls[0].request.body) == LOGIN_BODY


@responses.activate
def test_unscoped_credentials_keep_process_environment_behavior():
    responses.post(LOGIN_URL, json={"accessToken": "synthetic-launch-login-token"})
    assert resolve_token({"method": "token"}, HOST) == LAUNCH[STATIC_TOKEN_ENV]
    assert resolve_token({"method": "universal-auth"}, HOST) == "synthetic-launch-login-token"
    assert json.loads(responses.calls[0].request.body) == {
        "clientId": LAUNCH[CLIENT_ID_ENV], "clientSecret": LAUNCH[CLIENT_SECRET_ENV],
    }


@pytest.fixture
def registry(monkeypatch):
    _reset_registry_for_tests()
    monkeypatch.setattr("agent.secret_sources.registry._ensure_builtin_sources", lambda: None)
    assert register_source(InfisicalSource())
    assert register_source(InfisicalBulkSource())
    yield
    _reset_registry_for_tests()


def registry_config(method):
    common = {"enabled": True, "host_url": HOST, "auth": {"method": method}}
    return {
        "profile_alias": False,
        "infisical": {**common, "env": {"MAPPED_KEY": "inf://p/prod/MAPPED_KEY"}},
        "infisical_bulk": {**common, "project": "p", "environment": "prod"},
    }


def mock_secret_endpoints(token):
    headers = [matchers.header_matcher({"Authorization": f"Bearer {token}"})]
    responses.get(
        f"{HOST}/api/v1/projects",
        json={"projects": [{"id": "id-1", "name": "P", "slug": "p"}]},
        match=headers,
    )
    responses.get(
        f"{HOST}/api/v4/secrets/MAPPED_KEY",
        json={"secret": {"secretValue": "synthetic-mapped-value"}}, match=headers,
    )
    responses.get(
        f"{HOST}/api/v4/secrets",
        json={"secrets": [{"secretKey": "BULK_KEY", "secretValue": "synthetic-bulk-value"}],
              "imports": []}, match=headers,
    )


@responses.activate
@pytest.mark.parametrize("method", ["token", "universal-auth"])
def test_registry_worker_uses_scoped_auth_for_mapped_and_bulk(registry, tmp_path, method):
    if method == "universal-auth":
        responses.post(
            LOGIN_URL, json={"accessToken": "synthetic-login-token"},
            match=[matchers.json_params_matcher(LOGIN_BODY)],
        )
        token = "synthetic-login-token"
    else:
        token = SCOPED[STATIC_TOKEN_ENV]
    mock_secret_endpoints(token)
    environ = SCOPED.copy()
    report = apply_all(registry_config(method), tmp_path, environ=environ)
    assert len(report.sources) == 2
    assert all(sr.result.ok for sr in report.sources)
    assert environ == {
        **SCOPED, "MAPPED_KEY": "synthetic-mapped-value", "BULK_KEY": "synthetic-bulk-value",
    }
    assert {sr.name for sr in report.sources} == {"infisical", "infisical_bulk"}
    assert len([call for call in responses.calls if call.request.method == "POST"]) == (
        2 if method == "universal-auth" else 0
    )
    # The worker's ContextVar must not replace the caller's environment view.
    assert get_source_environment().get(STATIC_TOKEN_ENV) == LAUNCH[STATIC_TOKEN_ENV]


@responses.activate
@pytest.mark.parametrize("method", ["token", "universal-auth"])
@pytest.mark.parametrize("environ", [{}, {"UNRELATED": "value"}])
def test_registry_worker_missing_scoped_auth_is_not_configured(registry, tmp_path, method, environ):
    environ = environ.copy()
    before = environ.copy()
    report = apply_all(registry_config(method), tmp_path, environ=environ)
    assert len(report.sources) == 2
    for sr in report.sources:
        assert sr.result.error_kind is ErrorKind.NOT_CONFIGURED
        assert sr.result.secrets == {}
        assert sr.applied == []
        assert sr.result.error
        assert not any(value in sr.result.error for value in LAUNCH.values())
    assert environ == before
    assert len(responses.calls) == 0
