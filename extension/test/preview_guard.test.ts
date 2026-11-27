import assert from 'node:assert/strict';
import test from 'node:test';
import { applyCueTransition, shouldApplyDeltaUpdate, type CueGuardState } from '../src/preview_guard';

test('preview guard holds shorter final_token_stream after preview on same seq', () => {
  assert.equal(
    shouldApplyDeltaUpdate(undefined, { seq: 1, kind: 'preview', text: '朝海，这周末你来吧。' }),
    true,
  );
  assert.equal(
    shouldApplyDeltaUpdate(
      { seq: 1, kind: 'preview', text: '朝海，这周末你来吧。' },
      { seq: 1, kind: 'final_token_stream', text: '麻美' },
    ),
    false,
  );
  assert.equal(
    shouldApplyDeltaUpdate(
      { seq: 1, kind: 'preview', text: '不，我爸是上班。' },
      { seq: 1, kind: 'final_token_stream', text: '不，我爸要去上早班。' },
    ),
    true,
  );
  assert.equal(
    shouldApplyDeltaUpdate(
      { seq: 1, kind: 'final_token_stream', text: '麻美' },
      { seq: 1, kind: 'final_token_stream', text: '麻美，这周末' },
    ),
    true,
  );
  assert.equal(
    shouldApplyDeltaUpdate(
      { seq: 1, kind: 'preview', text: '旧预览内容' },
      { seq: 1, kind: 'preview', text: '新预览' },
    ),
    true,
  );
  assert.equal(
    shouldApplyDeltaUpdate(
      { seq: 1, kind: 'preview', text: '朝海，这周末你来吧。' },
      { seq: 2, kind: 'final_token_stream', text: '麻美' },
    ),
    true,
  );
});

test('R1 cue guard lifecycle revokes discarded delta, advances seq, and unblocks next cue without clearing prevText', () => {
  let state: CueGuardState = { sessionId: '', committedSeq: 0, currentText: '', prevText: '' };
  state = applyCueTransition(state, { type: 'ready', session_id: 's1' }).state;
  // Earlier committed caption stays in prevText once next cue streams
  state = applyCueTransition(state, { type: 'caption', session_id: 's1', seq: 1, text_zh: '上一句终稿。' }).state;

  // A (seq=2): preview -> longer stream -> length_limit discard
  state = applyCueTransition(state, { type: 'caption_delta', session_id: 's1', seq: 2, delta_kind: 'preview', text_zh: '这句预览很长。' }).state;
  assert.equal(state.currentText, '这句预览很长。');
  assert.equal(state.prevText, '上一句终稿。');

  const dRes = applyCueTransition(state, { type: 'caption_discard', session_id: 's1', seq: 2, reason: 'length_limit' });
  assert.equal(dRes.applied, true);
  assert.equal(dRes.clearedStreamingOnly, true);
  state = dRes.state;
  assert.equal(state.currentText, '');
  assert.equal(state.prevText, '上一句终稿。');
  assert.equal(state.committedSeq, 2);

  // Stale delta for discarded seq=2 or wrong session must be rejected
  assert.equal(applyCueTransition(state, { type: 'caption_delta', session_id: 's1', seq: 2, delta_kind: 'preview', text_zh: '迟到残影' }).applied, false);
  assert.equal(applyCueTransition(state, { type: 'caption_discard', session_id: 'other', seq: 3, reason: 'x' }).applied, false);

  // Consecutive discard at seq=3, then short stream & final at seq=4 must apply cleanly
  state = applyCueTransition(state, { type: 'caption_delta', session_id: 's1', seq: 3, delta_kind: 'preview', text_zh: '第二失败句' }).state;
  state = applyCueTransition(state, { type: 'caption_discard', session_id: 's1', seq: 3, reason: 'length_limit' }).state;
  const bStream = applyCueTransition(state, { type: 'caption_delta', session_id: 's1', seq: 4, delta_kind: 'final_token_stream', text_zh: '麻美' });
  assert.equal(bStream.applied, true);
  state = applyCueTransition(bStream.state, { type: 'caption', session_id: 's1', seq: 4, text_zh: '麻美你好。' }).state;
  assert.equal(state.currentText, '麻美你好。');
  assert.equal(state.committedSeq, 4);
});
