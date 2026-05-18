from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest


class TestGiteaProvider:
    @patch('pr_agent.git_providers.gitea_provider.get_settings')
    @patch('pr_agent.git_providers.gitea_provider.giteapy.ApiClient')
    def test_gitea_provider_auth_header(self, mock_api_client_cls, mock_get_settings):
        settings = MagicMock()
        settings.get.side_effect = lambda k, d=None: {
            'GITEA.URL': 'https://gitea.example.com',
            'GITEA.PERSONAL_ACCESS_TOKEN': 'test-token',
            'GITEA.REPO_SETTING': None,
            'GITEA.SKIP_SSL_VERIFICATION': False,
            'GITEA.SSL_CA_CERT': None
        }.get(k, d)
        mock_get_settings.return_value = settings

        mock_api_client = mock_api_client_cls.return_value
        mock_api_client.configuration.api_key = {'Authorization': 'token test-token'}

        def call_api_side_effect(path, method, **kwargs):
            mock_resp = MagicMock()
            if 'files' in path:
                mock_resp.data = BytesIO(b'[]')
                return mock_resp
            if 'commits' in path:
                mock_resp.data = BytesIO(b'[]')
                return mock_resp

            mock_resp.data = BytesIO(b'{}')
            return mock_resp

        mock_api_client.call_api.side_effect = call_api_side_effect

        from pr_agent.git_providers.gitea_provider import RepoApi

        client = mock_api_client
        repo_api = RepoApi(client)

        mock_api_client.reset_mock()
        mock_resp = MagicMock()
        mock_resp.data = BytesIO(b'[]')
        mock_api_client.call_api.return_value = mock_resp

        repo_api.get_change_file_pull_request('owner', 'repo', 123)

        args, kwargs = mock_api_client.call_api.call_args
        assert '/repos/owner/repo/pulls/123/files' in args[0]
        assert kwargs.get('auth_settings') == ['AuthorizationHeaderToken']
        assert 'token=' not in args[0]

        mock_api_client.reset_mock()
        mock_resp = MagicMock()
        mock_resp.data = BytesIO(b'diff content')
        mock_api_client.call_api.return_value = mock_resp

        repo_api.get_pull_request_diff('owner', 'repo', 123)

        args, kwargs = mock_api_client.call_api.call_args
        assert args[0] == '/repos/owner/repo/pulls/123.diff'
        assert kwargs.get('auth_settings') == ['AuthorizationHeaderToken']

        mock_api_client.reset_mock()
        mock_resp.data = BytesIO(b'{"Python": 100}')
        mock_api_client.call_api.return_value = mock_resp

        repo_api.get_languages('owner', 'repo')

        args, kwargs = mock_api_client.call_api.call_args
        assert args[0] == '/repos/owner/repo/languages'
        assert kwargs.get('auth_settings') == ['AuthorizationHeaderToken']

        mock_api_client.reset_mock()
        mock_resp.data = BytesIO(b'content')
        mock_api_client.call_api.return_value = mock_resp

        repo_api.get_file_content('owner', 'repo', 'sha1', 'file.txt')

        args, kwargs = mock_api_client.call_api.call_args
        assert args[0] == '/repos/owner/repo/raw/file.txt'
        assert kwargs.get('query_params') == [('ref', 'sha1')]
        assert kwargs.get('auth_settings') == ['AuthorizationHeaderToken']

        mock_api_client.reset_mock()
        mock_resp.data = BytesIO(b'[]')
        mock_api_client.call_api.return_value = mock_resp

        repo_api.get_pr_commits('owner', 'repo', 123)

        args, kwargs = mock_api_client.call_api.call_args
        assert args[0] == '/repos/owner/repo/pulls/123/commits'
        assert kwargs.get('auth_settings') == ['AuthorizationHeaderToken']

    @pytest.fixture
    def repo_api(self):
        from pr_agent.git_providers.gitea_provider import RepoApi

        client = MagicMock()
        return RepoApi(client)

    def test_get_pull_request_diff_decodes_gbk_content(self, repo_api):
        content = "diff --git a/demo.java b/demo.java\n@@ -1 +1 @@\n-// 中文注释\n+// 中文注释已修改"
        mock_resp = MagicMock()
        mock_resp.data = BytesIO(content.encode("gbk"))
        repo_api.api_client.call_api.return_value = mock_resp

        result = repo_api.get_pull_request_diff("owner", "repo", 123)

        assert result == content

    def test_get_file_content_decodes_gbk_content(self, repo_api):
        content = "public class Demo {\n    // 中文注释：GBK编码\n}"
        mock_resp = MagicMock()
        mock_resp.data = BytesIO(content.encode("gbk"))
        repo_api.api_client.call_api.return_value = mock_resp

        result = repo_api.get_file_content("owner", "repo", "sha1", "Demo.java")

        assert result == content

    def test_get_change_file_pull_request_keeps_utf8_json_decoding(self, repo_api):
        content = '[{"filename": "中文.java"}]'
        mock_resp = MagicMock()
        mock_resp.data = BytesIO(content.encode("utf-8"))
        repo_api.api_client.call_api.return_value = mock_resp

        result = repo_api.get_change_file_pull_request("owner", "repo", 123)

        assert result == [{"filename": "中文.java"}]

    def test_get_comment_body_from_comment_id(self):
        from pr_agent.git_providers.gitea_provider import GiteaProvider

        provider = object.__new__(GiteaProvider)
        provider.owner = "owner"
        provider.repo = "repo"
        provider.max_comment_chars = 65000
        provider.logger = MagicMock()
        provider.repo_api = MagicMock()
        provider.repo_api.get_comment.return_value = MagicMock(body="hello")

        assert provider.get_comment_body_from_comment_id(10) == "hello"
        provider.repo_api.get_comment.assert_called_once_with(owner="owner", repo="repo", comment_id=10)

    def test_remove_reaction_accepts_reaction_id(self):
        from pr_agent.git_providers.gitea_provider import GiteaProvider

        provider = object.__new__(GiteaProvider)
        provider.owner = "owner"
        provider.repo = "repo"
        provider.logger = MagicMock()
        provider.repo_api = MagicMock()
        provider.repo_api.remove_reaction_comment.return_value = True

        assert provider.remove_reaction(11, 22) is True
        provider.repo_api.remove_reaction_comment.assert_called_once_with(
            owner="owner", repo="repo", comment_id=11, reaction_id=22
        )

    def test_get_canonical_url_parts_from_git_url(self):
        from pr_agent.git_providers.gitea_provider import GiteaProvider

        provider = object.__new__(GiteaProvider)
        provider.owner = None
        provider.repo = None
        provider.base_url = "https://gitea.example.com"
        provider.logger = MagicMock()

        prefix, suffix = provider.get_canonical_url_parts("https://gitea.example.com/acme/widgets.git", "main")

        assert prefix == "https://gitea.example.com/acme/widgets/src/branch/main"
        assert suffix == ""

    def test_publish_code_suggestions_returns_false_on_failure(self):
        from pr_agent.git_providers.gitea_provider import GiteaProvider

        provider = object.__new__(GiteaProvider)
        provider.logger = MagicMock()
        provider.publish_inline_comments = MagicMock(return_value=False)

        result = provider.publish_code_suggestions([
            {"body": "suggestion", "relevant_file": "a.py", "relevant_lines_start": 3}
        ])

        assert result is False

    def test_repo_api_get_comment_uses_direct_api(self, repo_api):
        repo_api.get_comment("owner", "repo", 7)

        args, kwargs = repo_api.api_client.call_api.call_args
        assert args[0] == '/repos/{owner}/{repo}/issues/comments/{id}'
        assert kwargs["path_params"] == {'owner': 'owner', 'repo': 'repo', 'id': 7}

    def test_repo_api_remove_reaction_with_reaction_id(self, repo_api):
        repo_api.remove_reaction_comment("owner", "repo", 7, 9)

        args, kwargs = repo_api.api_client.call_api.call_args
        assert args[0] == '/repos/{owner}/{repo}/issues/comments/{id}/reactions/{reaction_id}'
        assert kwargs["path_params"] == {'owner': 'owner', 'repo': 'repo', 'id': 7, 'reaction_id': 9}
