type CookieHttpMode = 'HTTP' | 'HTTPS';

const DEFAULT_COOKIE_HTTP_MODE: CookieHttpMode = 'HTTPS';

function normalizeCookieHttpMode(value: string | undefined): CookieHttpMode {
  if (!value) return DEFAULT_COOKIE_HTTP_MODE;
  const normalized = value.trim().toUpperCase();
  return normalized === 'HTTP' ? 'HTTP' : 'HTTPS';
}

export function getCookieHttpMode(): CookieHttpMode {
  return normalizeCookieHttpMode(process.env.COOKIE_HTTP);
}

export function getCookieSecure(): boolean {
  if (getCookieHttpMode() === 'HTTP') return false;
  return process.env.NODE_ENV === 'production';
}
