import asyncio.locks
import copy
import os
import re
import uuid
from typing import Any, Dict, Tuple

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from starlette.background import BackgroundTasks
from starlette.middleware import Middleware
from starlette_context import context
from starlette_context.middleware import RawContextMiddleware

from pr_agent.agent.pr_agent import PRAgent
from pr_agent.algo.utils import update_settings_from_args
from pr_agent.config_loader import get_settings, global_settings
from pr_agent.git_providers import get_git_provider, get_git_provider_with_context
from pr_agent.git_providers.utils import apply_repo_settings
from pr_agent.identity_providers import get_identity_provider
from pr_agent.identity_providers.identity_provider import Eligibility
from pr_agent.log import LoggingFormat, get_logger, setup_logger
from pr_agent.servers.utils import DefaultDictWithTimeout, verify_signature

setup_logger(fmt=LoggingFormat.JSON, level=get_settings().get("CONFIG.LOG_LEVEL", "DEBUG"))
base_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
build_number_path = os.path.join(base_path, "build_number.txt")
if os.path.exists(build_number_path):
    with open(build_number_path) as f:
        build_number = f.read().strip()
else:
    build_number = "unknown"
router = APIRouter()


def _get_gitea_setting(name: str, default=None):
    return get_settings().get(f"gitea.{name}", get_settings().get(f"GITEA.{name.upper()}", default))


@router.post("/api/v1/gitea_webhooks")
async def handle_gitea_webhooks(background_tasks: BackgroundTasks, request: Request, response: Response):
    """Handle incoming Gitea webhook requests"""
    get_logger().debug("Received a Gitea webhook")

    body = await get_body(request)

    context["settings"] = copy.deepcopy(global_settings)
    context["git_provider"] = {}
    background_tasks.add_task(handle_request, body, event=request.headers.get("X-Gitea-Event", None))
    return {}


async def get_body(request: Request):
    """Parse and verify webhook request body"""
    try:
        body = await request.json()
    except Exception as e:
        get_logger().error("Error parsing request body", artifact={'error': e})
        raise HTTPException(status_code=400, detail="Error parsing request body") from e

    webhook_secret = getattr(get_settings().gitea, 'webhook_secret', None)
    if webhook_secret:
        body_bytes = await request.body()
        signature_header = request.headers.get('x-gitea-signature', None)
        if not signature_header:
            get_logger().error("Missing signature header")
            raise HTTPException(status_code=400, detail="Missing signature header")

        try:
            verify_signature(body_bytes, webhook_secret, f"sha256={signature_header}")
        except Exception as ex:
            get_logger().error(f"Invalid signature: {ex}")
            raise HTTPException(status_code=401, detail="Invalid signature")

    return body


_duplicate_push_triggers = DefaultDictWithTimeout(ttl=_get_gitea_setting("push_trigger_pending_tasks_ttl", 120))
_pending_task_duplicate_push_conditions = DefaultDictWithTimeout(
    asyncio.locks.Condition, ttl=_get_gitea_setting("push_trigger_pending_tasks_ttl", 120)
)


def get_log_context(body, event, action, build_number):
    sender = ""
    sender_id = ""
    sender_type = ""
    try:
        sender = body.get("sender", {}).get("login") or body.get("sender", {}).get("username", "")
        sender_id = body.get("sender", {}).get("id", "")
        sender_type = body.get("sender", {}).get("type", "")
        repo = body.get("repository", {}).get("full_name", "")
        git_org = body.get("organization", {}).get("username") or body.get("organization", {}).get("name", "")
        app_name = get_settings().get("CONFIG.APP_NAME", "Unknown")
        log_context = {"action": action, "event": event, "sender": sender, "server_type": "gitea_app",
                       "request_id": uuid.uuid4().hex, "build_number": build_number, "app_name": app_name,
                       "repo": repo, "git_org": git_org}
    except Exception as e:
        get_logger().error("Failed to get log context", artifact={'error': e})
        log_context = {}
    return log_context, sender, sender_id, sender_type


def is_bot_user(sender, sender_type):
    try:
        if get_settings().get("GITEA.IGNORE_BOT_PR", get_settings().get("gitea.ignore_bot_pr", False)) and sender_type == "Bot":
            if 'pr-agent' not in sender:
                get_logger().info(f"Ignoring PR from '{sender=}' because it is a bot")
            return True
        if sender_type == "Bot" and sender and "pr-agent" not in sender.lower():
            get_logger().info(f"Ignoring event from bot user: {sender}")
            return True
    except Exception as e:
        get_logger().error(f"Failed 'is_bot_user' logic: {e}")
    return False


