import {directionVector, pulseSamples} from './core.mjs';

export function buildCue(context, {angle=0, kind='spatial', volume=0.15, when=context.currentTime}) {
  const buffer = context.createBuffer(1, Math.round(context.sampleRate * 1.2), context.sampleRate);
  buffer.copyToChannel(pulseSamples(context.sampleRate), 0);
  const source = context.createBufferSource();
  source.buffer = buffer;
  const gain = context.createGain();
  gain.gain.setValueAtTime(Math.max(0, Math.min(0.4, volume)), when);
  let spatial;
  if (kind === 'left' || kind === 'right') {
    spatial = context.createStereoPanner();
    spatial.pan.value = kind === 'left' ? -1 : 1;
  } else {
    spatial = context.createPanner();
    spatial.panningModel = 'HRTF';
    spatial.distanceModel = 'inverse';
    spatial.refDistance = 1;
    spatial.rolloffFactor = 0;
    const [x,y,z] = directionVector(angle);
    if (spatial.positionX) {
      spatial.positionX.value=x; spatial.positionY.value=y; spatial.positionZ.value=z;
    } else spatial.setPosition(x,y,z);
  }
  source.connect(gain).connect(spatial).connect(context.destination);
  return {source, gain, disconnect(){source.disconnect(); gain.disconnect(); spatial.disconnect();}};
}

export class CuePlayer {
  context = null;
  active = null;
  generation = 0;
  constructor(onInterrupted) { this.onInterrupted = onInterrupted; }
  async play(options) {
    this.stop();
    const token=this.generation;
    if (!this.context) {
      const Constructor = window.AudioContext || window.webkitAudioContext;
      if (!Constructor) throw new Error('当前浏览器不支持空间音频，请换用 Safari 或 Chrome 的较新版本。');
      this.context = new Constructor({latencyHint:'interactive'});
      this.context.onstatechange = () => {
        if (this.context.state !== 'running' && this.active) this.onInterrupted();
      };
    }
    if (this.context.state !== 'running') await this.context.resume();
    if (token!==this.generation) return false;
    if (this.context.state !== 'running') throw new Error('声音尚未启用，请回到页面并再次点击播放。');
    const context = this.context;
    return new Promise((resolve, reject) => {
      let cue;
      try {
        cue = buildCue(context,{...options,when:context.currentTime+0.06});
        const active = {cue,resolve};
        this.active = active;
        cue.source.onended = () => {
          // Allow the HRTF tail to render; a stop during this gap still cancels the attempt.
          active.timer = setTimeout(() => {
            if (this.active !== active) return;
            this.active = null;
            cue.disconnect();
            resolve(true);
          },70);
        };
        cue.source.start(context.currentTime+0.06);
      } catch (error) {
        this.active = null;
        cue?.disconnect();
        reject(error);
      }
    });
  }
  setVolume(volume) {
    if (this.active) this.active.cue.gain.gain.setTargetAtTime(volume,this.context.currentTime,0.015);
  }
  stop() {
    this.generation++;
    const active = this.active;
    if (!active) return;
    this.active = null;
    clearTimeout(active.timer);
    active.cue.source.onended=null;
    active.cue.source.stop();
    active.cue.disconnect();
    active.resolve(false);
  }
  close() {
    this.stop();
    if (this.context) {
      const context=this.context;
      this.context=null;
      context.onstatechange=null;
      if (context.state!=='closed') void context.close();
    }
  }
}
