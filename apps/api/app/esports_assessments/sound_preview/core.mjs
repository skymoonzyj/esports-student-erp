export const directions = [0, 45, 90, 135, 180, 225, 270, 315];
export const labels = ['前方', '右前', '右侧', '右后', '后方', '左后', '左侧', '左前'];

export function directionVector(angle) {
  const rad = angle * Math.PI / 180;
  return [Math.sin(rad), 0, -Math.cos(rad)];
}
export function angleError(target, answer) {
  const diff = Math.abs(target - answer) % 360;
  return Math.min(diff, 360 - diff);
}
export function createRound(random = Math.random) {
  const targets = [...directions];
  for (let i = targets.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [targets[i], targets[j]] = [targets[j], targets[i]];
  }
  return {targets, answers:[]};
}
export function submitAnswer(round, answer) {
  if (!directions.includes(answer)) throw new Error('invalid direction');
  if (round.answers.length >= round.targets.length) throw new Error('round complete');
  const target = round.targets[round.answers.length];
  return {...round, answers:[...round.answers, {target, answer, error:angleError(target, answer)}]};
}
export function summarize(round) {
  const total = round.answers.length;
  const correct = round.answers.filter(row => row.error === 0).length;
  return {total, correct, accuracy:total ? correct / total * 100 : 0,
    meanError:total ? round.answers.reduce((sum,row)=>sum+row.error,0)/total : 0,
    complete:total === round.targets.length};
}
export function pulseSamples(sampleRate) {
  // Identical broadband stimulus at every azimuth: only the spatial filter varies.
  const samples = new Float32Array(Math.round(sampleRate * 1.2));
  let seed = 73129;
  for (let i = 0; i < samples.length; i++) {
    seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
    const t = i / sampleRate;
    let envelope = 0;
    for (const onset of [0.06, 0.41, 0.76]) {
      const age = t - onset;
      if (age >= 0 && age < 0.22) envelope += Math.sin(Math.PI * age / 0.22) ** 2;
    }
    samples[i] = (seed / 2147483648 - 1) * envelope;
  }
  return samples;
}
