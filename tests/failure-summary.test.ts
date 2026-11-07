import { describe, expect, test } from 'vitest';

import {
  composeFailureNotice,
  summarizeAgentFailure,
} from '../src/failure-summary.js';

describe('summarizeAgentFailure', () => {
  test('names the bulk-delete guard instead of echoing its JSON payload', () => {
    const raw =
      '[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":79,"threshold":50,"scope":"turn","targets":["/Users/x/.skills-previous-a"],"targetCount":1}';

    const summary = summarizeAgentFailure(raw);

    expect(summary).toContain('批量删除');
    expect(summary).not.toContain('{');
    expect(summary).not.toContain('/Users/x');
  });

  test('maps network failures to one readable cause', () => {
    expect(summarizeAgentFailure('connect ECONNREFUSED 127.0.0.1:3000')).toBe(
      '连不上上游服务（模型接口或网络超时）',
    );
  });

  test('drops a leading machine tag and any trailing JSON blob', () => {
    const summary = summarizeAgentFailure(
      '[agent-runner] worker crashed while indexing {"path":"/tmp/a","id":7}',
    );

    expect(summary).toBe('worker crashed while indexing');
  });

  test('clips long unexplainable text but keeps the beginning', () => {
    const summary = summarizeAgentFailure('x'.repeat(400));

    expect(summary).toHaveLength(160);
    expect(summary?.endsWith('…')).toBe(true);
  });

  test('returns undefined when there is nothing to say', () => {
    expect(summarizeAgentFailure(undefined)).toBeUndefined();
    expect(summarizeAgentFailure('   ')).toBeUndefined();
  });
});

describe('composeFailureNotice', () => {
  test('appends the reason on its own line', () => {
    expect(
      composeFailureNotice('设备异常排查 处理失败，已达最大重试次数', 'ENOSPC'),
    ).toBe('设备异常排查 处理失败，已达最大重试次数\n原因：磁盘空间不足');
  });

  test('leaves the base sentence alone when no reason is known', () => {
    expect(composeFailureNotice('处理失败', undefined)).toBe('处理失败');
    expect(composeFailureNotice('处理失败', '  ')).toBe('处理失败');
  });
});
