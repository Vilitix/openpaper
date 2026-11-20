import { useCallback } from 'react';
import { SubscriptionData, UseSubscriptionReturn } from '@/lib/schema';

// Credits/subscriptions are disabled in this self-hosted fork. Returning an
// unlimited subscription keeps the existing UI helpers working while removing
// every limit, warning and upgrade gate.
const UNLIMITED = 10 ** 9;

export const UNLIMITED_SUBSCRIPTION: SubscriptionData = {
    plan: 'researcher',
    limits: {
        paper_uploads: UNLIMITED,
        knowledge_base_size: UNLIMITED,
        chat_credits_weekly: UNLIMITED,
        audio_overviews_weekly: UNLIMITED,
        data_tables_weekly: UNLIMITED,
        discover_searches_weekly: UNLIMITED,
        projects: UNLIMITED,
        project_papers: UNLIMITED,
        model: [],
    },
    usage: {
        paper_uploads: 0,
        paper_uploads_remaining: UNLIMITED,
        knowledge_base_size: 0,
        knowledge_base_size_remaining: UNLIMITED,
        chat_credits_used: 0,
        chat_credits_remaining: UNLIMITED,
        audio_overviews_used: 0,
        audio_overviews_remaining: UNLIMITED,
        projects: 0,
        projects_remaining: UNLIMITED,
        data_tables_used: 0,
        data_tables_remaining: UNLIMITED,
        discover_searches_used: 0,
        discover_searches_remaining: UNLIMITED,
    },
};

export const useSubscription = (): UseSubscriptionReturn => {
    const refetch = useCallback(async () => { }, []);
    return { subscription: UNLIMITED_SUBSCRIPTION, loading: false, error: null, refetch };
};

const pct = (used: number, total: number): number => (total > 0 ? (used / total) * 100 : 0);

export const getStorageUsagePercentage = (s: SubscriptionData | null) =>
    s ? pct(s.usage.knowledge_base_size, s.limits.knowledge_base_size) : 0;
export const isStorageNearLimit = (s: SubscriptionData | null, threshold = 75) => getStorageUsagePercentage(s) >= threshold;
export const isStorageAtLimit = (s: SubscriptionData | null) => getStorageUsagePercentage(s) >= 100;

export const getPaperUploadPercentage = (s: SubscriptionData | null) =>
    s ? pct(s.usage.paper_uploads, s.limits.paper_uploads) : 0;
export const isPaperUploadNearLimit = (s: SubscriptionData | null, threshold = 75) => getPaperUploadPercentage(s) >= threshold;
export const isPaperUploadAtLimit = (s: SubscriptionData | null) => getPaperUploadPercentage(s) >= 100;

export const formatFileSize = (sizeInKb: number): string => {
    if (sizeInKb < 1024) return `${sizeInKb.toFixed(1)} KB`;
    if (sizeInKb < 1024 * 1024) return `${(sizeInKb / 1024).toFixed(1)} MB`;
    return `${(sizeInKb / (1024 * 1024)).toFixed(1)} GB`;
};

export const getChatCreditUsagePercentage = (s: SubscriptionData | null) =>
    s ? pct(s.usage.chat_credits_used, s.limits.chat_credits_weekly) : 0;
export const isChatCreditNearLimit = (s: SubscriptionData | null, threshold = 75) => getChatCreditUsagePercentage(s) >= threshold;
export const isChatCreditAtLimit = (s: SubscriptionData | null) => getChatCreditUsagePercentage(s) >= 100;

export const getAudioOverviewUsagePercentage = (s: SubscriptionData | null) =>
    s ? pct(s.usage.audio_overviews_used, s.limits.audio_overviews_weekly) : 0;
export const isAudioOverviewNearLimit = (s: SubscriptionData | null, threshold = 75) => getAudioOverviewUsagePercentage(s) >= threshold;
export const isAudioOverviewAtLimit = (s: SubscriptionData | null) => getAudioOverviewUsagePercentage(s) >= 100;

export const getProjectUsagePercentage = (s: SubscriptionData | null) =>
    s ? pct(s.usage.projects, s.limits.projects) : 0;
export const isProjectNearLimit = (s: SubscriptionData | null, threshold = 75) => getProjectUsagePercentage(s) >= threshold;
export const isProjectAtLimit = (s: SubscriptionData | null) => getProjectUsagePercentage(s) >= 100;

export const getProjectPaperHardLimit = (s: SubscriptionData | null): number | null =>
    s?.limits.project_papers ?? null;
export const getProjectPaperUsagePercentage = (s: SubscriptionData | null, paperCount: number) =>
    s ? pct(paperCount, s.limits.project_papers) : 0;
export const isProjectPaperNearLimit = (s: SubscriptionData | null, paperCount: number, threshold = 75) =>
    getProjectPaperUsagePercentage(s, paperCount) >= threshold;
export const isProjectPaperAtLimit = (s: SubscriptionData | null, paperCount: number) =>
    getProjectPaperUsagePercentage(s, paperCount) >= 100;

export const getDataTableUsagePercentage = (s: SubscriptionData | null) =>
    s ? pct(s.usage.data_tables_used, s.limits.data_tables_weekly) : 0;
export const isDataTableNearLimit = (s: SubscriptionData | null, threshold = 75) => getDataTableUsagePercentage(s) >= threshold;
export const isDataTableAtLimit = (s: SubscriptionData | null) => getDataTableUsagePercentage(s) >= 100;

export const getDiscoverSearchUsagePercentage = (s: SubscriptionData | null) =>
    s ? pct(s.usage.discover_searches_used, s.limits.discover_searches_weekly) : 0;
export const isDiscoverSearchNearLimit = (s: SubscriptionData | null, threshold = 75) => getDiscoverSearchUsagePercentage(s) >= threshold;
export const isDiscoverSearchAtLimit = (s: SubscriptionData | null) =>
    !!s && s.usage.discover_searches_remaining <= 0;

export const nextMonday = new Date();
