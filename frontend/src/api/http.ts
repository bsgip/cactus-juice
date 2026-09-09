/** Thin fetch wrapper shared by every api/*.ts module. */

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, body: unknown) {
    super(`Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

/** FastAPI's default error shape (HTTPException / RequestValidationError). */
interface FastApiErrorBody {
  detail?: string | { loc?: (string | number)[]; msg: string }[]
}

/** Renders an ApiError's body into something a user can read - handles both plain `{detail: "..."}`
 * errors and FastAPI's structured 422 validation errors. */
export function formatApiError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error ? error.message : 'Unexpected error'
  }

  const body = error.body as FastApiErrorBody | null
  if (!body?.detail) return error.message
  if (typeof body.detail === 'string') return body.detail
  return body.detail.map((item) => item.msg).join('; ')
}

async function parseErrorBody(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

/** Issues a JSON request (GET/DELETE/etc, no request body) and decodes the JSON response. */
export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorBody(response))
  }
  return (await response.json()) as T
}

/** Issues a multipart/form-data request (used for endpoints that accept file uploads) and decodes the JSON response. */
export async function apiSendForm<T>(path: string, method: 'POST' | 'PUT', formData: FormData): Promise<T> {
  const response = await fetch(path, { method, body: formData, headers: { Accept: 'application/json' } })
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorBody(response))
  }
  return (await response.json()) as T
}
