/**
 * Normalises anything the backend might send as a timestamp into epoch
 * milliseconds — the unit the rest of the UI works in, and the unit
 * `new Date(number)` assumes.
 *
 * The backend now sends ISO-8601 UTC strings for every timestamp that crosses
 * the wire. This accepts those, and still copes with the two numeric forms
 * that older payloads carry, because a persisted plan or an in-flight socket
 * message can predate that change:
 *
 *  - epoch **seconds** (what `time.time()` produced). Passing one straight to
 *    `new Date()` dated the message to January 1970, a thousand-fold
 *    under-read that looked like a 56-year drift.
 *  - epoch **milliseconds**, which is what `Date.now()` produces on this side.
 *
 * The two are told apart by magnitude: a seconds value for any date this
 * software will see is ~1.7e9, while the same instant in milliseconds is
 * ~1.7e12. The threshold sits between them, so anything below it is seconds.
 *
 * @param value ISO string, epoch seconds, epoch milliseconds, Date, or null
 * @returns epoch milliseconds, or null when there is no usable instant
 */
const EPOCH_SECONDS_CEILING = 1e11;

export const toEpochMs = (
    value: string | number | Date | null | undefined
): number | null => {
    if (value === null || value === undefined || value === '') return null;

    if (value instanceof Date) {
        return isNaN(value.getTime()) ? null : value.getTime();
    }

    if (typeof value === 'number') {
        if (!isFinite(value)) return null;
        return value < EPOCH_SECONDS_CEILING ? Math.round(value * 1000) : Math.round(value);
    }

    // A numeric string is a number that survived JSON as text; anything else
    // is a date string and goes to the Date parser, which handles ISO-8601.
    const asNumber = Number(value);
    if (value.trim() !== '' && !isNaN(asNumber)) {
        return asNumber < EPOCH_SECONDS_CEILING
            ? Math.round(asNumber * 1000)
            : Math.round(asNumber);
    }

    const parsed = new Date(value).getTime();
    return isNaN(parsed) ? null : parsed;
};

/**
 * Formats a date according to the provided format string.
 * Supported tokens:
 *  - YYYY: 4-digit year
 *  - YY: 2-digit year
 *  - MMM: short month name (e.g., Jan)
 *  - MM: 2-digit month
 *  - M: 1 or 2-digit month
 *  - DD: 2-digit day
 *  - D: 1 or 2-digit day
 *  - HH: 2-digit hour (24h)
 *  - H: 1 or 2-digit hour (24h)
 *  - hh: 2-digit hour (12h)
 *  - h: 1 or 2-digit hour (12h)
 *  - mm: 2-digit minute
 *  - m: 1 or 2-digit minute
 *  - A: AM/PM
 *
 * @param date Date | string | number
 * @param format string
 * @returns string
 */
export const formatDate = (
    date: Date | string | number,
    format?: string
): string => {
    const d = date instanceof Date ? date : new Date(date);

    if (isNaN(d.getTime())) return '';

    if (!format) {
        // Use system's locale date and time format
        return d.toLocaleString();
    }

    const monthsShort = [
        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];

    const pad = (n: number, len = 2) => n.toString().padStart(len, '0');

    const hours12 = d.getHours() % 12 || 12;
    const ampm = d.getHours() < 12 ? 'AM' : 'PM';

    const replacements: Record<string, string> = {
        YYYY: d.getFullYear().toString(),
        YY: d.getFullYear().toString().slice(-2),
        MMM: monthsShort[d.getMonth()],
        MM: pad(d.getMonth() + 1),
        M: (d.getMonth() + 1).toString(),
        DD: pad(d.getDate()),
        D: d.getDate().toString(),
        HH: pad(d.getHours()),
        H: d.getHours().toString(),
        hh: pad(hours12),
        h: hours12.toString(),
        mm: pad(d.getMinutes()),
        m: d.getMinutes().toString(),
        A: ampm,
    };
    let formatted = format;
    Object.entries(replacements)
        .sort(([a], [b]) => b.length - a.length)
        .forEach(([token, value]) => {
            formatted = formatted.replace(new RegExp(token, 'g'), value);
        });

    return formatted;
}

/**
 * Formats an elapsed-time duration in seconds for display in processing
 * indicators and completion messages.
 *
 * Examples:
 *  - 5  → "5s"
 *  - 59 → "59s"
 *  - 60 → "1min 0sec"
 *  - 75 → "1min 15sec"
 *
 * @param elapsedSeconds Non-negative integer seconds elapsed.
 * @returns Human-readable elapsed-time string.
 */
export const formatElapsedTime = (elapsedSeconds: number): string => {
    if (elapsedSeconds < 60) {
        return `${elapsedSeconds}s`;
    }

    const minutes = Math.floor(elapsedSeconds / 60);
    const seconds = elapsedSeconds % 60;
    return `${minutes}min ${seconds}sec`;
};
