import { isDateInput, todayDateInput } from "../shared/date"
import { currentAge } from "../people/presentation"

export const identityDocumentTypes = ["cn_id", "hk_id", "hk_mo_permit"] as const
export const personDocumentTypes = [...identityDocumentTypes, "passport"] as const
export type IdentityDocumentType = typeof identityDocumentTypes[number]
export type PersonDocumentType = IdentityDocumentType | "passport"

const cnWeights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
/** Only a checksum-valid, non-future CN identity carries demographic facts. */
export function cnDocumentFacts(value: string, today = todayDateInput()) {
  const number = value.trim().toUpperCase()
  if (!/^\d{17}[\dX]$/.test(number)) return null
  const date_of_birth = `${number.slice(6, 10)}-${number.slice(10, 12)}-${number.slice(12, 14)}`
  const sum = [...number.slice(0, 17)].reduce((total, digit, index) => total + Number(digit) * cnWeights[index], 0)
  if (!isDateInput(date_of_birth) || date_of_birth > today || number[17] !== "10X98765432"[sum % 11]) return null
  return { date_of_birth, sex: Number(number[16]) % 2 ? "male" as const : "female" as const }
}

/** HKID has no encoded birthday or sex. Return only its normalized number. */
export function normalizeHkId(value: string): string | null {
  const number = value.replace(/\s/g, "").replace(/（([0-9Aa])）$/, "($1)")
  const match = /^([A-Za-z]{1,2})([0-9]{6})(?:([0-9Aa])|\(([0-9Aa])\))$/.exec(number)
  if (!match) return null
  const [, rawLetters, digits, bareCheck, bracketCheck] = match
  const letters = rawLetters.toUpperCase()
  const check = (bareCheck ?? bracketCheck).toUpperCase()
  const letterValue = (letter: string) => letter.charCodeAt(0) - 55
  let sum = letters.length === 1 ? 36 * 9 + letterValue(letters[0]) * 8 : letterValue(letters[0]) * 9 + letterValue(letters[1]) * 8
  sum += [...digits].reduce((total, digit, index) => total + Number(digit) * (7 - index), 0)
  sum += check === "A" ? 10 : Number(check)
  return sum % 11 === 0 ? `${letters}${digits}${check}` : null
}

/** A return permit encodes no demographic facts; renewal counts are not identity. */
export function normalizeHkMoPermit(value: string): string | null {
  const match = /^([HhMm][0-9]{8})(?:[0-9]{2})?$/.exec(value.trim())
  return match ? match[1].toUpperCase() : null
}

export function normalizeIdentityDocument(value: string, type: IdentityDocumentType = "cn_id"): string | null {
  return type === "hk_mo_permit" ? normalizeHkMoPermit(value) : type === "hk_id" ? normalizeHkId(value) : cnDocumentFacts(value) ? value.trim().toUpperCase() : null
}

export function documentDemographicNotice(type?: PersonDocumentType): string {
  return type === "hk_id" ? "香港身份证不包含生日或性别，请单独填写。" : type === "hk_mo_permit" ? "回乡证不推导生日或性别，请单独填写。" : ""
}

export function documentTypeLabel(type?: PersonDocumentType): string {
  return type === "hk_mo_permit" ? "回乡证（港澳居民来往内地通行证）" : type === "hk_id" ? "香港身份证" : type === "passport" ? "护照" : "中国居民身份证"
}
export function documentNumberLabel(type?: PersonDocumentType): string {
  return type === "hk_mo_permit" ? "回乡证号码" : type === "hk_id" ? "香港身份证号码" : type === "passport" ? "护照号码" : "身份证"
}
export function birthdayAgeText(birth: string | null | undefined): string {
  return birth && isDateInput(birth) && birth <= todayDateInput() ? `年龄：${currentAge(birth)} 周岁` : "年龄待补"
}
