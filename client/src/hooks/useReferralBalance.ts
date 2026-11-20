export interface ReferralBalance {
    pending_cents: number;
    available_cents: number;
    total_converted: number;
    referee_discount_percent: number;
    referee_discount_available: boolean;
}

export interface UseReferralBalanceReturn {
    balance: ReferralBalance | null;
    loading: boolean;
    error: string | null;
    refetch: () => Promise<void>;
}

// Referrals are removed in this self-hosted fork.
export function useReferralBalance(enabled: boolean = true): UseReferralBalanceReturn {
    void enabled;
    return { balance: null, loading: false, error: null, refetch: async () => { } };
}

export function hasUnusedReferralValue(balance: ReferralBalance | null): boolean {
    void balance;
    return false;
}
