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
    const [importMessage, setImportMessage] = useState<{ text: string; isError: boolean } | null>(null);

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
            setImportMessage({
                text: `Imported ${partNumber} — ${batch.matched_items ?? 0} matched, ${batch.flagged_items ?? 0} need review.`,
                isError: false,
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
            <Title order={4}>Import harness from OMG</Title>
            <Text size="sm" c="dimmed">
                Search OMG's harness part numbers, then import — creates the
                harness's InvenTree part if it doesn't exist yet, or updates
                its BOM if it does.
            </Text>

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
                <Alert color={importMessage.isError ? 'red' : 'green'}>{importMessage.text}</Alert>
            )}

            {results.length > 0 && (
                <Stack gap={6}>
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
