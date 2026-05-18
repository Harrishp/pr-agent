# Gitea PR-Agent 使用手册

本文面向已经接入 Gitea 的 PR-Agent 使用者，重点说明 PR-Agent 在 Gitea Pull Request 中的自动处理规则，以及如何通过评论命令手动触发代码分析能力。

本文不包含部署步骤和 Webhook 配置说明。

## 功能概览

PR-Agent 接入 Gitea 后，可以在 PR 流程中提供以下能力：

- 新建或重新触发 PR 时，自动生成 PR 描述、代码改进建议和智能评审结果。
- PR 有新 commit 推送时，自动对最新变更执行增量处理。
- 在 Gitea PR 评论中输入 `/describe`、`/review`、`/improve`、`/ask` 等命令，手动触发对应功能。
- 在代码行评论中使用 `/ask`，针对具体代码位置提问。

## 自动 PR 处理规则

当 Gitea 产生 `pull_request` 相关事件时，PR-Agent 会根据事件动作和 PR 状态判断是否需要自动处理。

默认适合自动处理的 PR 动作包括：

- `opened`：新建 PR。
- `reopened`：重新打开 PR。
- `ready_for_review`：PR 从草稿状态变为可评审状态。
- `review_requested`：请求评审。

符合条件时，PR-Agent 会执行 `pr_commands` 中配置的自动命令。

以下情况通常会被跳过：

- PR 是 draft 状态。
- PR 已关闭，或状态不是 open。
- 仓库、作者、标题、标签、源分支或目标分支命中了忽略规则。
- 事件发送者被识别为需要忽略的 Bot 用户。
- PR 事件缺少必要的 pull request 信息。

## 推荐自动命令

当前项目推荐的新 PR 自动命令如下：

```toml
[gitea]
pr_commands = [
    "/describe --pr_description.publish_description_as_comment=true",
    "/improve",
    "/review"
]
```

配置段名称使用 `[gitea]`，用于声明 Gitea 场景下的新 PR 自动命令。

### `/describe --pr_description.publish_description_as_comment=true`

生成 PR 描述，并将描述内容以评论形式发布到 PR 中。

适合用于：

- 自动总结本次 PR 的变更内容。
- 帮助评审者快速理解改动范围。
- 为缺少详细说明的 PR 补充上下文。

### `/improve`

分析 PR diff，给出可改进的代码建议。

适合用于：

- 发现潜在 bug、边界条件或可读性问题。
- 给出局部代码优化建议。
- 辅助作者在人工评审前先完成一次自查。

### 自动代码审查 `/review`

执行更完整的智能代码审查。

适合用于：

- 对复杂 PR 做系统性检查。
- 识别跨文件影响、逻辑遗漏和潜在风险。
- 补充人工 reviewer 的审查视角。

## Push 后自动处理规则

当启用 `handle_push_trigger` 后，如果 PR 中有新 commit 推送，PR-Agent 会执行 `push_commands` 中配置的命令。

推荐配置示例：

```toml
[gitea]
handle_push_trigger = true
push_commands = [
    "/improve",
    "/review"
]
```

Push 后自动处理会遵循以下规则：

- 只处理仍处于 open 状态的 PR。
- 如果 before commit 和 after commit 相同，会跳过处理。
- 如果新推送没有实际变更，会跳过处理。
- 对同一个 PR 的重复 push trigger 会进行并发去重，避免短时间内重复执行过多任务。
- 执行的命令来自 `push_commands`，通常建议只保留与新变更相关的分析命令。

推荐在 push 阶段使用：

- `/improve`：对新提交的变更给出改进建议。
- `/review`：对新提交后的 PR 状态做进一步审查。

## 手动评论命令使用方式

在 Gitea PR 评论中输入以 `/` 开头的命令，即可手动触发 PR-Agent。

普通评论不会触发 PR-Agent。例如下面的评论会被忽略：

```text
这次改动看起来不错
```

下面的评论会触发 PR-Agent：

```text
/review
```

### 常用命令

#### `/describe`

生成或更新 PR 描述。

示例：

```text
/describe
```

适合在以下场景使用：

- PR 描述为空或不够清晰。
- PR 修改范围较大，需要自动总结。
- 希望在评审前补充变更说明。

#### `/review`

执行代码审查。

示例：

```text
/review
```

适合在以下场景使用：

- 希望对当前 PR 做一次通用审查。
- 自动审查没有触发，想手动补跑。
- 修改后希望重新检查整体风险。

也可以携带额外要求：

```text
/review --pr_reviewer.extra_instructions="重点关注安全风险和异常处理"
```

#### `/improve`

生成代码改进建议。

示例：

```text
/improve
```

适合在以下场景使用：

- 希望获取更具体的代码优化建议。
- 想检查是否存在可维护性、可读性或性能方面的问题。
- 想在提交给人工 reviewer 前先自查一轮。

