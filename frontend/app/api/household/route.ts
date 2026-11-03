import { NextResponse } from 'next/server';
import { householdProfilesFromEnv } from '@/lib/household';

export const runtime = 'nodejs';
// Read per request so that HOUSEHOLD_PROFILES can change without rebuilding the image.
export const dynamic = 'force-dynamic';

export function GET() {
  return NextResponse.json({ profiles: householdProfilesFromEnv() });
}