async def handle_request(body: Dict[str, Any], event: str):
    """Process Gitea webhook events"""
    action = body.get("action")
    get_logger().debug(f"Processing Gitea webhook event={event}, action={action}, keys={list(body.keys())}")
    if not action:
        get_logger().debug("No action found in request body")
        return {}

    agent = PRAgent()
    log_context, sender, sender_id, sender_type = get_log_context(body, event, action, build_number)

    if is_bot_user(sender, sender_type):
        get_logger().debug("Request ignored: bot user detected")
        return {}
    if action != 'created' and not should_process_pr_logic(body):
        get_logger().debug("Request ignored: PR logic filtering")
        return {}

    normalized_action = "synchronize" if action == "synchronized" else action
    if action == 'created' or event == "issue_comment":
        get_logger().debug('Request body', artifact=body, event=event)
        await handle_comments_on_pr(body, event, sender, sender_id, action, log_context, agent)
    elif event == "pull_request" and normalized_action not in ("synchronize", "closed"):
        get_logger().debug('Request body', artifact=body, event=event)
        await handle_new_pr_opened(body, event, sender, sender_id, normalized_action, log_context, agent)
    elif event == "pull_request" and normalized_action == "synchronize":
        await handle_push_trigger_for_new_commits(body, event, sender, sender_id, normalized_action, log_context, agent)
    elif event == "pull_request" and normalized_action == "closed":
        if get_settings().get("CONFIG.ANALYTICS_FOLDER", ""):
            handle_closed_pr(body, event, normalized_action, log_context)
    else:
        get_logger().info(f"event {event=} action {action=} does not require any handling")
    return {}


async def handle_comments_on_pr(body: Dict[str, Any], event: str, sender: str, sender_id: str, action: str,
                                log_context: Dict[str, Any], agent: PRAgent):
    comment = body.get("comment", {})
    if not comment:
        get_logger().debug("No comment found in payload")
        return {}

    comment_body = comment.get("body", "")
    if comment_body and isinstance(comment_body, str) and not comment_body.lstrip().startswith("/"):
        if '/ask' in comment_body and comment_body.strip().startswith('> ![image]'):
            comment_body_split = comment_body.split('/ask')
            comment_body = '/ask' + comment_body_split[1] + ' \n' + comment_body_split[0].strip().lstrip('>')
            get_logger().info(f"Reformatting comment_body so command is at the beginning: {comment_body}")
        else:
            get_logger().info("Ignoring comment not starting with /")
            return {}

    pr_url = (
        body.get("pull_request", {}).get("url")
        or body.get("issue", {}).get("pull_request", {}).get("url")
        or comment.get("pull_request_url")
        or _construct_pr_url(body)
    )
    if not pr_url:
        get_logger().warning(f"Could not determine PR URL from payload. Top-level keys: {list(body.keys())}")
        return {}

    disable_eyes = False
    try:
        if '/ask' in comment_body and _is_line_comment(comment):
            comment_body = handle_line_comments(body, comment_body)
            disable_eyes = True
    except Exception as e:
        get_logger().error("Failed to handle line comment", artifact={'error': e})

    log_context["api_url"] = pr_url
    comment_id = comment.get("id")
    provider = get_git_provider_with_context(pr_url=pr_url)
    with get_logger().contextualize(**log_context):
        if get_identity_provider().verify_eligibility("gitea", sender_id, pr_url) is not Eligibility.NOT_ELIGIBLE:
            get_logger().info(f"Processing comment on PR {pr_url=}, comment_body={comment_body}")
            await agent.handle_request(pr_url, comment_body,
                                       notify=lambda: provider.add_eyes_reaction(comment_id, disable_eyes=disable_eyes))
        else:
            get_logger().info(f"User {sender=} is not eligible to process comment on PR {pr_url=}")


async def handle_new_pr_opened(body: Dict[str, Any], event: str, sender: str, sender_id: str, action: str,
                               log_context: Dict[str, Any], agent: PRAgent):
    pull_request, api_url = _check_pull_request_event(action, body, log_context)
    if not (pull_request and api_url):
        get_logger().info(f"Invalid PR event: {action=} {api_url=}")
        return {}

    handle_pr_actions = _get_gitea_setting("handle_pr_actions", ["opened", "reopened", "ready_for_review", "review_requested"])
    if action in handle_pr_actions:
        apply_repo_settings(api_url)
        if get_identity_provider().verify_eligibility("gitea", sender_id, api_url) is not Eligibility.NOT_ELIGIBLE:
            await _perform_commands_gitea("pr_commands", agent, body, api_url)
        else:
            get_logger().info(f"User {sender=} is not eligible to process PR {api_url=}")


