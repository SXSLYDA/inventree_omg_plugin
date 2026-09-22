import { useCallback, useEffect, useState } from 'react';
import { Alert, Badge, Button, Group, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

interface BatchSummary {
    id: number;
    created_at: string;
    total_items: number;
    matched_items: number;
    flagged_items: number;
    items: any[];
}

interface Candidate {
    pk: number;
    ipn: string;
    name: string;
}

interface UnresolvedItem {
    id: number;
    part_number: string;
    reason: string;
    notes: string;
    candidates: Candidate[];
}

interface HarnessSearchResult {
    id: number;
    part_number: string;
    description: string;
}

/**
 * "OMG Harness" — shown on every assembly part's own detail page (see
 * core.py's get_ui_panels). Two states, one panel:
 *   - not yet linked to OMG: search OMG's harness part numbers and
 *     link this exact part to one (target_part_pk pins it, regardless
 *     of whether this part's own name happens to match).
 *   - already linked: the original sync/re-import flow, plus the
 *     inline review queue for flagged items.
 * is_linked itself is checked server-side (LatestBatchForPartView),
 * not inferred from batch history alone — a part can be marked linked
 * manually before ever running a first sync.
 *
 * Backed by:
 *   GET  /plugin/omg-harness-import/batches/latest/?part_pk=...     (is_linked + status, on load)
 *   GET  /plugin/omg-harness-import/harness-search/                 (search, when not yet linked)
 *   POST /plugin/omg-harness-import/import-harness/                 (link-and-import, or re-sync)
 *   GET  /plugin/omg-harness-import/unresolved/?part_pk=...          (review queue)
 *   POST /plugin/omg-harness-import/unresolved/<id>/resolve/         (link/dismiss)
 *   GET  /plugin/omg-harness-import/work-on-url/?part_pk=...         (deep link to OMG)
 */
function OMGHarnessSyncPanel({ context }: { context: InvenTreePluginContext }) {
    const [lastBatch, setLastBatch] = useState<BatchSummary | null>(null);
    const [isLinked, setIsLinked] = useState(false);
    const [loadingStatus, setLoadingStatus] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [queue, setQueue] = useState<UnresolvedItem[]>([]);
    const [resolvingId, setResolvingId] = useState<number | null>(null);

    // Link flow (only used while !isLinked)
    // Pre-filled with the part's own name — that's the overwhelmingly
    // likely search term (OMG matches harnesses by name, same as
    // runSync below), so someone linking this part shouldn't have to
    // retype what's already right there on the page. Still fully
    // editable if the actual OMG harness is named differently.
    const [query, setQuery] = useState(() => context?.instance?.name || '');
    const [searching, setSearching] = useState(false);
    const [searchError, setSearchError] = useState<string | null>(null);
    const [results, setResults] = useState<HarnessSearchResult[]>([]);
    const [linkingPartNumber, setLinkingPartNumber] = useState<string | null>(null);
    // Same reasoning as ImportHarnessPanel.tsx's dashboard widget —
    // distinguishes "haven't searched yet" from "searched, zero
    // matches", so a genuinely empty result gives a clear answer
    // instead of silently showing nothing at all.
    const [hasSearched, setHasSearched] = useState(false);

    const partId = context.id;

    const loadStatus = useCallback(async () => {
        if (!partId) return;
        setLoadingStatus(true);
        try {
            const response = await context.api.get('/plugin/omg-harness-import/batches/latest/', {
                params: { part_pk: partId },
            });
            setIsLinked(!!response.data?.is_linked);
            setLastBatch(response.data?.batch ?? null);
        } catch (err: any) {
            // A failed status check shouldn't block the rest of the panel
            // from being usable — just show the "not linked" state.
            setIsLinked(false);
            setLastBatch(null);
        } finally {
            setLoadingStatus(false);
        }
    }, [partId, context.api]);


    const loadQueue = useCallback(async () => {
        if (!partId) return;
        try {
            const response = await context.api.get('/plugin/omg-harness-import/unresolved/', {
                params: { part_pk: partId },
            });
            setQueue(response.data || []);
        } catch (err: any) {
            // Same principle as loadStatus — a failed queue fetch shouldn't
            // break the rest of the panel, just show nothing to review.
            setQueue([]);
        }
    }, [partId, context.api]);

    useEffect(() => {
        loadStatus();
        loadQueue();
    }, [loadStatus, loadQueue]);

    const runSync = useCallback(async () => {
        const partName = context?.instance?.name;
        if (!partName) {
            setError('This part has no name set — OMG matches harnesses by name, so one is needed before syncing.');
            return;
        }
        setSyncing(true);
        setError(null);

        try {
            const response = await context.api.post('/plugin/omg-harness-import/import-harness/', {
                harness_part_number: partName,
                target_part_pk: partId,
            });
            setLastBatch(response.data);
            const flagged = response.data?.flagged_items || 0;
            notifications.show({
                title: 'Sync complete',
                message: flagged > 0
                    ? `Synced — ${flagged} item(s) need review below.`
                    : 'Synced — everything matched cleanly.',
                color: flagged > 0 ? 'yellow' : 'green',
            });
            await loadQueue();
        } catch (err: any) {
            const detail = err?.response?.data?.detail || err.message;
            setError(`Sync failed: ${detail}`);
        } finally {
            setSyncing(false);
        }
    }, [context.instance, context.api, partId, loadQueue]);

    // Link flow — search OMG's harness part numbers, then link+import
    // this exact part (target_part_pk) to whichever one is picked.
    // Reuses the same search/import endpoints the dashboard's "Import
    // harness from OMG" widget uses — the only difference is
    // target_part_pk pinning the result to THIS part rather than
    // finding/creating one by name.
    const runSearch = useCallback(async () => {
        if (query.trim().length < 2) {
            setSearchError('Type at least 2 characters');
            setResults([]);
            return;
        }
        setSearching(true);
        setSearchError(null);
        setHasSearched(true);
        try {
            const response = await context.api.get('/plugin/omg-harness-import/harness-search/', {
                params: { q: query.trim() },
            });
            if (response.data.error) {
                setSearchError(response.data.error);
                setResults([]);
            } else {
                setResults(response.data.results || []);
            }
        } catch (err: any) {
            setSearchError(err?.response?.data?.detail || err.message);
            setResults([]);
        } finally {
            setSearching(false);
        }
    }, [query, context.api]);

    const linkHarness = useCallback(async (partNumber: string) => {
        setLinkingPartNumber(partNumber);
        setError(null);
        try {
            const response = await context.api.post('/plugin/omg-harness-import/import-harness/', {
                harness_part_number: partNumber,
                target_part_pk: partId,
            });
            setLastBatch(response.data);
            setIsLinked(true);
            notifications.show({
                title: 'Linked',
                message: `Linked to ${partNumber} and imported its BOM.`,
                color: 'green',
            });
            await loadQueue();
        } catch (err: any) {
            const detail = err?.response?.data?.detail || err.message;
            setError(`Could not link ${partNumber}: ${detail}`);
        } finally {
            setLinkingPartNumber(null);
        }
    }, [context.api, partId, loadQueue]);

    const workOnInOmg = useCallback(async () => {
        if (!partId) return;
        setError(null);
        try {
            const response = await context.api.get('/plugin/omg-harness-import/work-on-url/', {
                params: { part_pk: partId },
            });
            // Opens in a new tab. Whether that tab is already logged into
            // OMG or not is handled entirely by OMG's own login_required +
            // next= redirect on the far end — nothing to detect here.
            window.open(response.data.url, '_blank');
        } catch (err: any) {
            const detail = err?.response?.data?.detail || err.message;
            setError(`Could not open OMG: ${detail}`);
        }
    }, [partId, context.api]);

    const resolveItem = useCallback(async (itemId: number, action: 'link' | 'dismiss', partPk?: number) => {
        setResolvingId(itemId);
        setError(null);
        try {
            await context.api.post(`/plugin/omg-harness-import/unresolved/${itemId}/resolve/`, {
                action,
                part_pk: partPk,
            });
            await loadQueue();
            await loadStatus();
            notifications.show({ title: 'Resolved', message: 'Item resolved.', color: 'green' });
        } catch (err: any) {
            const detail = err?.response?.data?.detail || err.message;
            setError(`Could not resolve item: ${detail}`);
        } finally {
            setResolvingId(null);
        }
    }, [context.api, loadQueue, loadStatus]);

    if (loadingStatus) {
        return <Text size="sm" c="dimmed">Checking OMG link status…</Text>;
    }

    if (!isLinked) {
        return (
            <Stack gap="sm">
                <Title order={4}>Link to OMG</Title>
                <Text size="sm" c="dimmed">
                    This part isn't linked to an OMG Harness design yet.
                    Search OMG's harness part numbers below and link this
                    exact part to one — its BOM imports immediately once
                    linked.
                </Text>

                <Group gap="xs" align="flex-end">
                    <TextInput
                        label="Search OMG"
                        placeholder="e.g. R1300G"
                        value={query}
                        onChange={(e) => setQuery(e.currentTarget.value)}
                        onKeyDown={(e) => e.key === 'Enter' && runSearch()}
                        style={{ flex: 1 }}
                    />
                    <Button onClick={runSearch} loading={searching}>Search</Button>
                </Group>

                {searchError && <Alert color="orange">{searchError}</Alert>}
                {error && <Alert color="red" title="Link issue">{error}</Alert>}

                {hasSearched && !searching && !searchError && results.length === 0 && (
                    <Text size="sm" c="dimmed">No matching harnesses found in OMG for "{query}".</Text>
                )}

                {results.length > 0 && (
                    <Stack gap={6}>
                        {results.map((r) => (
                            <Group key={r.id} justify="space-between" wrap="nowrap"
                                   style={{ border: '1px solid var(--mantine-color-gray-3)', borderRadius: 4, padding: '8px 12px' }}>
                                <div>
                                    <Text fw={500} size="sm">{r.part_number}</Text>
                                    <Text size="xs" c="dimmed">{r.description}</Text>
                                </div>
                                <Button
                                    size="xs"
                                    variant="outline"
                                    loading={linkingPartNumber === r.part_number}
                                    onClick={() => linkHarness(r.part_number)}
                                >
                                    Link &amp; import
                                </Button>
                            </Group>
                        ))}
                    </Stack>
                )}
            </Stack>
        );
    }

    return (
        <Stack gap="sm">
            <Title order={4}>Sync with OMG</Title>
            <Text size="sm" c="dimmed">
                Pulls this harness's current design from OMG Harness and
                updates its BOM here — connectors, conductors, and
                multicore cables. Safe to run again any time the design
                changes in OMG; quantities update, nothing is deleted
                without being flagged for review first.
            </Text>

            <Group>
                <Button onClick={runSync} loading={syncing}>
                    {lastBatch ? 'Sync now' : 'Import from OMG'}
                </Button>
                <Button variant="light" onClick={workOnInOmg}>
                    Work on in OMG
                </Button>
                {lastBatch && !loadingStatus && (
                    <Text size="xs" c="dimmed">
                        Last synced {new Date(lastBatch.created_at).toLocaleString()}
                    </Text>
                )}
            </Group>

            {error && <Alert color="red" title="Sync issue">{error}</Alert>}

            {lastBatch && (
                <Table>
                    <Table.Tbody>
                        <Table.Tr>
                            <Table.Td>Total items</Table.Td>
                            <Table.Td>{lastBatch.total_items}</Table.Td>
                        </Table.Tr>
                        <Table.Tr>
                            <Table.Td>Matched</Table.Td>
                            <Table.Td><Badge color="green">{lastBatch.matched_items}</Badge></Table.Td>
                        </Table.Tr>
                        <Table.Tr>
                            <Table.Td>Needs review</Table.Td>
                            <Table.Td>
                                <Badge color={lastBatch.flagged_items > 0 ? 'yellow' : 'gray'}>
                                    {lastBatch.flagged_items}
                                </Badge>
                            </Table.Td>
                        </Table.Tr>
                    </Table.Tbody>
                </Table>
            )}

            {!lastBatch && !loadingStatus && (
                <Text size="sm" c="dimmed">Not synced with OMG yet.</Text>
            )}

            {queue.length > 0 && (
                <>
                    <Title order={5} mt="md">Needs review</Title>
                    <Stack gap="xs">
                        {queue.map((item) => (
                            <Alert key={item.id} color="yellow" title={item.part_number}>
                                <Text size="sm" mb={item.candidates.length > 0 ? 'xs' : 0}>{item.notes}</Text>
                                {item.candidates.length > 0 && (
                                    <Group gap="xs">
                                        {item.candidates.map((c) => (
                                            <Button
                                                key={c.pk}
                                                size="xs"
                                                variant="outline"
                                                loading={resolvingId === item.id}
                                                onClick={() => resolveItem(item.id, 'link', c.pk)}
                                            >
                                                Use {c.name || c.ipn}
                                            </Button>
                                        ))}
                                    </Group>
                                )}
                                <Button
                                    size="xs"
                                    variant="subtle"
                                    color="gray"
                                    mt="xs"
                                    loading={resolvingId === item.id}
                                    onClick={() => resolveItem(item.id, 'dismiss')}
                                >
                                    Dismiss
                                </Button>
                            </Alert>
                        ))}
                    </Stack>
                </>
            )}
        </Stack>
    );
}

export function RenderOMGHarnessSyncPanel(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return (
        <OMGHarnessSyncPanel context={context} />
    );
}
