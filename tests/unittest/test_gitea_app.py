from unittest.mock import MagicMock, patch


def test_construct_pr_url_fallback():
    from pr_agent.servers import gitea_app

    body = {
        "repository": {"full_name": "acme/widgets"},
        "issue": {"number": 12},
    }

    with patch.object(gitea_app, "get_settings") as mock_get_settings:
        settings = MagicMock()
        settings.get.side_effect = lambda key, default=None: {
            "GITEA.URL": "https://gitea.example.com"
        }.get(key, default)
        mock_get_settings.return_value = settings

        assert gitea_app._construct_pr_url(body) == "https://gitea.example.com/api/v1/repos/acme/widgets/pulls/12"


def test_handle_line_comments_converts_ask_to_ask_line():
    from pr_agent.servers import gitea_app

    settings = MagicMock()
    body = {
        "comment": {
            "id": 10,
            "path": "src/app.py",
            "line": 42,
            "diff_hunk": "@@ -1 +1 @@",
        }
    }

    with patch.object(gitea_app, "get_settings", return_value=settings):
        result = gitea_app.handle_line_comments(body, "/ask why")

    assert result == "/ask_line --line_start=42 --line_end=42 --side=RIGHT --file_name=src/app.py --comment_id=10 why"
    settings.set.assert_called_once_with("ask_diff_hunk", "@@ -1 +1 @@")


def test_check_pull_request_event_filters_draft_pr():
    from pr_agent.servers import gitea_app

    body = {
        "pull_request": {
            "url": "https://gitea.example.com/api/v1/repos/acme/widgets/pulls/1",
            "draft": True,
            "state": "open",
        }
    }

    assert gitea_app._check_pull_request_event("opened", body, {}) == ({}, "")


def test_check_pull_request_event_accepts_open_pr():
    from pr_agent.servers import gitea_app

    pr = {
        "url": "https://gitea.example.com/api/v1/repos/acme/widgets/pulls/1",
        "state": "open",
    }
    log_context = {}

    assert gitea_app._check_pull_request_event("opened", {"pull_request": pr}, log_context) == (pr, pr["url"])
    assert log_context["api_url"] == pr["url"]


def test_is_bot_user_ignores_non_pr_agent_bot():
    from pr_agent.servers import gitea_app

    with patch.object(gitea_app, "get_settings") as mock_get_settings:
        settings = MagicMock()
        settings.get.side_effect = lambda key, default=None: default
        mock_get_settings.return_value = settings

        assert gitea_app.is_bot_user("dependabot", "Bot") is True
        assert gitea_app.is_bot_user("pr-agent", "Bot") is False
