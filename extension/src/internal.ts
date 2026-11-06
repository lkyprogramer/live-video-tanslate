import type { Caption } from './protocol';

export interface Owner { tabId: number; activation: string }
export interface Player {
  serial: number;
  version: number;
  state: 'playing' | 'paused' | 'seeking' | 'ended';
}
export type View = Owner & { epoch: number } & (
  | { kind: 'status'; text: string }
  | { kind: 'ready'; sessionId: string }
  | { kind: 'caption'; cue: Caption }
  | { kind: 'clear' }
  | { kind: 'stopped'; text?: string }
);
