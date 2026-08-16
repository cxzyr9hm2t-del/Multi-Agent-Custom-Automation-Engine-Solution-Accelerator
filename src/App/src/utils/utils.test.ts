/**
 * Guards the timestamp unit contract between the backend and this UI.
 *
 * The backend sends ISO-8601 UTC strings; `Date.now()` on this side produces
 * epoch milliseconds; older payloads carry epoch seconds. Mixing the last two
 * in one field is what dated agent messages to January 1970, so the seconds
 * case is asserted explicitly alongside the bug it replaces.
 */
import { describe, it, expect } from 'vitest';
import { toEpochMs } from './utils';

describe('toEpochMs', () => {
  it('parses ISO-8601 UTC as the same instant', () => {
    expect(toEpochMs('2026-08-16T17:00:00+00:00')).toBe(Date.UTC(2026, 7, 16, 17, 0, 0));
  });
  it('scales epoch seconds up to milliseconds', () => {
    expect(toEpochMs(1786000000)).toBe(1786000000000);
  });
  it('leaves epoch milliseconds alone', () => {
    expect(toEpochMs(1786000000000)).toBe(1786000000000);
  });
  it('rejects nothing-values', () => {
    expect(toEpochMs(null)).toBeNull();
    expect(toEpochMs(undefined)).toBeNull();
    expect(toEpochMs('')).toBeNull();
    expect(toEpochMs('not a date')).toBeNull();
  });
  it('the old bug: a seconds value read as ms landed in 1970', () => {
    expect(new Date(1786000000).getUTCFullYear()).toBe(1970);
    expect(new Date(toEpochMs(1786000000)!).getUTCFullYear()).toBe(2026);
  });
});
