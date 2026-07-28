/**
 * Shape of the errors thrown by the HTTP layer.
 *
 * Under `strict`, a `catch` binding is `unknown`, so the call sites that previously
 * wrote `catch (error: any)` need a type to narrow to. Every field is optional
 * because a thrown value is not guaranteed to be an HTTP error at all — it may be a
 * plain `Error`, or something that is not an `Error` — so reads must stay optional
 * chained. Narrowing with `as ApiError` is erased at compile time and adds nothing
 * at runtime.
 */
export interface ApiErrorResponse {
    status?: number;
    data?: {
        detail?: string;
        [key: string]: unknown;
    };
}

export interface ApiError {
    response?: ApiErrorResponse;
    message?: string;
    name?: string;
    stack?: string;
}
