import assert from 'node:assert/strict'
import { log } from 'node:console'
import { readFile } from 'node:fs/promises'
import process from 'node:process'
import { URL } from 'node:url'
import { chromium } from '@playwright/test'

// Isolated presentation fixtures: no authentication, operational data or mutations.
// Integration screenshots of complete routed pages are owned by the coordinator.
const cssFiles = ['../styles/tokens.css', '../styles/global.css', '../people/people.css', '../teaching/teaching.css', './assets.css', '../procurement/procurement.css', '../revenueShare/revenue-share.css', '../admissions/admissions.css']
const css = (await Promise.all(cssFiles.map((file) => readFile(new URL(file, import.meta.url), 'utf8')))).join('\n')
const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
const sections = `
  <section class="assetsPage"><header class="assetsPageHeader"><div><p class="pageEyebrow">ASSET DIRECTORY</p><h1>资产管理</h1><p>归属、租赁与保修信息</p></div></header><div class="assetStats">${['资产总数', '可领用', '已领用', '维修中'].map((label) => `<div class="assetStat"><span>${label}</span><strong>128</strong></div>`).join('')}</div><div class="assetDirectory"><div class="assetTableRow"><div><strong>电竞显示器 · 超长设备名称显示测试</strong><small>ASSET-000123</small></div><div>显示设备</div><div>校区运营中心</div><div><span class="assetStatus available">可领用</span></div><div><button class="assetActionButton">查看保修</button></div></div></div></section>
  <section class="procurementPage"><div class="procurementSummary">${['采购总额', '已报销', '未报销'].map((label) => `<div><span>${label}</span><strong>¥12,345,678.90</strong></div>`).join('')}</div><div class="procurementTableWrap" role="region" aria-label="采购台账，可横向滚动" tabindex="0"><table class="procurementTable"><thead><tr>${['采购日期', '物品', '渠道', '数量', '单价', '总价', '结算中心', '资金来源', '报销', '结算', '发票', '操作'].map((label) => `<th>${label}</th>`).join('')}</tr></thead><tbody><tr><td>2026-10-27</td><td>训练设备</td><td>线上</td><td>12</td><td>¥99,999.00</td><td><strong>¥1,199,988.00</strong></td><td>运营中心</td><td>备用金</td><td>未报销</td><td>未结算</td><td>未开票</td><td><button class="procurementTextButton">登记报销</button></td></tr></tbody></table></div></section>
  <section class="revenueSharePage"><div class="revenueShareStats">${['累计应分', '已结算', '未结算', '待分配付款'].map((label) => `<div><span>${label}</span><strong>¥123,456.78</strong></div>`).join('')}</div><div class="revenueShareTableWrap"><table class="revenueShareTable"><tbody><tr><td>机构收款对象</td><td>机构</td><td class="financeAmount">¥123,456.78</td><td class="financeAmount">¥0.00</td><td class="financeAmount">¥123,456.78</td><td><button class="iconTextButton">登记打款</button></td></tr></tbody></table></div></section>
  <section class="paymentsPage"><div class="admissionsTabs"><button aria-selected="true">付款记录</button><button>线下订单</button><button>冲突处理</button></div><div class="admissionsDataTable paymentsTable"><div class="admissionsTableRow">${['张同学', '训练营学费', '¥123,456.78', '已支付', 'ORDER-12345678901234567890', '详情'].map((label, index) => `<div role="cell" data-label="${['学员', '课程', '金额', '状态', '单号', '操作'][index]}" class="${index === 2 ? 'amountCell' : ''}">${label}</div>`).join('')}</div></div></section>`
