export type DeltaKind = 'preview' | 'final_token_stream';

export interface ActiveDeltaState {
  seq: number;
  kind: DeltaKind;
  text: string;
}

/**
 * Prevent shrink-then-regrow flicker when transitioning from a longer preview
 * to early short tokens of final_token_stream for the same caption seq.
 * Final `caption` events always bypass this guard and commit unconditionally.
 */
export function shouldApplyDeltaUpdate(
  current: ActiveDeltaState | undefined,
  incoming: ActiveDeltaState,
): boolean {
  if (!current || current.seq !== incoming.seq) return true;
  if (current.kind === 'preview' && incoming.kind === 'final_token_stream') {
    return incoming.text.length >= current.text.length;
  }
  return true;
}
