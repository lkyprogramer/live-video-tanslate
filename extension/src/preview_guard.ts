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

export interface CueGuardState {
  sessionId: string;
  committedSeq: number;
  activeDelta?: ActiveDeltaState;
  currentText: string;
  prevText: string;
}

export type CueEventInput =
  | { type: 'ready'; session_id: string }
  | { type: 'caption_delta'; session_id: string; seq: number; text_zh: string; delta_kind?: DeltaKind }
  | { type: 'caption_discard'; session_id: string; seq: number; reason: string }
  | { type: 'caption'; session_id: string; seq: number; text_zh: string };

export interface CueTransitionResult {
  applied: boolean;
  clearedStreamingOnly: boolean;
  state: CueGuardState;
}

/**
 * Pure state machine modeling per-session seq monotonicity, preview_guard hold,
 * explicit caption_discard revocation (clearing streaming text without touching prevText),
 * and unconditional final caption commits.
 */
export function applyCueTransition(state: CueGuardState, event: CueEventInput): CueTransitionResult {
  if (event.type === 'ready') {
    return {
      applied: true,
      clearedStreamingOnly: false,
      state: { sessionId: event.session_id, committedSeq: 0, activeDelta: undefined, currentText: '', prevText: '' },
    };
  }
  if (event.session_id !== state.sessionId || event.seq <= state.committedSeq) {
    return { applied: false, clearedStreamingOnly: false, state };
  }
  if (event.type === 'caption_delta') {
    const incoming: ActiveDeltaState = {
      seq: event.seq,
      kind: event.delta_kind ?? 'preview',
      text: event.text_zh,
    };
    if (!shouldApplyDeltaUpdate(state.activeDelta, incoming)) {
      return { applied: false, clearedStreamingOnly: false, state };
    }
    const newSeq = state.activeDelta?.seq !== event.seq;
    return {
      applied: true,
      clearedStreamingOnly: false,
      state: {
        sessionId: state.sessionId,
        committedSeq: state.committedSeq,
        activeDelta: incoming,
        currentText: event.text_zh,
        prevText: newSeq ? state.currentText : state.prevText,
      },
    };
  }
  if (event.type === 'caption_discard') {
    const clearingActive = state.activeDelta?.seq === event.seq;
    return {
      applied: true,
      clearedStreamingOnly: clearingActive,
      state: {
        sessionId: state.sessionId,
        committedSeq: event.seq,
        activeDelta: clearingActive ? undefined : state.activeDelta,
        currentText: clearingActive ? '' : state.currentText,
        prevText: state.prevText,
      },
    };
  }
  const finalizedInPlace = state.activeDelta?.seq === event.seq;
  return {
    applied: true,
    clearedStreamingOnly: false,
    state: {
      sessionId: state.sessionId,
      committedSeq: event.seq,
      activeDelta: undefined,
      currentText: event.text_zh,
      prevText: finalizedInPlace ? state.prevText : state.currentText,
    },
  };
}
