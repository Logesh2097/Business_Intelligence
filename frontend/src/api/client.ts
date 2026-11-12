// The one place the web app talks to the API (docs/api.md §1).
//
// SIGN-IN IS NOT BUILT. Until it is, the API accepts the email of a demo user in the
// X-Dev-User header, in development and test only. This file adds that header; the
// authentication phase replaces it with an access token and nothing else changes.

const USER_KEY = 'bi.devUser'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message)
  }
}

export function devUser(): string {
  try {
    return localStorage.getItem(USER_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setDevUser(email: string): void {
  try {
    if (email) localStorage.setItem(USER_KEY, email)
    else localStorage.removeItem(USER_KEY)
  } catch {
    // private browsing: the choice lasts until the page is reloaded
  }
}

const NETWORK = 'The server could not be reached. Check that the API is running.'

async function failure(response: Response): Promise<ApiError> {
  let code = 'error'
  let message = `The server answered ${response.status}.`
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string } }
    code = body.error?.code ?? code
    message = body.error?.message ?? message
  } catch {
    // not JSON: keep the plain sentence
  }
  return new ApiError(response.status, code, message)
}

async function send(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers)
  const user = devUser()
  if (user) headers.set('X-Dev-User', user)
  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch {
    throw new ApiError(0, 'network', NETWORK)
  }
  if (!response.ok) throw await failure(response)
  return response
}

export async function get<T>(path: string): Promise<T> {
  return (await (await send(path, { method: 'GET' })).json()) as T
}

export async function post<T>(path: string, body: unknown = {}): Promise<T> {
  const response = await send(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return (await response.json()) as T
}

/** A file as the request body, as the documents route takes it. */
export async function upload<T>(path: string, file: Blob): Promise<T> {
  const response = await send(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  })
  return (await response.json()) as T
}

export async function remove(path: string): Promise<void> {
  await send(path, { method: 'DELETE' })
}

export function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Something went wrong. Please try again.'
}