const dialogFixtures = [
  ['assetDialogBackdrop', 'assetDialog', 'assetDialogForm', 'assetField', 'assetDialogActions'],
  ['procurementDialogBackdrop', 'procurementDialog', 'procurementForm', 'procurementFilterField', 'procurementDialogActions'],
  ['revenueShareDialogBackdrop', 'revenueShareDialog', 'revenueShareDialogForm', '', 'revenueShareDialogActions'],
  ['admissionsDialogBackdrop', 'admissionsDialog', 'admissionsDialogForm', '', 'admissionDialogActions'],
]
try {
  for (const theme of ['clear', 'command']) {
    for (const width of [320, 390, 768, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, reducedMotion: 'reduce' })
      await page.setContent(`<!doctype html><html lang="zh-CN" data-ui-theme="${theme}"><head><style>${css} body { margin:0; padding:16px; } main { min-width:0; display:grid; gap:24px; }</style></head><body><main>${sections}</main></body></html>`)
      assert.equal(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth), true, `${theme}/${width}: page must not overflow horizontally`)
      for (const selector of ['.assetActionButton', '.procurementTextButton', '.iconTextButton', '.admissionsTabs button']) {
        assert.ok((await page.locator(selector).first().boundingBox()).height >= 44, `${theme}/${width}: ${selector} target must be 44px`)
      }
      assert.equal(await page.locator('.procurementTableWrap').evaluate((el) => el.scrollWidth > el.clientWidth), width < 1440, `${theme}/${width}: wide ledger must scroll inside its own region`)
      assert.equal(await page.locator('.procurementTable td').nth(5).evaluate((el) => globalThis.getComputedStyle(el).textAlign), 'right')
      assert.equal(await page.locator('.procurementTable td').nth(5).evaluate((el) => el.scrollWidth <= el.clientWidth), true, `${theme}/${width}: monetary total must fit its cell`)
      assert.equal(await page.locator('.financeAmount').first().evaluate((el) => globalThis.getComputedStyle(el).textAlign), 'right')
      if (theme === 'command') assert.equal(await page.locator('.procurementSummary').evaluate((el) => globalThis.getComputedStyle(el).gap), '0px', 'command summary must be a connected strip')
      for (const [backdrop, dialog, form, field, actions] of dialogFixtures) {
        const warning = dialog === 'assetDialog' ? '<div class="rentalPreview"><strong>资产 ASSET-123 · 租赁续期</strong><span>2026-10-27 → 2027-10-27</span><p class="rentalWarning">请核对原到期日和新到期日，确认后继续。</p></div>' : '<div class="enrollmentOperationWarning"><p>请核对收款对象、金额与凭证。操作将保留审计记录。</p></div>'
        const labels = ['金额（元）', '收款对象', '凭证号', '登记日期', '结算中心', '备用金来源', '发票号', '发票金额', '备注', '经办人']
        const fields = labels.map((label) => `<label class="${field}"><span>${label}</span><input value="${label === '金额（元）' ? '1234567.89' : '测试展示内容'}"></label>`).join('')
        await page.locator('body').evaluate((body, markup) => body.insertAdjacentHTML('beforeend', markup), `<div class="${backdrop}" data-check-dialog><section class="${dialog}" role="dialog" aria-label="财务确认"><h2>确认财务操作</h2><p>合计 ¥1,234,567.89</p>${warning}<form class="${form}">${fields}<div class="${actions}"><button class="secondaryButton" type="button">取消</button><button class="primaryButton" type="button">确认登记</button></div></form></section></div>`)
        const box = await page.getByRole('dialog').boundingBox()
        assert.ok(box.x >= 0 && box.x + box.width <= width + 1 && box.y >= 0 && box.y + box.height <= 901, `${theme}/${width}: ${dialog} must fit viewport`)
        assert.equal(await page.getByRole('dialog').evaluate((el) => el.scrollWidth <= el.clientWidth), true, `${theme}/${width}: ${dialog} must not clip its form`)
        assert.ok((await page.getByRole('dialog').getByRole('textbox').first().boundingBox()).height >= 44, `${theme}/${width}: ${dialog} inputs must be 44px`)
        await page.getByRole('button', { name: '确认登记', exact: true }).scrollIntoViewIfNeeded()
        assert.equal(await page.getByRole('button', { name: '确认登记', exact: true }).isVisible(), true)
        if (process.env.LAYOUT_SCREENSHOT_DIR && [320, 1440].includes(width)) await page.screenshot({ path: `${process.env.LAYOUT_SCREENSHOT_DIR}/${dialog}-${theme}-${width}.png` })
        await page.locator('[data-check-dialog]').evaluate((el) => el.remove())
      }
      if (process.env.LAYOUT_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.LAYOUT_SCREENSHOT_DIR}/finance-${theme}-${width}.png`, fullPage: true })
      log(`PASS ${theme} ${width}px: contained tables, totals, targets and 4 dialogs`)
      await page.close()
    }
  }
} finally {
  await browser.close()
}
