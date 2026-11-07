/**
 * Human-readable summaries for terminal agent failures.
 *
 * A retry-exhaustion notice used to say only "处理失败，已达最大重试次数", which
 * gives the person who received it nothing to act on — the actual cause lived
 * in `turn_runs.error` and was only visible to whoever could read the server
 * log. This module turns that machine text into one short line.
 */

/** Longest reason we are willing to put in a chat notice. */
const MAX_REASON_LENGTH = 160;

interface KnownCause {
  pattern: RegExp;
  describe: string;
}

/**
 * Causes worth naming explicitly. Ordered: the first match wins, so put the
 * specific markers before the generic network ones.
 */
const KNOWN_CAUSES: KnownCause[] = [
  {
    pattern: /SAFE_DELETE_BULK_CONFIRM_REQUIRED|SAFE_DELETE_[A-Z_]+/,
    describe:
      '宿主拦截了一次批量删除（单次删除条目数超过保护阈值），通常是会话临时目录堆积',
  },
  {
    pattern: /ENOSPC|no space left on device/i,
    describe: '磁盘空间不足',
  },
  {
    pattern: /EACCES|EPERM|permission denied/i,
    describe: '文件权限不足，进程无法读写目标路径',
  },
  {
    pattern: /ENOENT|no such file or directory/i,
    describe: '运行所需文件不存在（依赖或工作目录被移动/删除）',
  },
  {
    pattern: /ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|socket hang up|fetch failed/i,
    describe: '连不上上游服务（模型接口或网络超时）',
  },
  {
    pattern: /\b(401|403)\b|unauthorized|invalid[_ ]api[_ ]key|authentication/i,
    describe: '模型服务鉴权失败（密钥无效或无权限）',
  },
  {
    pattern: /\b429\b|rate limit|too many requests/i,
    describe: '上游接口限流，短时间内请求过多',
  },
  {
    pattern: /context.{0,20}(length|window)|too many tokens|max_tokens/i,
    describe: '上下文超出模型窗口上限',
  },
  {
    pattern: /unrecognized_model|model.{0,20}not.{0,10}found/i,
    describe: '模型名不被上游识别（供应商未提供该模型）',
  },
];

/** Collapse whitespace and clip, keeping the beginning (most specific part). */
function clip(text: string): string {
  const collapsed = text.replace(/\s+/g, ' ').trim();
  if (collapsed.length <= MAX_REASON_LENGTH) return collapsed;
  return `${collapsed.slice(0, MAX_REASON_LENGTH - 1)}…`;
}

/**
 * Turn a raw `turn_runs.error` value into a one-line reason.
 *
 * Returns `undefined` when there is nothing worth telling the user, so callers
 * can fall back to their plain notice instead of printing "原因：unknown".
 */
export function summarizeAgentFailure(raw: string | undefined): string | undefined {
  const text = raw?.trim();
  if (!text) return undefined;

  for (const cause of KNOWN_CAUSES) {
    if (cause.pattern.test(text)) return cause.describe;
  }

  // Strip a leading machine tag (`[safe-delete]`, `[agent-runner]` …) and any
  // trailing JSON blob: the payload carries absolute paths and identifiers that
  // mean nothing in a chat message.
  const withoutTag = text.replace(/^\[[a-z0-9._-]+\]\s*/i, '');
  const withoutJson = withoutTag.replace(/\s*\{.*$/s, '');
  const candidate = withoutJson.trim() || withoutTag.trim();

  return candidate ? clip(candidate) : undefined;
}

/** Compose the final notice body: base sentence plus an optional reason. */
export function composeFailureNotice(
  base: string,
  rawReason: string | undefined,
): string {
  const reason = summarizeAgentFailure(rawReason);
  return reason ? `${base}\n原因：${reason}` : base;
}
