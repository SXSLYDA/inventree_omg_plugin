import { useCallback, useEffect, useState } from 'react';
import { Alert, Badge, Button, Card, Group, HoverCard, Loader, Select, Stack, Text, TextInput, Title } from '@mantine/core';
import { IconCheck, IconFileImport, IconSearch, IconX } from '@tabler/icons-react';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

// Harness imports run on InvenTree's BACKGROUND WORKER now (see the
// plugin's harness_import_job.py): the import endpoint queues a job and
// returns at once, and this polls the job until it finishes. A big
// harness used to run past the web server's request timeout and fail
// with a 500 even though nothing was wrong with it.
const IMPORT_POLL_MS = 2000;
const IMPORT_MAX_WAIT_MS = 15 * 60 * 1000;

async function runHarnessImport(
    api: InvenTreePluginContext['api'],
    body: Record<string, unknown>,
    onProgress?: (message: string) => void,
): Promise<any> {
    const started = await api.post('/plugin/omg-harness-import/import-harness/', body);
    let job = started.data;
    const deadline = Date.now() + IMPORT_MAX_WAIT_MS;
    while (job.status === 'queued' || job.status === 'running') {
        onProgress?.(job.progress || (job.status === 'queued' ? 'Waiting for the background worker…' : 'Working…'));
        if (Date.now() > deadline) {
            throw new Error('The import is still running after 15 minutes. Check back shortly, or look at InvenTree\'s background tasks.');
        }
        await new Promise((resolve) => setTimeout(resolve, IMPORT_POLL_MS));
        const polled = await api.get(`/plugin/omg-harness-import/import-harness/jobs/${job.job_id}/`);
        job = polled.data;
    }
    if (job.status === 'failed') {
        throw new Error(job.error || 'The import failed.');
    }
    return job.batch;
}

/**
 * "Import Harness from OMG" dashboard item — lives on the main
 * dashboard (get_ui_dashboard_items), not tied to any specific part or
 * record. This is the genuine gap found in review: HarnessSearchProxyView
 * and HarnessImportView (the search-and-import backend) existed with no
 * frontend calling them at all anywhere in this project until now.
 *
 * Why a dashboard item, not a panel: importing a harness by name/IPN
 * creates or finds ITS OWN Part (see harness_import.py's auto-create),
 * completely unrelated to whatever record a get_ui_panels target_model
 * would be scoped to — there's no natural "part" or "salesorder" page
 * this belongs on. A dashboard item has no such scoping requirement,
 * and get_ui_dashboard_items is confirmed working directly from
 * InvenTree's own real sample plugin source (fetched, not inferred).
 */

interface HarnessSearchResult {
    id: number;
    part_number: string;
    description: string;
}

interface CategoryOption {
    value: string;
    label: string;
}

interface CredentialStatus {
    omg_harness_api_url: boolean;
    omg_inventree_user_token: boolean;
    inventree_webhook_token: boolean;
    mouser_api_key: boolean;
    // Needed for the wire gauge fill-in (null = couldn't tell)
    event_integration?: boolean | null;
    background_worker?: boolean | null;
}

