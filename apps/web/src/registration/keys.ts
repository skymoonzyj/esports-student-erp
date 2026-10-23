export const registrationRoot = () => ["registration"] as const

export const registrationKeys = {
  root: registrationRoot,
  self: () => [...registrationRoot(), "self"] as const,
  minorGuardianInvitations: () =>
    [...registrationRoot(), "self", "guardian-invitations"] as const,
  registrationTermsConsent: () =>
    [...registrationRoot(), "self", "registration-terms-consent"] as const,
  adultGuardianInvitations: (accountId: string) =>
    [
      ...registrationRoot(),
      "account",
      accountId,
      "guardian-invitations",
    ] as const,
  accountIdentifiers: (accountId: string) =>
    [...registrationRoot(), "account", accountId, "identifiers"] as const,
  staffApplication: (accountId: string) =>
    [...registrationRoot(), "account", accountId, "staff-application"] as const,
  adminRoot: () => [...registrationRoot(), "admin"] as const,
  adminChannelSettings: () =>
    [...registrationRoot(), "admin", "channel-settings"] as const,
  adminApplications: () =>
    [...registrationRoot(), "admin", "applications"] as const,
  adminApplication: (applicationId: string, sensitive: boolean) =>
    [
      ...registrationRoot(),
      "admin",
      "applications",
      applicationId,
      { sensitive },
    ] as const,
  staffAdminRoot: () => [...registrationRoot(), "staff-admin"] as const,
  staffAdminApplications: () =>
    [...registrationRoot(), "staff-admin", "applications"] as const,
  staffAdminApplication: (applicationId: string, sensitive: boolean) =>
    [
      ...registrationRoot(),
      "staff-admin",
      "applications",
      applicationId,
      { sensitive },
    ] as const,
}
