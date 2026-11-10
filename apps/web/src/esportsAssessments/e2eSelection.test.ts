import { fireEvent, render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { expect, it } from 'vitest'
import { projectSelection, type CatalogRevision } from '../../tests/helpers/esportsSelection'
import { assessmentTaskLabel } from './hostState'
import { fixtureConfig, fixtureV3Config } from './testSupport/fixtures'
import { fixtureV4Config } from './testSupport/fixturesV4'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import { fixtureMobaV5Config } from './testSupport/fixturesMobaV5'

it.each([4, 5] as const)('selects the exact five-minute lane task for revision %s', revision => {
  let selected = false
  const config = revision === 4 ? fixtureMobaV4Config() : fixtureMobaV5Config()
  render(createElement('button', { onClick: () => { selected = true } }, assessmentTaskLabel(config)))
  fireEvent.click(screen.getByRole('button', projectSelection('last-hit', revision)))
  expect(selected).toBe(true)
})

it.each([
  ['pc-moba', 1], ['pc-moba', 2], ['pc-moba', 3],
  ['mobile-moba', 1], ['mobile-moba', 2], ['mobile-moba', 3],
] as const)('selects the exact production dodge button for %s v%s', (suite, revision) => {
  const config = revision === 3 ? fixtureV3Config(suite, 'dodge') : {
    ...fixtureConfig(suite, 'dodge'), engine_version: `${suite}-v${revision}`,
    catalog_version: `${suite}-v${revision}`,
  }
  let selected = false
  render(createElement('button', { onClick: () => { selected = true } }, assessmentTaskLabel(config)))
  fireEvent.click(screen.getByRole('button', projectSelection('dodge', revision as CatalogRevision)))
  expect(selected).toBe(true)
})

it('still selects an unchanged v3 project with an exact label', () => {
  let selected = false
  render(createElement('button', { onClick: () => { selected = true } }, assessmentTaskLabel(fixtureV3Config('mobile-fps', 'move-fire'))))
  fireEvent.click(screen.getByRole('button', projectSelection('move-fire')))
  expect(selected).toBe(true)
})

it.each([
  ['gridshot', '网格射击'], ['sixshot', '六目标射击'], ['spheretrack90', '90°范围跟枪'],
] as const)('selects current v4 %s with its exact production label', (testId, label) => {
  let selected = false
  render(createElement('button', { onClick: () => { selected = true } }, assessmentTaskLabel(fixtureV4Config('pc-fps', testId))))
  expect(projectSelection(testId, 4)).toEqual({ name: label, exact: true })
  fireEvent.click(screen.getByRole('button', projectSelection(testId, 4)))
  expect(selected).toBe(true)
})
