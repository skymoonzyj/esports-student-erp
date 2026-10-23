import { fireEvent, render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { expect, it } from 'vitest'
import { projectSelection, type CatalogRevision } from '../../tests/helpers/esportsSelection'
import { assessmentTaskLabel } from './hostState'
import { fixtureConfig, fixtureV3Config } from './testSupport/fixtures'

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
