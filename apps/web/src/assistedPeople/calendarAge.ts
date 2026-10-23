/** Canonical backend policy: a leap-day eighteenth birthday falls on February 28. */
export function isAssistedAdult(birth: string, today: string): boolean {
  const year = Number(birth.slice(0, 4)) + 18
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const monthDay = birth.slice(4) === "-02-29" && !leapYear ? "-02-28" : birth.slice(4)
  return today >= `${year}${monthDay}`
}
