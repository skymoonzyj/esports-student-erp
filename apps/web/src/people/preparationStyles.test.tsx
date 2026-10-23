import { readFileSync } from 'node:fs'
import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'

it.each([
  ['peoplePage', 'subtleStatus', 'fontSize', '11px'],
  ['personDetailPage', 'subtleStatus', 'fontSize', '11px'],
  ['personFormPage', 'formSection', 'padding', '20px'],
  ['assistedPersonForm', 'formSection', 'padding', '20px'],
  ['personFormPage', 'formSubmitError', 'padding', '12px 14px'],
  ['guardianPanel', 'formSubmitError', 'padding', '12px 14px'],
  ['assistedPersonForm', 'formSubmitError', 'padding', '12px 14px'],
] as const)('preserves %s %s legitimate owner styling during preparation', (owner, child, property, value) => {
  const style = document.createElement('style')
  style.textContent = readFileSync('src/people/people.css', 'utf8')
  document.head.append(style)
  try {
    render(<section className={owner}><p className={child}>Owner content</p><input aria-label="owner draft" defaultValue="retained" /></section>)
    expect(getComputedStyle(screen.getByText('Owner content'))[property]).toBe(value)
    expect(screen.getByLabelText('owner draft')).toHaveValue('retained')
  } finally { style.remove() }
})