async def handle_push_trigger_for_new_commits(body: Dict[str, Any], event: str, sender: str, sender_id: str, action: str,
                                              log_context: Dict[str, Any], agent: PRAgent):
    pull_request, api_url = _check_pull_request_event(action, body, log_context)
    if not (pull_request and api_url):
        return {}

    apply_repo_settings(api_url)
    if not _get_gitea_setting("handle_push_trigger", False):
        return {}

    before_sha = body.get("before") or body.get("old_commit_id")
    after_sha = body.get("after") or body.get("new_commit_id") or pull_request.get("head", {}).get("sha")
    merge_commit_sha = pull_request.get("merge_commit_sha")
    if before_sha and after_sha and before_sha == after_sha:
        return {}
    if _get_gitea_setting("push_trigger_ignore_merge_commits", False) and after_sha == merge_commit_sha:
        return {}

    current_active_tasks = _duplicate_push_triggers.setdefault(api_url, 0)
    max_active_tasks = 2 if _get_gitea_setting("push_trigger_pending_tasks_backlog", False) else 1
    if current_active_tasks < max_active_tasks:
        get_logger().info(f"Continue processing push trigger for {api_url=} because there are {current_active_tasks} active tasks")
        _duplicate_push_triggers[api_url] += 1
    else:
        get_logger().info(f"Skipping push trigger for {api_url=} because another event already triggered the same processing")
        return {}

    async with _pending_task_duplicate_push_conditions[api_url]:
        if current_active_tasks == 1:
            get_logger().info(f"Waiting to process push trigger for {api_url=} because the first task is still in progress")
            await _pending_task_duplicate_push_conditions[api_url].wait()
            get_logger().info(f"Finished waiting to process push trigger for {api_url=} - continue with flow")

    try:
        if get_identity_provider().verify_eligibility("gitea", sender_id, api_url) is not Eligibility.NOT_ELIGIBLE:
            get_logger().info(f"Performing incremental review for {api_url=} because of {event=} and {action=}")
            await _perform_commands_gitea("push_commands", agent, body, api_url)
    finally:
        async with _pending_task_duplicate_push_conditions[api_url]:
            _pending_task_duplicate_push_conditions[api_url].notify(1)
            _duplicate_push_triggers[api_url] -= 1


def handle_closed_pr(body, event, action, log_context):
    pull_request = body.get("pull_request", {})
    is_merged = pull_request.get("merged", False) or pull_request.get("has_merged", False)
    if not is_merged:
        return
    api_url = pull_request.get("url", "")
    pr_statistics = get_git_provider()(api_url).calc_pr_statistics(pull_request)
    log_context["api_url"] = api_url
    get_logger().info("PR-Agent statistics for closed PR", analytics=True, pr_statistics=pr_statistics, **log_context)


def _is_line_comment(comment: Dict[str, Any]) -> bool:
    return any(key in comment for key in ("path", "line", "new_position", "old_position", "diff_hunk"))


def handle_line_comments(body: Dict, comment_body: str) -> str:
    if not comment_body:
        return ""
    comment = body.get("comment", {})
    start_line = comment.get("start_line") or comment.get("line") or comment.get("new_line") or comment.get("new_position")
    end_line = comment.get("line") or comment.get("new_line") or comment.get("new_position") or start_line
    side = comment.get("side") or "RIGHT"
    path = comment.get("path") or comment.get("file_path") or comment.get("filename", "")
    comment_id = comment.get("id")
    question = comment_body.replace('/ask', '').strip()
    get_settings().set("ask_diff_hunk", comment.get("diff_hunk", ""))
    if '/ask' in comment_body:
        comment_body = f"/ask_line --line_start={start_line} --line_end={end_line} --side={side} --file_name={path} --comment_id={comment_id} {question}"
    return comment_body


def _construct_pr_url(body: Dict[str, Any]) -> str | None:
    issue = body.get("issue", {})
    repo_full_name = body.get("repository", {}).get("full_name", "")
    issue_number = issue.get("number")
    if repo_full_name and issue_number:
        gitea_base = get_settings().get("GITEA.URL", get_settings().get("gitea.url", "")).rstrip("/")
        url = f"{gitea_base}/api/v1/repos/{repo_full_name}/pulls/{issue_number}"
        get_logger().debug(f"Constructed PR URL from repository info: {url}")
        return url
    return None


def _check_pull_request_event(action: str, body: dict, log_context: dict) -> Tuple[Dict[str, Any], str]:
    invalid_result = {}, ""
    pull_request = body.get("pull_request")
    if not pull_request:
        return invalid_result
    api_url = pull_request.get("url")
    if not api_url:
        return invalid_result
    log_context["api_url"] = api_url
    if pull_request.get("draft", False) or pull_request.get("state") not in (None, "open"):
        return invalid_result
    if action in ("review_requested", "synchronize") and pull_request.get("created_at") == pull_request.get("updated_at"):
        return invalid_result
    return pull_request, api_url


