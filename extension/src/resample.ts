const OUTPUT_RATE = 16000;
const RADIUS = 20;

export class Resampler {
  private buffer: number[] = [];
  private base = 0;
  private total = 0;
  private produced = 0;

  constructor(private readonly inputRate: number) {}

  push(channels: Float32Array[]): number[] {
    const length = channels[0]?.length ?? 0;
    for (let i = 0; i < length; i++) {
      let sum = 0;
      for (const channel of channels) sum += channel[i];
      this.buffer.push(sum / channels.length);
    }
    this.total += length;
    return this.emit(false);
  }

  finish(): number[] { return this.emit(true); }

  private emit(eof: boolean): number[] {
    const out: number[] = [];
    const limit = Math.floor(this.total * OUTPUT_RATE / this.inputRate);
    const cutoff = Math.min(1, OUTPUT_RATE / this.inputRate) * .90;
    while (this.produced < limit) {
      const pos = this.produced * this.inputRate / OUTPUT_RATE;
      const middle = Math.floor(pos);
      if (!eof && middle + RADIUS >= this.total) break;
      let value = 0;
      let weight = 0;
      for (let i = middle - RADIUS + 1; i <= middle + RADIUS; i++) {
        const x = i - pos;
        const z = Math.PI * cutoff * x;
        const kernel = cutoff * (z === 0 ? 1 : Math.sin(z) / z) * (.5 + .5 * Math.cos(Math.PI * x / RADIUS));
        value += (this.buffer[i - this.base] ?? 0) * kernel;
        weight += kernel;
      }
      out.push(value / weight);
      this.produced++;
    }
    const floor = Math.floor(this.produced * this.inputRate / OUTPUT_RATE) - RADIUS;
    const drop = Math.max(0, Math.min(this.buffer.length, floor - this.base));
    this.buffer.splice(0, drop);
    this.base += drop;
    return out;
  }
}

export function pcm(samples: number[]): ArrayBuffer {
  const view = new DataView(new ArrayBuffer(samples.length * 2));
  samples.forEach((sample, i) => view.setInt16(i * 2,
    Math.min(32767, Math.max(-32768, Math.round(sample * 32768))), true));
  return view.buffer;
}
