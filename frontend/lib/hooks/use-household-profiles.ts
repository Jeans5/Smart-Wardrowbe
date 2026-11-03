'use client';

import { useQuery } from '@tanstack/react-query';
import { queryKeys } from '@/lib/hooks/query-keys';
import type { HouseholdProfile } from '@/lib/household';

// The list comes from the frontend's own route rather than the backend API, because it is
// frontend configuration (HOUSEHOLD_PROFILES) next to the dev sign-in it drives.
async function fetchHouseholdProfiles(): Promise<HouseholdProfile[]> {
  const response = await fetch('/api/household', { cache: 'no-store' });
  if (!response.ok) return [];
  const body: unknown = await response.json().catch(() => null);
  const profiles = (body as { profiles?: unknown } | null)?.profiles;
  if (!Array.isArray(profiles)) return [];
  return profiles.filter(
    (p): p is HouseholdProfile =>
      !!p && typeof p.name === 'string' && typeof p.email === 'string'
  );
}

export function useHouseholdProfiles() {
  return useQuery({
    queryKey: queryKeys.householdProfiles,
    queryFn: fetchHouseholdProfiles,
    staleTime: Infinity,
    retry: false,
  });
}
