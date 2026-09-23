// Only safe code/retry metadata crosses into UI state. Never retain response text.
export class ApiFailure extends Error {
  constructor(public readonly code: string, public readonly retryable = true) {
    super('Report unavailable');
    this.name = 'ApiFailure';
  }
}
export function safeFailure(error: unknown): ApiFailure {
  return error instanceof ApiFailure ? error : new ApiFailure('internal_error');
}
