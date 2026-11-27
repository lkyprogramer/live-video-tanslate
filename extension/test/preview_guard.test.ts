import assert from 'node:assert/strict';
import test from 'node:test';
import { shouldApplyDeltaUpdate } from '../src/preview_guard';

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
