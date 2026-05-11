# Fix: Gitea provider returns str instead of bytes, causing repo settings loading failure

## Bug Description

When using pr-agent with a **Gitea** provider, the repository-level configuration file (`.pr_agent.toml`) fails to load due to a type mismatch. The Gitea provider's `get_file_content()` returns a `str`, but `utils.py` expects `bytes` — unlike the GitHub provider which returns `bytes`.

This causes two cascading errors:
1. `os.write()` fails with `"a bytes-like object is required, not 'str'"`
2. Error handler crashes with `"'str' object has no attribute 'decode'"`

As a result, repo-level settings (model, response_language, etc.) are never applied, and pr-agent falls back to global defaults.

## Steps to Reproduce

1. Configure pr-agent with `git_provider = "gitea"` and a local Gitea instance
2. Push a `.pr_agent.toml` config file to the Gitea repository
3. Run any pr-agent command, e.g.: `pr-agent --pr_url="http://gitea-host/owner/repo/pulls/1" review`

## Expected Behavior

The `.pr_agent.toml` file should be loaded and its settings should override global defaults.

## Actual Behavior

Settings loading fails silently (then crashes the error handler), and global defaults are used instead.

## Error Logs

```
WARNING  | Failed to apply repo local settings, error: a bytes-like object is required, not 'str'
ERROR    | Failed to handle configurations errors
Traceback (most recent call last):
  ...
  File "pr_agent/git_providers/utils.py", line 38, in apply_repo_settings
    os.write(fd, repo_settings)
TypeError: a bytes-like object is required, not 'str'

  File "pr_agent/git_providers/utils.py", line 100, in handle_configurations_errors
    configuration_file_content = err['settings'].decode()
AttributeError: 'str' object has no attribute 'decode'
```

## Root Cause

In `pr_agent/git_providers/utils.py`:

**Line 38** — `os.write(fd, repo_settings)` expects `bytes`, but Gitea's `get_file_content()` returns `str`.

**Line 100** — `err['settings'].decode()` assumes `bytes`, but when Gitea is the provider, the value is already a `str`.

The GitHub provider returns `bytes` from its file content API, so this issue only affects Gitea.

## Suggested Fix

**File:** `pr_agent/git_providers/utils.py`

**Line 38:**
```python
# Before:
os.write(fd, repo_settings)

# After:
os.write(fd, repo_settings if isinstance(repo_settings, bytes) else repo_settings.encode())
```

**Line 100:**
```python
# Before:
configuration_file_content = err['settings'].decode()

# After:
configuration_file_content = err['settings'].decode() if isinstance(err['settings'], bytes) else err['settings']
```

## Environment

- pr-agent version: v0.3.1 (branch: gitea-pr-agent, commit: bc1d2362)
- Git provider: Gitea (self-hosted)
- Python: 3.12
- OS: Windows 10