也可以携带参数控制建议数量或质量阈值：

```text
/improve --pr_code_suggestions.suggestions_score_threshold=7
```

#### `/ask`

针对当前 PR 提问。

示例：

```text
/ask 这个改动是否有性能风险？
```

```text
/ask 这段逻辑有没有并发问题？
```

```text
/ask 这个 PR 最需要 reviewer 关注哪些文件？
```

适合在以下场景使用：

- 想让 PR-Agent 解释某个改动的影响。
- 想确认是否存在特定类型的风险。
- 想让 PR-Agent 给出评审重点。
- 想针对业务逻辑、性能、安全性或边界条件追问。

## 行评论中的 `/ask` 用法

在 Gitea 的代码行评论中使用 `/ask ...`，PR-Agent 会将问题转换为针对具体行范围的分析请求。

示例：

```text
/ask 这里是否可能出现空指针？
```

```text
/ask 这个循环能否优化？
```

```text
/ask 这个条件判断是否覆盖了所有边界情况？
```

行评论提问适合用于：

- 解释某一行或某一段 diff 的行为。
- 判断局部代码是否存在 bug。
- 请求替代实现建议。
- 分析具体代码位置的性能、安全或并发风险。

使用建议：

- 问题越具体，回答越有针对性。
- 如果问题和某段代码强相关，优先在代码行评论中使用 `/ask`。
- 如果问题面向整个 PR，优先在 PR 普通评论中使用 `/ask`。

## 命令参数使用示例

PR-Agent 命令可以携带参数，用于临时调整本次执行的行为。

示例：

```text
/review --pr_reviewer.extra_instructions="重点关注 SQL 注入、权限校验和敏感信息泄露"
```

```text
/improve --pr_code_suggestions.suggestions_score_threshold=7
```

```text
/describe --pr_description.publish_description_as_comment=true
```

常见使用方式：

- 对安全敏感 PR，给 `/review` 增加安全审查要求。
- 对建议过多的 PR，提高 `/improve` 的建议阈值。
- 对需要补充说明的 PR，使用 `/describe` 生成描述评论。

## 使用建议

建议按以下方式使用 Gitea PR-Agent：

1. 新建 PR 后，先查看自动生成的描述、改进建议和智能评审结果。
2. 如果自动结果没有覆盖重点问题，在 PR 评论中使用 `/ask` 追加追问。
3. 如果某段代码存在疑问，在对应代码行评论中使用 `/ask`。
4. 修改代码并 push 后，查看 push 自动处理结果。
5. 对 PR-Agent 给出的建议进行人工判断，再决定是否采纳。

对于复杂 PR，推荐组合使用：

```text
/review --pr_reviewer.extra_instructions="重点关注核心业务逻辑、异常路径和数据一致性"
```

```text
/ask 这个 PR 中最可能引入回归问题的地方在哪里？
```

对于安全相关 PR，推荐使用：

```text
/review --pr_reviewer.extra_instructions="重点关注认证、授权、输入校验、敏感信息泄露和注入风险"
```

## 常见问题

### 评论后没有响应怎么办？

先确认评论内容是否以 `/` 开头。

会触发：

```text
/ask 这个实现是否合理？
```

不会触发：

```text
这个实现是否合理？
```

如果命令格式正确但仍无响应，再确认 PR 是否处于 open 状态，以及当前用户是否有权限触发 PR-Agent。

### 自动处理没有触发怎么办？

常见原因包括：

- PR 是 draft 状态。
- PR 已关闭或不是 open 状态。
- PR 命中了忽略规则。
- 事件发送者是被忽略的 Bot 用户。
- 没有配置 `pr_commands`。

可以通过手动评论 `/review`、`/improve` 或 `/ask ...` 验证 PR-Agent 是否能正常响应。

### Push 后没有自动处理怎么办？

常见原因包括：

- 没有启用 `handle_push_trigger`。
- 没有配置 `push_commands`。
- 推送前后的 commit 没有实际变化。
- 同一个 PR 短时间内已有处理任务正在运行，重复触发被去重。

可以在 PR 评论中手动执行：

```text
/improve
```

或：

```text
/review
```

### 建议太多或太少怎么办？

可以通过命令参数调整本次执行行为。例如提高建议阈值：

```text
/improve --pr_code_suggestions.suggestions_score_threshold=7
```

也可以改用更具体的问题：

```text
/ask 这个 PR 是否有明显的空指针、数组越界或异常未处理风险？
```

### `/ask` 应该写在 PR 评论还是代码行评论？

如果问题面向整个 PR，写在 PR 普通评论中。

示例：

```text
/ask 这个 PR 的主要风险是什么？
```

如果问题面向某一行或某段 diff，写在对应代码行评论中。

示例：

```text
/ask 这里的条件判断是否会漏掉空值？
```
