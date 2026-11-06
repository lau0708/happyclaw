import { describe, expect, test } from 'vitest';

import {
  isPulseOpsBootstrapTurn,
  isPulseOpsOwnerProfileRuntimeStructurallyEligible,
} from '../src/happyclaw-bootstrap.js';

describe('PulseOps first-wake eligibility', () => {
  test('allows only a real interactive Home turn of the built-in profile', () => {
    expect(
      isPulseOpsBootstrapTurn({
        turnId: 'owner-turn',
        isHome: true,
        isDefaultProfile: true,
      }),
    ).toBe(true);
    expect(
      isPulseOpsBootstrapTurn({
        isHome: true,
        isDefaultProfile: true,
      }),
    ).toBe(false);
    expect(
      isPulseOpsBootstrapTurn({
        turnId: 'custom-turn',
        isHome: true,
        isDefaultProfile: false,
      }),
    ).toBe(false);
    expect(
      isPulseOpsBootstrapTurn({
        turnId: 'project-turn',
        isHome: false,
        isDefaultProfile: true,
      }),
    ).toBe(false);
  });
});

describe('PulseOps Owner Profile structural runtime eligibility', () => {
  test('any conversation session of Home has it; other runtime kinds do not', () => {
    const session = {
      isHome: true,
      isDefaultProfile: true,
      runtimeAgentKind: 'conversation' as const,
    };
    expect(isPulseOpsOwnerProfileRuntimeStructurallyEligible(session)).toBe(
      true,
    );
    for (const runtimeAgentKind of ['task', 'spawn', null] as const) {
      expect(
        isPulseOpsOwnerProfileRuntimeStructurallyEligible({
          ...session,
          runtimeAgentKind,
        }),
      ).toBe(false);
    }
    expect(
      isPulseOpsOwnerProfileRuntimeStructurallyEligible({
        ...session,
        isHome: false,
      }),
    ).toBe(false);
    expect(
      isPulseOpsOwnerProfileRuntimeStructurallyEligible({
        ...session,
        isDefaultProfile: false,
      }),
    ).toBe(false);
  });
});
