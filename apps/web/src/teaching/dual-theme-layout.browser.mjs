// Real Chromium layout checks for Task 6. Synthetic content only, no API or account access.
// Run from apps/web: node src/teaching/dual-theme-layout.browser.mjs
/* global process, console, window, document, innerWidth, URL, getComputedStyle */
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'
import { createServer } from 'vite'

const webRoot = fileURLToPath(new URL('../../', import.meta.url))
const output = path.resolve(webRoot, '../../.superpowers/sdd/2026-10-27-erp-dual-theme-redesign/task-6-visuals')
const fixture = `
<main>
 <section class="teachingCatalogPage" data-panel="teaching">
  <header class="teachingPageHeader"><div><p class="teachingEyebrow">教学基础</p><h1>教学项目与教练</h1><p class="teachingFoundationDescription">项目教学归属与队伍成员归属独立管理</p></div><button class="teachingPrimaryButton">新增项目</button></header>
  <nav class="teachingSectionNav"><a class="teachingSectionNavLink active" href="#teaching">教学项目</a><a class="teachingSectionNavLink" href="#cohort">营期与班级</a></nav>
  <details class="teachingFilterMenu" open><summary>筛选项目</summary><form class="teachingFilters projectFilters"><label>项目名称<input value="测试项目" /></label><label>状态<select><option>全部状态</option></select></label><button class="teachingSecondaryButton">查询</button></form></details>
  <div class="teachingDataSurface"><div class="teachingTableHeader projectTableGrid"><span>项目</span><span>教练</span><span>状态</span><span>操作</span></div><div class="teachingTableRow projectTableGrid"><div role="cell" data-label="项目"><strong>战术与团队协作教学项目</strong></div><div role="cell" data-label="教练">2 位教练</div><div role="cell" data-label="状态"><span class="teachingStatus active">启用</span></div><div role="cell" data-label="操作" class="teachingRowActions"><button aria-label="编辑项目">编</button><button aria-label="管理教练">教</button><button aria-label="停用项目">停</button></div></div></div>
 </section>
 <section class="teamManagementPage" data-panel="teams"><header class="teamPageHeader"><div><p class="pageEyebrow">队伍管理</p><h1>队伍与成员</h1></div><button class="teamPrimaryButton" id="open-team">新增队伍</button></header><form class="teamFilters"><label>搜索队伍<input value="测试队伍" /></label><label>状态<select><option>全部状态</option></select></label><button>查询</button></form><div class="teamCardGrid"><article class="teamCard"><h2>青训 A 队</h2><p class="teamCardMeta">团队成员归属 · 不授予后台权限</p><div class="teamActionRow"><button>查看成员</button><button>编辑队伍</button></div></article><article class="teamCard"><h2>青训 B 队</h2><p class="teamCardMeta">待分配成员</p><div class="teamActionRow"><button>查看成员</button></div></article></div></section>
 <section class="settingsPage" data-panel="settings"><header class="settingsHeading"><div><p class="pageEyebrow">账号与访问权限</p><h1>账号管理</h1><p>后台权限与人员业务身份分别管理</p></div><button class="primaryButton">开通账号</button></header><div class="settingsToolbar"><label>账号状态</label><select><option>全部状态</option></select><span class="accountCount">共 <strong>1</strong> 个账号</span></div><div class="settingsTable"><div class="settingsTableHeader"><div>姓名</div><div>昵称</div><div>邮箱</div><div>状态</div><div>操作</div></div><div class="settingsTableRow"><div data-label="姓名"><strong>演示管理员</strong></div><div data-label="昵称"><strong>测试昵称</strong></div><div data-label="邮箱"><small>synthetic-account@example.invalid</small></div><div data-label="状态"><span class="statusPill active">启用</span></div><div class="settingsRowActions" data-label="操作"><button>重置密码</button><button class="dangerButton">停用账号</button></div></div></div></section>
 <section data-panel="assisted" class="peoplePage"><header class="settingsHeading"><div><p class="pageEyebrow">人员代办</p><h1>资料待补与监护人信息</h1></div></header><form class="assistedPersonForm"><fieldset class="assistedFormBody"><legend>新增待完善档案</legend><p class="assistedFormNotice">创建档案、账号激活及队伍分配保持独立。</p><fieldset class="assistedGuardian"><legend>监护人信息</legend><label class="formField">监护人姓名<input value="演示监护人" /></label><label class="formField">联系电话<input value="13800000000" /></label></fieldset><button class="primaryButton">保存待补档案</button></fieldset></form></section>
 <section data-panel="claims" class="claimReviewPage"><header class="settingsHeading"><div><p class="pageEyebrow">注册资料处理</p><h1>认领资料审核</h1></div></header><nav class="registrationProcessingTabs"><a href="#claims" class="active">认领审核</a><a href="#assisted">代办请求</a></nav><div class="registrationReviewPanel"><form><label>审核结果<select><option>等待审核</option></select></label><label>审核备注<textarea>合成审核内容，不含生产资料</textarea></label><fieldset class="claimCompletionFields"><legend>资料补全</legend><div class="claimCompletionRead"><dl><div><dt>补全状态</dt><dd>等待监护人确认</dd></div></dl></div></fieldset><div class="claimReviewPagination"><button>上一条</button><button>下一条</button></div></form></div></section>
 <section class="enrollmentPage" data-panel="enrollment"><header class="enrollmentPageHeader"><div><p class="pageEyebrow">报名与缴费</p><h1>课程方案</h1></div><span class="enrollmentCatalogCount">共 <strong>2</strong> 个方案</span></header><section class="enrollmentProgramSection"><header><h2>选择课程方案</h2></header><div class="enrollmentProgramGrid"><article class="selected"><h3>基础训练营</h3><div class="enrollmentProgramPrice"><strong>¥ 1,200</strong><span> / 营期</span></div><p>合成课程内容 · 查看方案后继续报名</p><footer><button>方案详情</button></footer></article><article><h3>进阶训练营</h3><div class="enrollmentProgramPrice"><strong>¥ 2,400</strong></div><footer><button>方案详情</button></footer></article></div></section></section>
 <section class="assistedPeoplePage assistedRequestsPage" data-panel="assisted-requests"><h1>我的建档申请</h1><p>档案审核不代表监护验证、同意或家庭查看授权。</p><table class="assistedRequestTable"><thead><tr><th>申请</th><th>提交人</th><th>身份</th><th>状态</th><th>版本</th></tr></thead><tbody><tr><td data-label="申请"><button>查看申请 1</button></td><td data-label="提交人">synthetic-very-long-submitter-name-for-layout-check</td><td data-label="身份">学员兼队员</td><td data-label="状态">等待审核</td><td data-label="版本">1</td></tr></tbody></table><nav aria-label="申请分页"><button>上一页</button><span>第 1 页</span><button>下一页</button></nav></section>
</main><div id="dialog-host"></div>`

