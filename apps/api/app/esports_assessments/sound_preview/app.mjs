import {directions, labels, createRound, submitAnswer, summarize} from './core.mjs';
import {CuePlayer} from './audio.mjs';

const $ = id => document.getElementById(id);
let round = null;
let phase = 'setup';
let busy = false;
let heard = false;
let generation = 0;
const checks = {left:false,right:false};
const player = new CuePlayer(interrupt);
const name = angle => labels[directions.indexOf(angle)];
const buttons = directions.map((angle, i) => {
  const button = document.createElement('button');
  button.className='direction';button.dataset.angle=angle;
  button.textContent=labels[i];button.disabled=true;
  const radians=angle*Math.PI/180;
  button.style.left=`${50+44*Math.sin(radians)}%`;
  button.style.top=`${50-44*Math.cos(radians)}%`;
  button.addEventListener('click',()=>answer(angle));
  $('compass').append(button);
  $('progress').append(document.createElement('span'));
  return button;
});
function sync() {
  const calibrated=checks.left && checks.right && $('confirmed').checked && $('device').value && Number($('volume').value)>0;
  $('start').disabled=!calibrated || busy;
  $('left').disabled=busy; $('right').disabled=busy;
  $('device').disabled=busy; $('confirmed').disabled=busy;
  $('stop').disabled=!busy;
  $('stop-calibration').disabled=!busy;
  $('play').disabled=phase!=='trial' || busy;
  $('play').hidden=phase==='feedback' || phase==='complete';
  $('next').hidden=phase!=='feedback';
  for (const button of buttons) button.disabled=phase!=='trial' || !heard || busy;
  $('stage').classList.toggle('playing',busy && phase==='trial');
}
async function play(kind) {
  if (busy || (kind==='spatial' && phase!=='trial')) return;
  if (Number($('volume').value)===0) {
    $(kind==='spatial' ? 'trial-status' : 'audio-status').textContent='当前试听音量为 0，请先调整到舒适的音量。';return;
  }
  const token=++generation;
  busy=true;heard=false;
  const status=$(kind==='spatial'?'trial-status':'audio-status');
  status.textContent=kind==='spatial'?'正在播放，请仔细听……':'正在试听，请确认声音来自对应耳朵……';
  sync();
  try {
    const played=await player.play({kind,angle:round?.targets[round.answers.length] ?? 0,volume:Number($('volume').value)/100});
    if (token!==generation || !played) return;
    if (kind==='spatial') {
      heard=true;status.textContent='声音播放完成，请选择你听到的方向。';$('play').textContent='再听一次';
    } else {
      checks[kind]=true;status.textContent=kind==='left'?'左耳试听完成':'右耳试听完成';
      $(kind).textContent=kind==='left'?'✓ 左耳已试听':'✓ 右耳已试听';
    }
  } catch (error) {
    if (token===generation) status.textContent=error.message || '声音播放失败，请重试或更换浏览器。';
  } finally {
    if (token===generation) {busy=false;sync();}
  }
}
function interrupt() {
  if (!busy && !(phase==='trial' && heard)) return;
  generation++;player.stop();busy=false;heard=false;
  $(phase==='setup'?'audio-status':'trial-status').textContent='声音已停止或页面被切换，请重新播放后再作答。';sync();
}
function prepareTrial() {
  phase='trial';heard=false;
  $('stage-title').textContent='声音来自哪个方向？';
  $('counter').textContent=`第 ${round.answers.length+1} / 8 题`;
  $('trial-status').textContent='面朝屏幕上方，点击播放。';
  $('feedback').textContent='听完后选择方向；作答后才会显示真实声源。';
  $('play').textContent='播放声音';
  buttons.forEach(button=>button.classList.remove('correct','wrong'));
  [...$('progress').children].forEach((step,i)=>{step.className=i<round.answers.length?'done':i===round.answers.length?'current':'';});
  sync();
}
function answer(angle) {
  if (phase!=='trial' || !heard || busy) return;
  round=submitAnswer(round,angle);phase='feedback';heard=false;
  const row=round.answers.at(-1);
  $('feedback').textContent=`实际方向：${name(row.target)} · 你的选择：${name(row.answer)} · 偏差 ${row.error}°`;
  buttons.find(button=>Number(button.dataset.angle)===row.target).classList.add('correct');
  if (row.error) buttons.find(button=>Number(button.dataset.angle)===row.answer).classList.add('wrong');
  $('trial-status').textContent=row.error===0?'本题方向判断正确。':'已记录你的判断。';
  $('next').textContent=round.answers.length===8?'查看本轮记录 →':'下一题 →';sync();
}
function finish() {
  phase='complete';player.stop();
  const summary=summarize(round);
  $('results').hidden=false;
  $('correct').textContent=`${summary.correct} / ${summary.total}`;
  $('accuracy').textContent=`${summary.accuracy.toFixed(1)}%`;
  $('error').textContent=`${summary.meanError.toFixed(1)}°`;
  $('result-device').textContent=`设备（自行选择）：${$('device').selectedOptions[0].textContent}。播放次数不限、耗时不计分；不同耳机和设备的结果不直接比较。`;
  $('rows').replaceChildren(...round.answers.map((row,i)=>{
    const tr=document.createElement('tr');
    for (const value of [i+1,name(row.target),name(row.answer),`${row.error}°`]) {
      const td=document.createElement('td');td.textContent=value;tr.append(td);
    }
    return tr;
  }));
  $('counter').textContent='8 / 8 已完成';$('stage-title').textContent='这一轮试听完成';
  $('trial-status').textContent='向下查看方向判断记录。';
  [...$('progress').children].forEach(step=>step.className='done');
  sync();$('results').scrollIntoView({behavior:'smooth',block:'start'});
}
function clearChecks() {
  checks.left=false;checks.right=false;$('confirmed').checked=false;
  $('left').textContent='试听左耳';$('right').textContent='试听右耳';sync();
}
$('left').addEventListener('click',()=>play('left'));
$('right').addEventListener('click',()=>play('right'));
$('device').addEventListener('change',clearChecks);
$('confirmed').addEventListener('change',sync);
$('volume').addEventListener('input',()=>{
  interrupt();
  const volume=Number($('volume').value);$('volume-value').textContent=`${volume}%`;player.setVolume(volume/100);sync();
});
$('start').addEventListener('click',()=>{
  if ($('start').disabled) return;
  round=createRound();$('setup').hidden=true;$('results').hidden=true;prepareTrial();$('stage').scrollIntoView({behavior:'smooth',block:'start'});
});
$('play').addEventListener('click',()=>play('spatial'));
$('stop').addEventListener('click',interrupt);
$('stop-calibration').addEventListener('click',interrupt);
$('next').addEventListener('click',()=>{if(phase==='feedback') round.answers.length===8?finish():prepareTrial();});
$('restart').addEventListener('click',()=>{
  generation++;player.stop();round=null;phase='setup';busy=false;heard=false;
  $('setup').hidden=false;$('results').hidden=true;$('play').textContent='播放声音';
  $('stage-title').textContent='准备好，让声音带路';$('counter').textContent='等待开始';
  $('trial-status').textContent='完成耳机检查后开始新一轮。';$('feedback').textContent='播放完成后，点击圆盘上的方向作答。';
  $('audio-status').textContent='请重新确认左右声道。';clearChecks();
  buttons.forEach(button=>button.classList.remove('correct','wrong'));
  [...$('progress').children].forEach(step=>step.className='');
  $('setup').scrollIntoView({behavior:'smooth',block:'start'});
});
window.addEventListener('blur',interrupt);
document.addEventListener('visibilitychange',()=>{if(document.hidden) interrupt();});
window.addEventListener('pagehide',()=>{interrupt();generation++;player.close();busy=false;heard=false;sync();});
sync();
