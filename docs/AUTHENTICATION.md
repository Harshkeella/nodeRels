# Authentication

## Flow

```
  browser                    Supabase                     FastAPI
     |                          |                            |
     |-- password / Google ---->|                            |
     |<-- access + refresh -----|                            |
     |                                                       |
     |------ Authorization: Bearer <access_token> ---------->|
     |                                    verify HS256 locally
     |                                    sub -> User -> workspace
     |<---------------- only that user's data ---------------|
```

Supabase is used for **auth only**. No application data lives in its
PostgreSQL; the backend never calls it on the request path, and holds only its
JWT secret to verify signatures offline.

## Provider swap

Everything provider-specific is `verify_token()` in `app/core/auth.py` — one
function that turns a token string into a `User`. Swapping issuers (Auth0,
Clerk, your own JWTs) is that function plus the frontend's `lib/supabase.ts`.
Nothing else in the backend knows Supabase exists.

## Pages

| Route | Purpose |
|---|---|
| `/login` | Email + password, Google, forgot-password link |
| `/signup` | Name, email, password, confirm, Google |
| `/forgot-password` | Sends a reset link |
| `/reset-password` | Where that link lands |
| `/auth/callback` | Where Google and the confirmation email land |

Signed-in users hitting `/login` are redirected to the dashboard; signed-out
users hitting `/dashboard/*` are redirected to `/login`
(`components/auth/require-auth.tsx`). That redirect is convenience, not
security — the boundary is the API, which 401s regardless.

## Google

Real OAuth via `supabase.auth.signInWithOAuth({ provider: "google" })`. The
client secret lives in the Supabase dashboard; the browser only ever sees the
project URL and the public anon key. Setup steps are in
`docs/DEPLOYMENT.md`.

## Error messages

`friendlyAuthError` in `lib/supabase.ts` maps provider text to something a
person should read ("Invalid email or password.", "This email is not verified
yet.", "Your session has expired. Please sign in again."). Raw provider or
backend text is never shown.

Password reset always reports success, whether or not the address has an
account — telling an anonymous caller which emails are registered is an
account-enumeration oracle.

## Token lifetime

The Supabase SDK refreshes in the background. The API client asks it for a
token *per request* rather than capturing one, because a token captured at
mount is the stale one by the time a long chat stream reconnects. A 401 from
the API triggers `setUnauthorizedHandler`, which sends the user to `/login`.

## Local development

Leave `NEXT_PUBLIC_SUPABASE_URL` / `..._ANON_KEY` unset and run the backend
with `AUTH_DISABLED=true`. There is no login, no Supabase project, and the app
behaves exactly as the single-user install it started as.

## Extension

`extension/auth.js` calls Supabase's auth REST endpoints directly with `fetch`
— three endpoints, no SDK, no bundler, matching how the extension is built.
Email and password only; Google sign-in in the extension would need
`chrome.identity.launchWebAuthFlow` and a registered extension redirect URI,
and is not implemented.