const entry = `
import React from 'react'; import { createRoot } from 'react-dom/client';
import { TeamDialog } from '/src/teams/TeamDialog.tsx';
import '/src/styles/tokens.css'; import '/src/styles/global.css';
import '/src/teaching/teaching.css'; import '/src/teams/teams.css';
import '/src/assistedPeople/assisted-people.css'; import '/src/profileClaims/profileClaims.css';
import '/src/enrollment/enrollment.css'; import '/src/settings/settings.css';
import '/src/registration/registrationProcessing.css';
const root = createRoot(document.getElementById('dialog-host'));
const close = () => root.render(null);
document.getElementById('open-team').onclick = () => root.render(React.createElement(TeamDialog, { title: '新增队伍', pending: false, onClose: close },
 React.createElement('header', {className:'teamDialogHeader'}, React.createElement('h2',null,'新增队伍'),React.createElement('button',{onClick:close},'关闭')),
 React.createElement('label',null,'队伍名称',React.createElement('input',{defaultValue:'合成青训队'})),
 React.createElement('label',null,'说明',React.createElement('textarea',{defaultValue:'队伍成员归属与教学项目、后台权限相互独立。'})),
 React.createElement('div',{className:'teamDialogActions'},React.createElement('button',{onClick:close},'取消'),React.createElement('button',{className:'teamPrimaryButton'},'保存队伍'))));
window.fixtureReady = true;
`
const server = await createServer({ root: webRoot, server: { host: '127.0.0.1', port: 0 }, plugins: [{
 name: 'task6-layout-fixture', resolveId(id) { if (id === 'virtual:task6-layout') return '\0task6-layout' },
 load(id) { if (id === '\0task6-layout') return entry },
 configureServer(s) { s.middlewares.use('/__task6', async (_req, res) => { res.setHeader('Content-Type','text/html; charset=utf-8'); res.end(await s.transformIndexHtml('/__task6', `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><style>body{margin:0}main{padding:24px;display:grid;gap:48px}main>section{min-width:0;width:100%}button,input,select,textarea{font:inherit}@media(max-width:600px){main{padding:16px;gap:36px}}</style></head><body>${fixture}<script type="module" src="/@id/virtual:task6-layout"></script></body></html>`)) }) },
}] })
await server.listen()
const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || (process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : undefined)
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true })
const page = await browser.newPage()
const errors = []
page.on('pageerror', error => errors.push(error.message))
await mkdir(output, { recursive: true })
try {
 await page.goto(`${server.resolvedUrls.local[0]}__task6`)
 await page.waitForFunction(() => window.fixtureReady)
 await page.locator('.teachingPrimaryButton').waitFor({ state:'visible' })
 const failures = []
 for (const theme of ['clear','command']) for (const width of [320,390,720,768,960,1440]) {
  await page.setViewportSize({ width, height: 900 })
  await page.evaluate(theme => document.documentElement.dataset.uiTheme = theme, theme)
  await page.evaluate(() => document.activeElement?.blur())
  const result = await page.evaluate(() => {
   const tooSmall = [...document.querySelectorAll('main button,main input,main select,main textarea,main nav a')].filter(el => el.getClientRects().length && el.getBoundingClientRect().height < 43.5).map(el => `${el.className || el.tagName}: ${el.getBoundingClientRect().height}`)
   const overflow = document.documentElement.scrollWidth > innerWidth
   const dangerDistinct = getComputedStyle(document.querySelector('.settingsRowActions .dangerButton')).color !== getComputedStyle(document.querySelector('.settingsRowActions button')).color
   const clippedActions = [...document.querySelectorAll('.teachingRowActions button')].filter(el => {
    const action = el.getBoundingClientRect(), surface = el.closest('.teachingDataSurface').getBoundingClientRect()
    return action.left < surface.left - .5 || action.right > surface.right + .5 || action.top < surface.top - .5 || action.bottom > surface.bottom + .5 || action.left < 0 || action.right > innerWidth
   }).map(el=>el.getAttribute('aria-label'))
   return { tooSmall, overflow, dangerDistinct, clippedActions, gridGap: parseFloat(getComputedStyle(document.querySelector('.teamCardGrid')).gap), header: parseFloat(getComputedStyle(document.querySelector('.settingsHeading h1')).fontSize) }
  })
  if (result.overflow || !result.dangerDistinct || result.clippedActions.length || result.tooSmall.length || result.header > 32 || (width >= 768 && theme === 'command' && result.gridGap !== 0)) failures.push({ theme,width,...result })
  await page.screenshot({ path: `${output}/${theme}-${width}.png`, fullPage:true })
  if ([320,1440].includes(width)) for (const panel of ['teaching','teams','settings','assisted-requests']) await page.locator(`[data-panel="${panel}"]`).screenshot({path:`${output}/${theme}-${width}-${panel}.png`})
  await page.getByRole('button',{ name:'新增队伍',exact:true }).click()
  const dialog = page.getByRole('dialog',{name:'新增队伍'})
  await dialog.locator('input').fill('切换主题时保留输入')
  await page.evaluate(() => document.documentElement.dataset.uiTheme = document.documentElement.dataset.uiTheme === 'command' ? 'clear':'command')
  assert.equal(await dialog.locator('input').inputValue(), '切换主题时保留输入')
  const bounds = await dialog.boundingBox()
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1, 'dialog remains within viewport')
  if (width <= 600) assert.ok(Math.abs(bounds.y + bounds.height - 900) < 2, 'mobile team dialog stays bottom-aligned')
  await dialog.getByRole('button',{name:'保存队伍'}).focus()
  await page.keyboard.press('Tab')
  assert.equal(await dialog.getByRole('button',{name:'关闭'}).evaluate(el => document.activeElement === el),true,'real TeamDialog retains keyboard focus')
  await page.evaluate(theme => document.documentElement.dataset.uiTheme = theme, theme)
  await page.screenshot({ path:`${output}/${theme}-${width}-team-dialog.png` })
  await page.keyboard.press('Escape')
  await dialog.waitFor({state:'detached'})
  assert.equal(await page.getByRole('button',{name:'新增队伍',exact:true}).evaluate(el => document.activeElement === el),true,'dialog restores focus')
 }
 assert.deepEqual(errors, [], 'no browser runtime errors')
 assert.deepEqual(failures, [], '44px targets, no horizontal overflow, restrained headings and connected command layout')
 console.log('PASS: 12 theme/viewport layouts; full teaching-action containment; real TeamDialog input retention, focus trap, Escape/restore; 40 synthetic screenshots.')
} finally { await browser.close(); await server.close() }
