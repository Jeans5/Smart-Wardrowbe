import { describe, it, expect } from 'vitest'
import { householdProfilesFromEnv, parseHouseholdProfiles } from '@/lib/household'

describe('parseHouseholdProfiles', () => {
  it('gives each bare name a home.arpa address', () => {
    expect(parseHouseholdProfiles('Levi, Julie ,Sienna')).toEqual([
      { name: 'Levi', email: 'levi@home.arpa' },
      { name: 'Julie', email: 'julie@home.arpa' },
      { name: 'Sienna', email: 'sienna@home.arpa' },
    ])
  })

  it('keeps an explicit address after the colon', () => {
    expect(parseHouseholdProfiles('Julie:julie@example.com')).toEqual([
      { name: 'Julie', email: 'julie@example.com' },
    ])
  })

  it('slugs accents and spaces out of the generated address', () => {
    expect(parseHouseholdProfiles('Zoë Van Den Broeck')).toEqual([
      { name: 'Zoë Van Den Broeck', email: 'zoe-van-den-broeck@home.arpa' },
    ])
  })

  it('skips blanks, names without letters or digits, and duplicate addresses', () => {
    expect(parseHouseholdProfiles(' , ✨ ,Levi,levi')).toEqual([{ name: 'Levi', email: 'levi@home.arpa' }])
  })

  it('returns nothing for an unset variable', () => {
    expect(parseHouseholdProfiles(undefined)).toEqual([])
    expect(parseHouseholdProfiles('')).toEqual([])
  })
})

describe('householdProfilesFromEnv', () => {
  it('stays off unless the passwordless dev sign-in is on', () => {
    const env = { HOUSEHOLD_PROFILES: 'Levi', NODE_ENV: 'production' } as unknown as NodeJS.ProcessEnv
    expect(householdProfilesFromEnv(env)).toEqual([])
    expect(householdProfilesFromEnv({ ...env, DEV_MODE: 'true' })).toEqual([
      { name: 'Levi', email: 'levi@home.arpa' },
    ])
  })
})
