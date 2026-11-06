import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { serverEvent, start } from '../src/protocol';
import { pcm, Resampler } from '../src/resample';

test('wire fixtures match start and accept only zh-Hans ready', () => {
  const fixture = JSON.parse(readFileSync(new URL('../../protocol/v1.fixture.json', import.meta.url), 'utf8'));
  assert.deepEqual(start('reazon-hy', 'fixture-token'), fixture.start);
  for (const kind of ['ready', 'caption', 'warning', 'finished', 'error']) {
    const result = serverEvent(JSON.stringify(fixture[kind]));
    assert.equal(result?.type, kind);
  }
  assert.equal(serverEvent(JSON.stringify({ ...fixture.ready, language: 'ja' })), undefined);
  assert.equal(serverEvent(JSON.stringify({ ...fixture.caption, end_sample: 0 })), undefined);
});

for (const rate of [44100, 48000]) {
  test(`PCM continuity, duration and downmix at ${rate} Hz`, () => {
    const left = Float32Array.from({ length: rate }, (_, i) => .5 * Math.sin(i * 2 * Math.PI * 800 / rate));
    const right = left.map(x => -x);
    const r = new Resampler(rate);
    const out: number[] = [];
    for (let i = 0; i < rate; i += 113) out.push(...r.push([left.slice(i, i + 113)]));
    out.push(...r.finish());
    const one = new Resampler(rate);
    assert.deepEqual(out, [...one.push([left]), ...one.finish()]);
    assert.equal(out.length, 16000);
    const stereo = new Resampler(rate);
    assert.ok([...stereo.push([left, right]), ...stereo.finish()].every(x => x === 0));
    assert.deepEqual([...new Uint8Array(pcm([-1, .5, 1]))], [0, 128, 0, 64, 255, 127]);
  });

  test(`anti-alias filtering at ${rate} Hz`, () => {
    const high = Float32Array.from({ length: rate }, (_, i) => Math.sin(i * 2 * Math.PI * 13000 / rate));
    const r = new Resampler(rate);
    const out = [...r.push([high]), ...r.finish()].slice(100, -100);
    const rms = Math.sqrt(out.reduce((acc, n) => acc + n * n, 0) / out.length);
    assert.ok(rms < .03, String(rms));
  });
}