function OMGImportHarnessDashboardItem({ context }: { context: InvenTreePluginContext }) {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState<HarnessSearchResult[]>([]);
    const [searching, setSearching] = useState(false);
    const [searchError, setSearchError] = useState<string | null>(null);
    const [categoryPk, setCategoryPk] = useState<number | ''>('');
    const [categoryOptions, setCategoryOptions] = useState<CategoryOption[]>([]);
    const [categorySearchTerm, setCategorySearchTerm] = useState('');
    const [categorySearching, setCategorySearching] = useState(false);
    const [importingPartNumber, setImportingPartNumber] = useState<string | null>(null);
    const [importProgress, setImportProgress] = useState<string | null>(null);
    // partPk only set on a successful import - lets the success Alert
    // offer a "Review in InvenTree" button straight to that part's own
    // OMG Harness Sync panel, rather than leaving the person to find it
    // themselves after being told an import succeeded.
    const [importMessage, setImportMessage] = useState<{ text: string; isError: boolean; partPk?: number } | null>(null);
    // Distinguishes "haven't searched yet" from "searched, zero
    // matches" — without this, a genuinely empty result silently shows
    // nothing at all, which reads as "did this even do anything?"
    // rather than a clear answer.
    const [hasSearched, setHasSearched] = useState(false);
    const [credentialStatus, setCredentialStatus] = useState<CredentialStatus | null>(null);

    // Fetched once on mount — this is a small view the plugin defines
    // itself (see CredentialStatusView in api.py), not InvenTree's own
    // settings-display API, specifically so it can report the real,
    // unmasked "is this actually set?" answer that the Plugin Settings
    // page itself cannot give (confirmed directly: it shows the exact
    // same masked placeholder whether a credential is genuinely
    // configured or completely empty).
    useEffect(() => {
        context.api.get('/plugin/omg-harness-import/credential-status/')
            .then((response) => setCredentialStatus(response.data))
            .catch(() => setCredentialStatus(null));
    }, [context.api]);

    // Debounced category search, same 500ms pattern InvenTree's own
    // SearchInput component uses — searches InvenTree's real category
    // tree by name as the user types, instead of requiring them to
    // already know a raw category pk. pathstring (the full breadcrumb,
    // e.g. "Electronics/Connectors/Automotive") is shown rather than
    // bare name, since two categories in different branches can share
    // the same name and would otherwise be indistinguishable in the
    // dropdown.
    useEffect(() => {
        if (categorySearchTerm.trim().length < 2) {
            setCategoryOptions([]);
            return;
        }
        const timer = setTimeout(async () => {
            setCategorySearching(true);
            try {
                const response = await context.api.get('/api/part/category/', {
                    params: { search: categorySearchTerm.trim(), limit: 20 },
                });
                const rows = response.data?.results ?? response.data ?? [];
                setCategoryOptions(
                    rows.map((c: any) => ({
                        value: String(c.pk),
                        label: c.pathstring || c.name,
                    }))
                );
            } catch {
                setCategoryOptions([]);
            } finally {
                setCategorySearching(false);
            }
        }, 500);
        return () => clearTimeout(timer);
    }, [categorySearchTerm, context.api]);

    const runSearch = useCallback(async () => {
        if (query.trim().length < 2) {
            setSearchError('Type at least 2 characters');
            setResults([]);
            return;
        }
        setSearching(true);
        setSearchError(null);
        setImportMessage(null);
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

    const importHarness = useCallback(async (partNumber: string) => {
        setImportingPartNumber(partNumber);
        setImportMessage(null);

        try {
            const body: { harness_part_number: string; category_pk?: number } = {
                harness_part_number: partNumber,
            };
            if (categoryPk !== '') body.category_pk = categoryPk;

            const batch = await runHarnessImport(context.api, body, setImportProgress);
            const reconciliationWarning = batch.reconciliation_pushed === false
                ? ' Import succeeded, but reporting the result back to OMG failed — check the InvenTree Webhook Token setting.'
                : '';
            setImportMessage({
                text: `Imported ${partNumber} — ${batch.matched_items ?? 0} matched, ${batch.flagged_items ?? 0} need review.${reconciliationWarning}`,
                isError: false,
                // root_part serializes as a plain pk (ImportBatchSerializer
                // is a bare ModelSerializer with no custom field declared
                // for it, confirmed directly against api.py) - null only
                // if the harness part somehow doesn't exist, which
                // shouldn't happen given import_or_update_harness_bom
                // always creates or resolves one first.
                partPk: batch.root_part ?? undefined,
            });
        } catch (err: any) {
            const detail = err?.response?.data?.detail || err.message;
            setImportMessage({ text: `Could not import ${partNumber}: ${detail}`, isError: true });
        } finally {
            setImportingPartNumber(null);
            setImportProgress(null);
        }
    }, [categoryPk, context.api]);

    const checks = credentialStatus ? ([
        ['OMG URL', credentialStatus.omg_harness_api_url],
        ['OMG User Token', credentialStatus.omg_inventree_user_token],
        ['Webhook Token', credentialStatus.inventree_webhook_token],
        ['Mouser Key', credentialStatus.mouser_api_key],
        // Gauge mm2 <-> AWG fill-in needs both of these
        ...(credentialStatus.event_integration === undefined ? [] : [
            ['Event Integration', !!credentialStatus.event_integration],
            ['Background Worker', !!credentialStatus.background_worker],
        ]),
    ] as [string, boolean][]) : [];
    const missing = checks.filter(([, ok]) => !ok);

    return (
        <Stack gap="sm">
            <Group justify="space-between" wrap="nowrap">
                <Group gap={8} wrap="nowrap">
                    <IconFileImport size={20} color={context.theme.colors[context.theme.primaryColor][6]} />
                    {/* the theme's accent colour, like InvenTree's own dashboard item titles */}
                    <Title order={4} c={context.theme.primaryColor}>Import harness from OMG</Title>
                </Group>
                {/* The plugin's settings: one badge while all is well, the
                    missing ones by name when not - hover for the full list */}
                {checks.length > 0 && (
                    <HoverCard withArrow position="bottom-end">
                        <HoverCard.Target>
                            {missing.length
                                ? <Badge color="red" variant="light" leftSection={<IconX size={12} />}>{missing.map(([l]) => l).join(', ')} not set</Badge>
                                : <Badge color="green" variant="light" leftSection={<IconCheck size={12} />}>Settings OK</Badge>}
                        </HoverCard.Target>
                        <HoverCard.Dropdown>
                            <Stack gap={4}>
                                {checks.map(([label, ok]) => (
                                    <Group key={label} gap={6}>
                                        {ok ? <IconCheck size={14} color="var(--mantine-color-green-6)" /> : <IconX size={14} color="var(--mantine-color-red-6)" />}
                                        <Text size="xs">{label}</Text>
                                    </Group>
                                ))}
                            </Stack>
                        </HoverCard.Dropdown>
                    </HoverCard>
                )}
            </Group>

            <Group gap="xs">
                <TextInput
                    placeholder="OMG harness part number, e.g. R1300G"
                    leftSection={<IconSearch size={16} />}
                    value={query}
                    onChange={(e) => setQuery(e.currentTarget.value)}
                    onKeyDown={(e) => e.key === 'Enter' && runSearch()}
                    style={{ flex: 1 }}
                />
                <Button onClick={runSearch} loading={searching}>Search OMG</Button>
            </Group>

            {searchError && <Alert color="orange" p="xs"><Text size="sm">{searchError}</Text></Alert>}
            {importingPartNumber && importProgress && (
                <Group gap="xs"><Loader size="xs" /><Text size="sm" c="dimmed">{importingPartNumber}: {importProgress}</Text></Group>
            )}
            {importMessage && (
                <Alert color={importMessage.isError ? 'red' : 'green'} p="xs">
                    <Group justify="space-between" wrap="nowrap">
                        <Text size="sm">{importMessage.text}</Text>
                        {importMessage.partPk !== undefined && (
                            <Button size="xs" variant="light"
                                onClick={() => context.navigate(`/part/${importMessage.partPk}/omg-harness-sync`)}>
                                Open harness
                            </Button>
                        )}
                    </Group>
                </Alert>
            )}

            {hasSearched && !searching && !searchError && results.length === 0 && (
                <Text size="sm" c="dimmed">No harness in OMG matches "{query}".</Text>
            )}

            {results.length > 0 && (
                <>
                    <Stack gap={6} style={{ maxHeight: 240, overflowY: 'auto' }}>
                        {results.map((r) => (
                            <Card key={r.id} withBorder radius="md" padding="xs">
                                <Group justify="space-between" wrap="nowrap">
                                    <div>
                                        <Text fw={600} size="sm">{r.part_number}</Text>
                                        <Text size="xs" c="dimmed">{r.description}</Text>
                                    </div>
                                    <Button size="xs" variant="light"
                                        loading={importingPartNumber === r.part_number}
                                        disabled={!!importingPartNumber && importingPartNumber !== r.part_number}
                                        onClick={() => importHarness(r.part_number)}>
                                        Import
                                    </Button>
                                </Group>
                            </Card>
                        ))}
                    </Stack>
                    {/* only matters for a harness InvenTree doesn't have yet */}
                    <Select
                        size="xs"
                        label="Category for a new harness part"
                        placeholder="Type to search categories (optional)"
                        searchable
                        clearable
                        searchValue={categorySearchTerm}
                        onSearchChange={setCategorySearchTerm}
                        data={categoryOptions}
                        value={categoryPk === '' ? null : String(categoryPk)}
                        onChange={(v) => setCategoryPk(v ? Number(v) : '')}
                        nothingFoundMessage={categorySearching ? 'Searching…' : 'Type at least 2 characters'}
                        maw={360}
                    />
                </>
            )}
        </Stack>
    );
}

export function RenderOMGImportHarnessDashboardItem(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return (
        <OMGImportHarnessDashboardItem context={context} />
    );
}
