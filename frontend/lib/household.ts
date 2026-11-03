// Household profiles turn the dev sign-in into a "who is getting dressed?" picker, for a shared
// tablet where each family member should switch with one tap. They are read from the frontend's
// HOUSEHOLD_PROFILES variable, a comma-separated list of names with an optional address after a
// colon: "Levi, Julie:julie@example.com, Sienna". A name without an address gets
// <slug>@home.arpa, the special-use domain for home networks, which the backend accepts.
//
// The picker only works where the dev sign-in does (no password), so it is meant for a private
// home network and stays off unless that sign-in is enabled.

export interface HouseholdProfile {
  name: string;
  email: string;
}

export const HOUSEHOLD_EMAIL_DOMAIN = 'home.arpa';
const MAX_PROFILES = 12;

function slugify(name: string): string {
  return name
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function parseHouseholdProfiles(raw: string | undefined | null): HouseholdProfile[] {
  if (!raw) return [];
  const profiles: HouseholdProfile[] = [];
  const seen = new Set<string>();

  for (const entry of raw.split(',')) {
    const [rawName, ...rest] = entry.split(':');
    const name = rawName.trim();
    if (!name) continue;
    const explicitEmail = rest.join(':').trim();
    const slug = slugify(name);
    const email = explicitEmail || (slug ? `${slug}@${HOUSEHOLD_EMAIL_DOMAIN}` : '');
    if (!email.includes('@')) continue;
    const key = email.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    profiles.push({ name, email });
    if (profiles.length >= MAX_PROFILES) break;
  }
  return profiles;
}

export function devSignInEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.DEV_MODE === 'true' || env.NODE_ENV === 'development';
}

export function householdProfilesFromEnv(env: NodeJS.ProcessEnv = process.env): HouseholdProfile[] {
  return devSignInEnabled(env) ? parseHouseholdProfiles(env.HOUSEHOLD_PROFILES) : [];
}
