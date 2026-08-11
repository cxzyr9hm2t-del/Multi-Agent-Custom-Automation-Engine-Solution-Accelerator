import { ApiError } from '@/models';
import { useState, useCallback } from 'react';
import { RAIErrorData } from '../components/errors';

export interface UseRAIErrorHandling {
    raiError: RAIErrorData | null;
    setRAIError: (error: RAIErrorData | null) => void;
    handleError: (error: unknown) => boolean; // Returns true if it was an RAI error
    clearRAIError: () => void;
}

/**
 * Custom hook for handling RAI (Responsible AI) validation errors
 * Provides standardized error parsing and state management
 */
export const useRAIErrorHandling = (): UseRAIErrorHandling => {
    const [raiError, setRAIError] = useState<RAIErrorData | null>(null);

    const clearRAIError = useCallback(() => {
        setRAIError(null);
    }, []);

    const handleError = useCallback((error: unknown): boolean => {
        // Clear any previous RAI errors
        setRAIError(null);

        const err = error as ApiError;

        // Check if this is an RAI validation error
        let errorDetail: RAIErrorData | null = null;
        try {
            // Try to parse the error detail if it's a string
            if (typeof err?.response?.data?.detail === 'string') {
                errorDetail = JSON.parse(err.response.data.detail) as RAIErrorData;
            } else {
                errorDetail = (err?.response?.data?.detail ?? null) as unknown as RAIErrorData | null;
            }
        } catch {
            // If parsing fails, use the original error
            errorDetail = (err?.response?.data?.detail ?? null) as unknown as RAIErrorData | null;
        }

        // Handle RAI validation errors
        if (errorDetail?.error_type === 'RAI_VALIDATION_FAILED') {
            setRAIError(errorDetail);
            return true; // Indicates this was an RAI error
        }

        return false; // Indicates this was not an RAI error
    }, []);

    return {
        raiError,
        setRAIError,
        handleError,
        clearRAIError
    };
};

export default useRAIErrorHandling;
