import { useCallback, useEffect, useState } from 'react';
import { Alert, Badge, Button, Group, Stack, Table, Text, Title } from '@mantine/core';
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

/**
 * "Sync with OMG" — shown on a harness's own Part detail page (parts
 * explicitly marked as OMG-managed — see core.py's get_ui_panels). Same
 * sync action whether it's the first import or a re-sync after changes
 * in OMG's design tool — both just call import-harness/ again.
 *
 * Includes an inline review queue for this harness's flagged items —
 * resolving them (linking a candidate, or dismissing) doesn't require
 * leaving InvenTree's own UI for Django admin.
 *
 * Backed by:
 *   GET  /plugin/omg-harness-import/batches/latest/?part_pk=...     (status on load)
 *   POST /plugin/omg-harness-import/import-harness/                 (the sync button)
 *   GET  /plugin/omg-harness-import/unresolved/?part_pk=...          (review queue)
 *   POST /plugin/omg-harness-import/unresolved/<id>/resolve/         (link/dismiss)
 *   GET  /plugin/omg-harness-import/work-on-url/?part_pk=...         (deep link to OMG)
 */
function OMGHarnessSyncPanel({ context }: { context: InvenTreePluginContext }) {
    const [lastBatch, setLastBatch] = useState<BatchSummary | null>(null);
    const [loadingStatus, setLoadingStatus] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [queue, setQueue] = useState<UnresolvedItem[]>([]);
    const [resolvingId, setResolvingId] = useState<number | null>(null);

    const partId = context.id;
    const partIpn = context?.instance?.IPN;

    const loadStatus = useCallback(async () => {
        if (!partId) return;
        setLoadingStatus(true);
        try {
            const response = await context.api.get('/plugin/omg-harness-import/batches/latest/', {
                params: { part_pk: partId },
            });
            setLastBatch(response.data);
        } catch (err: any) {
            // A failed status check shouldn't block the sync button from
            // being usable — just show no prior status.
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
        if (!partIpn) {
            setError('This part has no IPN set — OMG matches harnesses by IPN, so one is needed before syncing.');
            return;
        }
        setSyncing(true);
        setError(null);

        try {
            const response = await context.api.post('/plugin/omg-harness-import/import-harness/', {
                harness_part_number: partIpn,
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
    }, [partIpn, context.api, loadQueue]);

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
