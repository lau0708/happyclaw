export interface PulseOpsBootstrapTurn {
  turnId?: string;
  isHome: boolean;
  isDefaultProfile: boolean;
}

/**
 * Restrict the one-shot first-wake ritual to a turn of the built-in
 * PulseOps in Home. Process warmups have no turnId; custom Agents and
 * non-Home workspaces are always excluded. The caller still requires the
 * turn to be the owner's own (never an automation run).
 */
export function isPulseOpsBootstrapTurn(
  input: PulseOpsBootstrapTurn,
): boolean {
  return Boolean(input.turnId && input.isHome && input.isDefaultProfile);
}

export interface PulseOpsOwnerProfileRuntime {
  isHome: boolean;
  isDefaultProfile: boolean;
  runtimeAgentKind?: 'task' | 'conversation' | 'spawn' | null;
}

/**
 * Any conversation session of Home running the built-in PulseOps has the
 * capability. Per-turn data and mutations remain host-authorized; task and
 * spawn runtimes never receive it.
 */
export function isPulseOpsOwnerProfileRuntimeStructurallyEligible(
  input: PulseOpsOwnerProfileRuntime,
): boolean {
  return (
    input.isHome &&
    input.isDefaultProfile &&
    input.runtimeAgentKind === 'conversation'
  );
}