async def handle_pr_event(body: Dict[str, Any], event: str, action: str, agent: PRAgent):
    log_context, sender, sender_id, _ = get_log_context(body, event, action, build_number)
    normalized_action = "synchronize" if action == "synchronized" else action
    if normalized_action == "synchronize":
        return await handle_push_trigger_for_new_commits(body, event, sender, sender_id, normalized_action, log_context, agent)
    return await handle_new_pr_opened(body, event, sender, sender_id, normalized_action, log_context, agent)


async def handle_comment_event(body: Dict[str, Any], event: str, action: str, agent: PRAgent):
    log_context, sender, sender_id, _ = get_log_context(body, event, action, build_number)
    return await handle_comments_on_pr(body, event, sender, sender_id, action, log_context, agent)


async def _perform_commands_gitea(commands_conf: str, agent: PRAgent, body: dict, api_url: str):
    apply_repo_settings(api_url)
    if commands_conf == "pr_commands" and get_settings().config.disable_auto_feedback:
        get_logger().info(f"Auto feedback is disabled, skipping auto commands for PR {api_url=}")
        return
    if not should_process_pr_logic(body):
        return {}
    commands = _get_gitea_setting(commands_conf, [])
    if not commands:
        get_logger().info("New PR, but no auto commands configured")
        return
    get_settings().set("config.is_auto_command", True)
    for command in commands:
        split_command = command.split(" ")
        command = split_command[0]
        args = split_command[1:]
        other_args = update_settings_from_args(args)
        new_command = ' '.join([command] + other_args)
        get_logger().info(f"{commands_conf}. Performing auto command '{new_command}', for {api_url=}")
        await agent.handle_request(api_url, new_command)


def should_process_pr_logic(body) -> bool:
    try:
        pull_request = body.get("pull_request", {})
        title = pull_request.get("title", "")
        pr_labels = pull_request.get("labels", [])
        source_branch = pull_request.get("head", {}).get("ref", "")
        target_branch = pull_request.get("base", {}).get("ref", "")
        sender = body.get("sender", {}).get("login")
        repo_full_name = body.get("repository", {}).get("full_name", "")

        ignore_repos = get_settings().get("CONFIG.IGNORE_REPOSITORIES", [])
        if ignore_repos and repo_full_name:
            if any(re.search(regex, repo_full_name) for regex in ignore_repos):
                get_logger().info(f"Ignoring PR from repository '{repo_full_name}' due to 'config.ignore_repositories' setting")
                return False

        ignore_pr_users = get_settings().get("CONFIG.IGNORE_PR_AUTHORS", [])
        if ignore_pr_users and sender:
            if any(re.search(regex, sender) for regex in ignore_pr_users):
                get_logger().info(f"Ignoring PR from user '{sender}' due to 'config.ignore_pr_authors' setting")
                return False

        if title:
            ignore_pr_title_re = get_settings().get("CONFIG.IGNORE_PR_TITLE", [])
            if not isinstance(ignore_pr_title_re, list):
                ignore_pr_title_re = [ignore_pr_title_re]
            if ignore_pr_title_re and any(re.search(regex, title) for regex in ignore_pr_title_re):
                get_logger().info(f"Ignoring PR with title '{title}' due to config.ignore_pr_title setting")
                return False

        ignore_pr_labels = get_settings().get("CONFIG.IGNORE_PR_LABELS", [])
        if pr_labels and ignore_pr_labels:
            labels = [label['name'] for label in pr_labels]
            if any(label in ignore_pr_labels for label in labels):
                labels_str = ", ".join(labels)
                get_logger().info(f"Ignoring PR with labels '{labels_str}' due to config.ignore_pr_labels settings")
                return False

        ignore_pr_source_branches = get_settings().get("CONFIG.IGNORE_PR_SOURCE_BRANCHES", [])
        ignore_pr_target_branches = get_settings().get("CONFIG.IGNORE_PR_TARGET_BRANCHES", [])
        if pull_request and (ignore_pr_source_branches or ignore_pr_target_branches):
            if any(re.search(regex, source_branch) for regex in ignore_pr_source_branches):
                get_logger().info(f"Ignoring PR with source branch '{source_branch}' due to config.ignore_pr_source_branches settings")
                return False
            if any(re.search(regex, target_branch) for regex in ignore_pr_target_branches):
                get_logger().info(f"Ignoring PR with target branch '{target_branch}' due to config.ignore_pr_target_branches settings")
                return False
    except Exception as e:
        get_logger().error(f"Failed 'should_process_pr_logic': {e}")
    return True


@router.get("/")
async def root():
    return {"status": "ok"}


middleware = [Middleware(RawContextMiddleware)]
app = FastAPI(middleware=middleware)
app.include_router(router)


def start():
    """Start the Gitea webhook server"""
    port = int(os.environ.get("PORT", "3000"))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    start()
