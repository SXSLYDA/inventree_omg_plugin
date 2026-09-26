import { useCallback, useEffect, useState } from 'react';
import { Alert, Badge, Button, Group, Select, Stack, Text, TextInput, Title } from '@mantine/core';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

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
    layout_status: number;
    diagram_status: number;
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

            const response = await context.api.post('/plugin/omg-harness-import/import-harness/', body);
            const batch = response.data;
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
        }
    }, [categoryPk, context.api]);

    return (
        <Stack gap="sm">
            <Group gap={8} wrap="nowrap">
                {/*
                  Inline SVG rather than pulling in @tabler/icons-react -
                  that package isn't a dependency of this panel yet, and
                  adding it means editing package.json plus an npm
                  install as part of the next build, more risk than
                  wanted right before a rebuild+redeploy. A simple
                  file-import glyph, close to Tabler's own "file-import"
                  icon used in this item's own core.py declaration
                  (which only affects the dashboard's "add widget"
                  picker, not this widget's own rendered content - hence
                  showing no icon at all before this fix, confirmed
                  directly from a screenshot of the actual rendering).
                */}
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={context.theme.colors[context.theme.primaryColor][6]} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 3v4a1 1 0 0 0 1 1h4" />
                    <path d="M5 12v-7a2 2 0 0 1 2 -2h7l5 5v4" />
                    <path d="M5 15l4 4" />
                    <path d="M5 19l4 -4" />
                </svg>
                {/*
                  c={context.theme.primaryColor} rather than a hardcoded
                  "blue"/"indigo" - matches whatever theme color is
                  actually active on this InvenTree instance (confirmed
                  from a screenshot: every native dashboard item's own
                  title uses this same accent color, plain black here
                  was the actual visible mismatch against them, not a
                  missing title - removing the title entirely, tried
                  briefly on the assumption the dashboard grid already
                  showed one of its own, was confirmed wrong from that
                  same screenshot: the title only ever appeared once).
                */}
                <Title order={4} c={context.theme.primaryColor}>Import harness from OMG</Title>
            </Group>
            <Text size="sm" c="dimmed">
                Search OMG's harness part numbers, then import — creates the
                harness's InvenTree part if it doesn't exist yet, or updates
                its BOM if it does.
            </Text>

            {credentialStatus && (
                <Group gap="md">
                    {([
                        ['OMG URL', credentialStatus.omg_harness_api_url],
                        ['OMG User Token', credentialStatus.omg_inventree_user_token],
                        ['Webhook Token', credentialStatus.inventree_webhook_token],
                        ['Mouser Key', credentialStatus.mouser_api_key],
                    ] as [string, boolean][]).map(([label, configured]) => (
                        <Badge
                            key={label}
                            size="sm"
                            variant="light"
                            color={configured ? 'green' : 'red'}
                            leftSection={configured ? '✓' : '✗'}
                        >
                            {label}
                        </Badge>
                    ))}
                </Group>
            )}

            <Group gap="xs" align="flex-end">
                <TextInput
                    label="Search"
                    placeholder="e.g. R1300G"
                    value={query}
                    onChange={(e) => setQuery(e.currentTarget.value)}
                    onKeyDown={(e) => e.key === 'Enter' && runSearch()}
                    style={{ flex: 1 }}
                />
                <Select
                    label="Category"
                    description="Only needed if this harness doesn't exist in InvenTree yet"
                    placeholder="Type to search categories"
                    searchable
                    clearable
                    searchValue={categorySearchTerm}
                    onSearchChange={setCategorySearchTerm}
                    data={categoryOptions}
                    value={categoryPk === '' ? null : String(categoryPk)}
                    onChange={(v) => setCategoryPk(v ? Number(v) : '')}
                    nothingFoundMessage={categorySearching ? 'Searching…' : 'Type at least 2 characters'}
                    style={{ width: 280 }}
                />
                <Button onClick={runSearch} loading={searching}>Search</Button>
            </Group>

            {searchError && <Alert color="orange">{searchError}</Alert>}
            {importMessage && (
                <Alert color={importMessage.isError ? 'red' : 'green'}>
                    <Stack gap={6}>
                        <Text size="sm">{importMessage.text}</Text>
                        {importMessage.partPk !== undefined && (
                            <Group>
                                <Button
                                    size="xs"
                                    variant="light"
                                    onClick={() => context.navigate(`/part/${importMessage.partPk}/omg-harness-sync`)}
                                >
                                    Review in InvenTree
                                </Button>
                            </Group>
                        )}
                    </Stack>
                </Alert>
            )}

            {hasSearched && !searching && !searchError && results.length === 0 && (
                <Text size="sm" c="dimmed">No matching harnesses found in OMG for "{query}".</Text>
            )}

            {results.length > 0 && (
                <Stack gap={6} style={{ maxHeight: 220, overflowY: 'auto' }}>
                    {results.map((r) => (
                        <Group key={r.id} justify="space-between" wrap="nowrap"
                               style={{ border: '1px solid var(--mantine-color-gray-3)', borderRadius: 4, padding: '8px 12px' }}>
                            <div>
                                <Group gap={6}>
                                    <Text fw={500} size="sm">{r.part_number}</Text>
                                    {r.diagram_status !== undefined && <Badge size="xs" variant="light">diagram {r.diagram_status}</Badge>}
                                </Group>
                                <Text size="xs" c="dimmed">{r.description}</Text>
                            </div>
                            <Button
                                size="xs"
                                variant="outline"
                                loading={importingPartNumber === r.part_number}
                                onClick={() => importHarness(r.part_number)}
                            >
                                Import
                            </Button>
                        </Group>
                    ))}
                </Stack>
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
