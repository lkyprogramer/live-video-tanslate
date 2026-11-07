import { pcm, Resampler } from './resample';
import { SAMPLES_PER_FRAME } from './protocol';

declare const sampleRate: number;
declare const currentFrame: number;
declare const SUBTITLE_DIAGNOSTICS: boolean;
declare class AudioWorkletProcessor { readonly port: MessagePort }
declare function registerProcessor(name: string, type: typeof AudioWorkletProcessor): void;

class PcmWorklet extends AudioWorkletProcessor {
  private stream?: { epoch: number; resampler: Resampler; pending: number[]; firstInputFrame?: number };

  constructor() {
    super();
    this.port.onmessage = ({ data }) => {
      const stream = this.stream;
      if (data.type === 'begin') {
        this.stream = { epoch: data.epoch, resampler: new Resampler(sampleRate), pending: [] };
      } else if (data.type === 'discard') {
        this.stream = undefined;
      } else if (data.type === 'finish' && stream && stream.epoch === data.epoch) {
        this.add(stream.resampler.finish());
        if (stream.pending.length) this.send(stream.epoch, stream.pending);
        this.stream = undefined;
        this.port.postMessage({ type: 'flushed', epoch: stream.epoch });
      }
    };
  }

  process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    for (const channel of outputs[0] ?? []) channel.fill(0);
    if (this.stream && inputs[0]?.length) {
      if (SUBTITLE_DIAGNOSTICS) this.stream.firstInputFrame ??= currentFrame;
      this.add(this.stream.resampler.push(inputs[0]));
    }
    return true;
  }

  private add(samples: number[]) {
    const stream = this.stream!;
    for (const sample of samples) {
      stream.pending.push(sample);
      if (stream.pending.length === SAMPLES_PER_FRAME) {
        this.send(stream.epoch, stream.pending);
        stream.pending = [];
      }
    }
  }

  private send(epoch: number, samples: number[]) {
    const frame = pcm(samples);
    const message = SUBTITLE_DIAGNOSTICS
      ? { type: 'pcm', epoch, frame, firstContextTime: (this.stream?.firstInputFrame ?? currentFrame) / sampleRate }
      : { type: 'pcm', epoch, frame };
    this.port.postMessage(message, [frame]);
  }
}

registerProcessor('subtitle-pcm', PcmWorklet);
