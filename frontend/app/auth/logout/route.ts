import { NextResponse, type NextRequest } from 'next/server';
import { clearSessionCookies } from '@/lib/session-cookies';

// loggedOut=1 stops the login page from signing straight back in through a proxy session
// that outlived the logout.
function logoutTarget(loggedOutUrl: string) {
  const forwardAuthLogoutUrl = process.env.FORWARD_AUTH_LOGOUT_URL;
  const tinyAuthUrl = process.env.TINYAUTH_URL?.replace(/\/+$/, '');
  const endSessionUrl = process.env.OIDC_END_SESSION_URL;

  if (forwardAuthLogoutUrl) return forwardAuthLogoutUrl;
  if (tinyAuthUrl) return `${tinyAuthUrl}/logout?redirect_uri=${encodeURIComponent(loggedOutUrl)}`;
  if (endSessionUrl) {
    return `${endSessionUrl}?post_logout_redirect_uri=${encodeURIComponent(loggedOutUrl)}`;
  }
  return loggedOutUrl;
}

// With AUTH_TRUST_HOST and no NEXTAUTH_URL the app answers on several addresses (the home network
// and a VPN, say), so the logout must return to the address the browser used. The standalone
// server reports its bind address (0.0.0.0) as nextUrl.origin, so the Host header is read instead.
function requestOrigin(request: NextRequest) {
  if (process.env.AUTH_TRUST_HOST) {
    const host = request.headers.get('x-forwarded-host') ?? request.headers.get('host');
    if (host) {
      const proto =
        request.headers.get('x-forwarded-proto') ?? request.nextUrl.protocol.replace(/:$/, '');
      return `${proto === 'https' ? 'https' : 'http'}://${host}`;
    }
  }
  return request.nextUrl.origin;
}

export async function GET(request: NextRequest) {
  const appUrl = process.env.NEXTAUTH_URL?.replace(/\/+$/, '') || requestOrigin(request);
  const response = NextResponse.redirect(logoutTarget(`${appUrl}/login?loggedOut=1`), 302);
  clearSessionCookies(request, response);
  return response;
}
