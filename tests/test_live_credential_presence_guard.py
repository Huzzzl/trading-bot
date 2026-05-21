"""
Tests for src/tools/live_credential_presence_guard.py

Coverage:
- PASS when all required env vars present and non-empty
- BLOCKED when no required keys provided
- BLOCKED when a required env var is missing
- BLOCKED when a required env var is empty or whitespace-only
- Output JSON never contains actual secret values
- Stdout never contains actual secret values
- redacted_preview is always exactly "<redacted>" or null
- credentials_read is always False
- credential_values_exposed is always False
- broker_calls_made is always False
- live_submit_enabled is always False
- real_submit_implemented is always False
- submit_order_reachable is always False
- No Alpaca imports
- No network library imports
- No submit_order / cancel_order calls
- No ledger writes
- run_guard never raises
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from src.tools.live_credential_presence_guard import (
    _check_key,
    _is_valid_key_name,
    _INVALID_KEY_PLACEHOLDER,
    _REDACTED_PREVIEW,
    run_guard,
    main,
)

_KEY_A = "LCPG_TEST_KEY_A"
_KEY_B = "LCPG_TEST_KEY_B"


def _output_path(tmp_path: Path) -> Path:
    return tmp_path / "out" / "guard.json"


# ---------------------------------------------------------------------------
# _check_key
# ---------------------------------------------------------------------------

class TestCheckKey:
    def test_present_non_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_KEY_A, "supersecret")
        check, valid = _check_key(_KEY_A)
        assert valid is True
        assert check["key"] == _KEY_A
        assert check["present"] is True
        assert check["non_empty"] is True
        assert check["redacted_preview"] == _REDACTED_PREVIEW

    def test_present_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_KEY_A, "")
        check, valid = _check_key(_KEY_A)
        assert valid is True
        assert check["present"] is True
        assert check["non_empty"] is False
        assert check["redacted_preview"] is None

    def test_present_whitespace_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_KEY_A, "   ")
        check, valid = _check_key(_KEY_A)
        assert valid is True
        assert check["present"] is True
        assert check["non_empty"] is False
        assert check["redacted_preview"] is None

    def test_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_KEY_A, raising=False)
        check, valid = _check_key(_KEY_A)
        assert valid is True
        assert check["present"] is False
        assert check["non_empty"] is False
        assert check["redacted_preview"] is None

    def test_redacted_preview_never_contains_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "my-super-secret-key-value-12345"
        monkeypatch.setenv(_KEY_A, secret)
        check, _ = _check_key(_KEY_A)
        assert secret not in str(check["redacted_preview"])
        assert check["redacted_preview"] == "<redacted>"

    def test_redacted_preview_exact_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "any-value")
        check, _ = _check_key(_KEY_A)
        assert check["redacted_preview"] == "<redacted>"

    def test_invalid_key_returns_false(self) -> None:
        check, valid = _check_key("sk-live-abcdef123456")
        assert valid is False
        assert check["key"] == _INVALID_KEY_PLACEHOLDER
        assert check["present"] is False
        assert check["non_empty"] is False
        assert check["redacted_preview"] is None

    def test_invalid_key_not_passed_to_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Put a value under this exact string in environ to prove it's never read
        monkeypatch.setenv("sk-live-abcdef123456", "should-not-appear")
        check, valid = _check_key("sk-live-abcdef123456")
        assert valid is False
        assert "should-not-appear" not in str(check)


# ---------------------------------------------------------------------------
# _is_valid_key_name
# ---------------------------------------------------------------------------

class TestIsValidKeyName:
    @pytest.mark.parametrize("key", [
        "ALPACA_LIVE_API_KEY",
        "ALPACA_LIVE_SECRET_KEY",
        "MY_KEY",
        "A",
        "_PRIVATE",
        "KEY123",
        "A_B_C_1",
    ])
    def test_valid_names_accepted(self, key: str) -> None:
        assert _is_valid_key_name(key) is True

    @pytest.mark.parametrize("key", [
        "sk-live-abcdef123456",
        "Bearer token value",
        "abc123/secret+value",
        "lowercase_key",
        "123START",
        "",
        "KEY NAME",
        "KEY=VALUE",
        "KEY\nINJECT",
        "$KEY",
        "sk_live_1234abcd",
    ])
    def test_invalid_names_rejected(self, key: str) -> None:
        assert _is_valid_key_name(key) is False


# ---------------------------------------------------------------------------
# run_guard — PASS paths
# ---------------------------------------------------------------------------

class TestRunGuardPass:
    def test_single_key_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_KEY_A, "key-value")
        result = run_guard(required_keys=[_KEY_A])
        assert result["result"] == "PASS"
        assert result["violations"] == []
        assert result["blocker"] is None
        assert result["credentials_present"] is True

    def test_multiple_keys_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_KEY_A, "val-a")
        monkeypatch.setenv(_KEY_B, "val-b")
        result = run_guard(required_keys=[_KEY_A, _KEY_B])
        assert result["result"] == "PASS"
        assert result["credentials_present"] is True

    def test_checks_list_length_matches_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "v")
        monkeypatch.setenv(_KEY_B, "v")
        result = run_guard(required_keys=[_KEY_A, _KEY_B])
        assert len(result["checks"]) == 2

    def test_required_keys_preserved_in_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "v")
        result = run_guard(required_keys=[_KEY_A])
        assert result["required_keys"] == [_KEY_A]


# ---------------------------------------------------------------------------
# run_guard — BLOCKED paths
# ---------------------------------------------------------------------------

class TestRunGuardBlocked:
    def test_no_keys_provided(self) -> None:
        result = run_guard(required_keys=[])
        assert result["result"] == "BLOCKED"
        assert result["credentials_present"] is False
        assert result["blocker"] is not None
        assert "no required credential keys" in result["blocker"]

    def test_missing_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_KEY_A, raising=False)
        result = run_guard(required_keys=[_KEY_A])
        assert result["result"] == "BLOCKED"
        assert result["credentials_present"] is False
        assert any(_KEY_A in v for v in result["violations"])

    def test_empty_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_KEY_A, "")
        result = run_guard(required_keys=[_KEY_A])
        assert result["result"] == "BLOCKED"
        assert any(_KEY_A in v for v in result["violations"])

    def test_whitespace_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_KEY_A, "   \t  ")
        result = run_guard(required_keys=[_KEY_A])
        assert result["result"] == "BLOCKED"
        assert any(_KEY_A in v for v in result["violations"])

    def test_one_missing_one_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_KEY_A, "present-value")
        monkeypatch.delenv(_KEY_B, raising=False)
        result = run_guard(required_keys=[_KEY_A, _KEY_B])
        assert result["result"] == "BLOCKED"
        assert any(_KEY_B in v for v in result["violations"])

    def test_all_missing_all_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_KEY_A, raising=False)
        monkeypatch.delenv(_KEY_B, raising=False)
        result = run_guard(required_keys=[_KEY_A, _KEY_B])
        assert result["result"] == "BLOCKED"
        assert len(result["violations"]) == 2

    def test_blocker_is_first_violation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_KEY_A, raising=False)
        result = run_guard(required_keys=[_KEY_A])
        assert result["blocker"] == result["violations"][0]


# ---------------------------------------------------------------------------
# Invalid key name hardening
# ---------------------------------------------------------------------------

class TestInvalidKeyNames:
    @pytest.mark.parametrize("bad_key", [
        "sk-live-abcdef123456",
        "Bearer token value",
        "abc123/secret+value",
        "lowercase_key",
        "123_START",
        "KEY=VALUE",
        "KEY NAME",
    ])
    def test_invalid_key_is_blocked(self, bad_key: str) -> None:
        result = run_guard(required_keys=[bad_key])
        assert result["result"] == "BLOCKED"

    @pytest.mark.parametrize("bad_key", [
        "sk-live-abcdef123456",
        "Bearer token value",
        "abc123/secret+value",
    ])
    def test_raw_invalid_key_not_in_output_json(self, bad_key: str) -> None:
        result = run_guard(required_keys=[bad_key])
        serialised = json.dumps(result)
        assert bad_key not in serialised

    @pytest.mark.parametrize("bad_key", [
        "sk-live-abcdef123456",
        "Bearer token value",
        "abc123/secret+value",
    ])
    def test_raw_invalid_key_not_in_stdout(
        self, bad_key: str, capsys: pytest.CaptureFixture
    ) -> None:
        from src.tools.live_credential_presence_guard import print_guard
        result = run_guard(required_keys=[bad_key])
        print_guard(result)
        captured = capsys.readouterr()
        assert bad_key not in captured.out
        assert bad_key not in captured.err

    def test_placeholder_used_in_required_keys(self) -> None:
        result = run_guard(required_keys=["sk-live-abcdef"])
        assert _INVALID_KEY_PLACEHOLDER in result["required_keys"]
        assert "sk-live-abcdef" not in result["required_keys"]

    def test_placeholder_used_in_checks(self) -> None:
        result = run_guard(required_keys=["sk-live-abcdef"])
        assert result["checks"][0]["key"] == _INVALID_KEY_PLACEHOLDER

    def test_placeholder_used_in_violations(self) -> None:
        result = run_guard(required_keys=["sk-live-abcdef"])
        assert any(_INVALID_KEY_PLACEHOLDER in v for v in result["violations"])

    def test_valid_key_alongside_invalid_still_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "val")
        result = run_guard(required_keys=[_KEY_A, "sk-live-bad"])
        assert result["result"] == "BLOCKED"
        # Valid key must still appear correctly
        assert any(c["key"] == _KEY_A for c in result["checks"])
        # Invalid key must be placeholder in checks
        assert any(c["key"] == _INVALID_KEY_PLACEHOLDER for c in result["checks"])

    def test_environ_not_queried_for_invalid_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even if the exact bad_key string happened to be set in environ,
        # run_guard must not return it as present/non_empty
        bad_key = "sk-live-abcdef123456"
        monkeypatch.setenv(bad_key, "should-not-be-seen")
        result = run_guard(required_keys=[bad_key])
        assert result["result"] == "BLOCKED"
        check = result["checks"][0]
        assert check["present"] is False
        assert check["non_empty"] is False
        assert check["redacted_preview"] is None

    def test_main_invalid_key_writes_blocked_output(
        self, tmp_path: Path
    ) -> None:
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--required-env", "sk-live-bad", "--output", str(out)])
        assert exc_info.value.code == 1
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"
        assert "sk-live-bad" not in json.dumps(data)


# ---------------------------------------------------------------------------
# Output structure invariants
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def _pass_result(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        monkeypatch.setenv(_KEY_A, "val")
        return run_guard(required_keys=[_KEY_A])

    def test_required_fields_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = self._pass_result(monkeypatch)
        required = {
            "checked_at_utc", "result", "credentials_present",
            "credentials_read", "credential_values_exposed",
            "broker_calls_made", "live_submit_enabled",
            "real_submit_implemented", "submit_order_reachable",
            "required_keys", "checks", "violations", "blocker",
        }
        assert required.issubset(result.keys())

    def test_credentials_read_always_false_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._pass_result(monkeypatch)
        assert result["credentials_read"] is False

    def test_credentials_read_always_false_blocked(self) -> None:
        result = run_guard(required_keys=[])
        assert result["credentials_read"] is False

    def test_credential_values_exposed_always_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._pass_result(monkeypatch)
        assert result["credential_values_exposed"] is False

    def test_broker_calls_made_always_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._pass_result(monkeypatch)
        assert result["broker_calls_made"] is False

    def test_live_submit_enabled_always_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._pass_result(monkeypatch)
        assert result["live_submit_enabled"] is False

    def test_real_submit_implemented_always_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._pass_result(monkeypatch)
        assert result["real_submit_implemented"] is False

    def test_submit_order_reachable_always_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._pass_result(monkeypatch)
        assert result["submit_order_reachable"] is False

    def test_all_invariants_on_blocked(self) -> None:
        result = run_guard(required_keys=[])
        assert result["credentials_read"] is False
        assert result["credential_values_exposed"] is False
        assert result["broker_calls_made"] is False
        assert result["live_submit_enabled"] is False
        assert result["real_submit_implemented"] is False
        assert result["submit_order_reachable"] is False

    def test_violations_is_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = self._pass_result(monkeypatch)
        assert isinstance(result["violations"], list)

    def test_checks_is_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = self._pass_result(monkeypatch)
        assert isinstance(result["checks"], list)

    def test_check_dict_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_KEY_A, "val")
        result = run_guard(required_keys=[_KEY_A])
        check = result["checks"][0]
        assert set(check.keys()) == {"key", "present", "non_empty", "redacted_preview"}

    def test_redacted_preview_exact_literal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "real-secret-value-xyz")
        result = run_guard(required_keys=[_KEY_A])
        assert result["checks"][0]["redacted_preview"] == "<redacted>"

    def test_redacted_preview_null_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_KEY_A, raising=False)
        result = run_guard(required_keys=[_KEY_A])
        assert result["checks"][0]["redacted_preview"] is None


# ---------------------------------------------------------------------------
# Secret value never exposed in output JSON
# ---------------------------------------------------------------------------

class TestSecretNotExposed:
    _SECRET = "SUPER_SECRET_DO_NOT_EXPOSE_XYZ_12345"

    def test_secret_not_in_output_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, self._SECRET)
        result = run_guard(required_keys=[_KEY_A])
        serialised = json.dumps(result)
        assert self._SECRET not in serialised

    def test_secret_not_in_stdout(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv(_KEY_A, self._SECRET)
        run_guard(required_keys=[_KEY_A])
        # Also exercise the printer
        from src.tools.live_credential_presence_guard import print_guard
        result = run_guard(required_keys=[_KEY_A])
        print_guard(result)
        captured = capsys.readouterr()
        assert self._SECRET not in captured.out
        assert self._SECRET not in captured.err

    def test_secret_not_in_written_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, self._SECRET)
        out = _output_path(tmp_path)
        monkeypatch.setenv(_KEY_B, self._SECRET)
        try:
            main([
                "--required-env", _KEY_A,
                "--required-env", _KEY_B,
                "--output", str(out),
            ])
        except SystemExit:
            pass
        content = out.read_text()
        assert self._SECRET not in content

    def test_redacted_preview_is_fixed_literal_not_derived(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for secret in ["abc", "XYZ789", "sk-live-abcdefg", "Bearer token"]:
            monkeypatch.setenv(_KEY_A, secret)
            check, _ = _check_key(_KEY_A)
            assert check["redacted_preview"] == "<redacted>"
            assert secret not in check["redacted_preview"]


# ---------------------------------------------------------------------------
# Exit codes via main()
# ---------------------------------------------------------------------------

class TestExitCodes:
    def test_pass_returns_normally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "val")
        out = _output_path(tmp_path)
        main(["--required-env", _KEY_A, "--output", str(out)])

    def test_blocked_exits_one_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_KEY_A, raising=False)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--required-env", _KEY_A, "--output", str(out)])
        assert exc_info.value.code == 1

    def test_blocked_exits_one_no_keys(self, tmp_path: Path) -> None:
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--output", str(out)])
        assert exc_info.value.code == 1

    def test_always_writes_output_on_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "val")
        out = _output_path(tmp_path)
        main(["--required-env", _KEY_A, "--output", str(out)])
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["result"] == "PASS"

    def test_always_writes_output_on_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_KEY_A, raising=False)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit):
            main(["--required-env", _KEY_A, "--output", str(out)])
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"

    def test_output_dir_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "val")
        out = tmp_path / "nested" / "deep" / "guard.json"
        main(["--required-env", _KEY_A, "--output", str(out)])
        assert out.exists()

    def test_output_json_fields_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "val")
        out = _output_path(tmp_path)
        main(["--required-env", _KEY_A, "--output", str(out)])
        data = json.loads(out.read_text())
        for field in (
            "checked_at_utc", "result", "credentials_present",
            "credentials_read", "credential_values_exposed",
            "broker_calls_made", "live_submit_enabled",
            "real_submit_implemented", "submit_order_reachable",
            "required_keys", "checks", "violations", "blocker",
        ):
            assert field in data, f"missing field: {field}"

    def test_multiple_required_env_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "val-a")
        monkeypatch.setenv(_KEY_B, "val-b")
        out = _output_path(tmp_path)
        main([
            "--required-env", _KEY_A,
            "--required-env", _KEY_B,
            "--output", str(out),
        ])
        data = json.loads(out.read_text())
        assert data["result"] == "PASS"
        assert len(data["checks"]) == 2


# ---------------------------------------------------------------------------
# Security properties
# ---------------------------------------------------------------------------

class TestSecurityProperties:
    def test_no_alpaca_import(self) -> None:
        import src.tools.live_credential_presence_guard as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "alpaca" not in line.lower(), (
                f"alpaca found in import: {line!r}"
            )

    def test_no_network_library_imports(self) -> None:
        import src.tools.live_credential_presence_guard as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            for lib in ("requests", "httpx", "aiohttp", "urllib.request"):
                assert lib not in line, (
                    f"network library {lib!r} found in import: {line!r}"
                )

    def test_no_submit_order_call(self) -> None:
        import src.tools.live_credential_presence_guard as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith(("#", '"""', "*"))
        ]
        for line in code_lines:
            assert "submit_order(" not in line, (
                f"submit_order( call found: {line!r}"
            )

    def test_no_cancel_order_call(self) -> None:
        import src.tools.live_credential_presence_guard as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith(("#", '"""', "*"))
        ]
        for line in code_lines:
            assert "cancel_order(" not in line, (
                f"cancel_order( call found: {line!r}"
            )

    def test_no_ledger_write(self) -> None:
        import src.tools.live_credential_presence_guard as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith(("#", '"""', "*"))
        ]
        for line in code_lines:
            assert "append_live_ledger_row" not in line, (
                f"ledger write call found: {line!r}"
            )

    def test_no_hardcoded_credential_env_lookups(self) -> None:
        # The tool must accept credential key names only via CLI args.
        # It must not contain os.environ.get("ALPACA_...") hardcoded calls.
        import src.tools.live_credential_presence_guard as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        for cred in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY"):
            # A hardcoded lookup would appear as os.environ.get("CRED") or
            # os.environ["CRED"] — check for the quoted form in non-comment lines
            code_lines = [
                line for line in source.splitlines()
                if not line.strip().startswith(("#", '"""', "'''", "*"))
                and not line.strip().startswith(("--", "python", "output/"))
            ]
            for line in code_lines:
                assert f'"{cred}"' not in line and f"'{cred}'" not in line, (
                    f"hardcoded credential string {cred!r} found in: {line!r}"
                )

    def test_run_guard_never_raises(self) -> None:
        for keys in [[], ["NONEXISTENT_KEY_XYZ"], [""]]:
            result = run_guard(required_keys=keys)
            assert "result" in result

    def test_credentials_read_always_false_in_all_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "val")
        for keys in [[], [_KEY_A], ["MISSING_XYZ"]]:
            result = run_guard(required_keys=keys)
            assert result["credentials_read"] is False

    def test_credential_values_exposed_always_false_in_all_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_KEY_A, "val")
        for keys in [[], [_KEY_A], ["MISSING_XYZ"]]:
            result = run_guard(required_keys=keys)
            assert result["credential_values_exposed"] is False

    def test_redacted_preview_constant(self) -> None:
        assert _REDACTED_PREVIEW == "<redacted>"
