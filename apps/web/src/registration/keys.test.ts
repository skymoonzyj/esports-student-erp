import { expect, it } from "vitest"

import { registrationKeys, registrationRoot } from "./keys"

it("builds stable isolated registration query keys", () => {
  expect(registrationRoot()).toEqual(["registration"])
  expect(registrationKeys.self()).toEqual(["registration", "self"])
  expect(registrationKeys.minorGuardianInvitations()).toEqual([
    "registration",
    "self",
    "guardian-invitations",
  ])
  expect(registrationKeys.accountIdentifiers("account-1")).toEqual([
    "registration",
    "account",
    "account-1",
    "identifiers",
  ])
  expect(registrationKeys.adminRoot()).toEqual(["registration", "admin"])
  expect(registrationKeys.adminChannelSettings()).toEqual([
    "registration",
    "admin",
    "channel-settings",
  ])
  expect(registrationKeys.adminApplications()).toEqual([
    "registration",
    "admin",
    "applications",
  ])
  expect(registrationKeys.adminApplication("application-1", true)).toEqual([
    "registration",
    "admin",
    "applications",
    "application-1",
    { sensitive: true },
  ])
  expect(registrationKeys.staffAdminRoot()).toEqual([
    "registration",
    "staff-admin",
  ])
})
