import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Anchor, Badge, Button, Group, Select, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';

import { ApiEndpoints, checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

// A sync is one long server-side request: InvenTree fetches the BOM from
// OMG, matches/updates every BOM line, then reports back to OMG's
// webhook. That regularly takes longer than the 5s default timeout on
// InvenTree's API client ("timeout of 5000ms exceeded"), so the panel
// reported a failure even when the sync finished fine. Only the
// import/sync calls get the longer timeout; everything else keeps the default.
const SYNC_TIMEOUT_MS = 120000;

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

interface PendingPartInfo {
    id: number;
    mpn: string;
    kind: string;
    manufacturer: string;
    description: string;
    url: string;
    spn: string;
    resolved_inventree_pk: number | null;
}

// Live details from OMG (POST /api/inventree/review-context/), attached
// by the plugin's queue endpoint - null when the item doesn't trace back
// to an OMG object, or OMG couldn't be reached.
interface OmgContext {
    label: string;
    wire_no?: number | string | null;
    part_no: string;
    description: string;
    linked_inventree_pk: number | null;
    pending_part: PendingPartInfo | null;
}

interface UnresolvedItem {
    id: number;
    part_number: string;
    reason: string;
    notes: string;
    candidates: Candidate[];
    omg_object_type: string | null;
    omg_object_id: number | null;
    omg_context?: OmgContext | null;
}

interface InvenTreePartRow {
    pk: number;
    name: string;
    IPN: string;
    description: string;
}

interface SupplierSearchResult {
    id: string;
    sku: string;
    name: string;
    description: string;
    price: string | null;
    link: string;
    existing_part_id: number | null;
}

// Review list sections, in display order. Items not tied to an OMG object
// (e.g. a stale BOM line, or a harness rename InvenTree refused) go last.
const REVIEW_GROUPS: { key: string; title: string }[] = [
    { key: 'connector', title: 'Connectors' },
    { key: 'wire', title: 'Wires' },
    { key: 'multicore', title: 'Multicore cables' },
    { key: 'accessory', title: 'Accessories' },
    { key: 'junction', title: 'Junctions' },
    { key: 'sub_harness', title: 'Sub-harnesses' },
    { key: 'other', title: 'Other' },
];

// "J2" before "J10", "Wire 9" before "Wire 17".
const naturalCompare = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' }).compare;

function reviewTitle(item: UnresolvedItem): string {
    return item.omg_context?.label || item.part_number;
}

// Wires sort by their actual WireNo from OMG (numeric-aware, so "12A"
// sits after "12"); everything else - and a wire OMG didn't return a
// number for - sorts by its title.
function reviewSortKey(item: UnresolvedItem): string {
    const wireNo = item.omg_context?.wire_no;
    if (item.omg_object_type === 'wire' && wireNo !== null && wireNo !== undefined && wireNo !== '') {
        return String(wireNo);
    }
    return reviewTitle(item);
}

const PLUGIN_SLUG = 'omg-harness-import';
const SUPPLIER_SLUG = 'mouser';

/**
 * One item in the "Needs review" list. Besides the existing candidate
 * picks and Dismiss, it can:
 *   - Search InvenTree and link an existing part.
 *   - Import the part from Mouser through InvenTree's own supplier import
 *     (the same /api/supplier/import/ endpoint - and so the same
 *     MouserSupplierMixin code - as Parts -> Add Parts -> Import from
 *     Supplier). If OMG has a Mouser pending part for this item, its MPN
 *     is already selected: pick a category and import.
 *   - Create a part by hand with InvenTree's own "Add Part" form (for
 *     parts that aren't on Mouser), pre-filled from OMG's part number,
 *     description and - if there's a pending part - its Mouser link.
 * Resolving reports back to OMG. For a pending part, OMG first checks it's
 * still the same pending part (exists, still used, not resolved elsewhere,
 * same MPN) and refuses otherwise - the item then stays in the queue.
 */
function ReviewItem({ item, context, onChanged }: {
    item: UnresolvedItem;
    context: InvenTreePluginContext;
    onChanged: () => Promise<void>;
}) {
    const omg = item.omg_context || null;
    const pending = omg?.pending_part || null;
    const defaultTerm = pending?.mpn || omg?.part_no || '';

    const [mode, setMode] = useState<'none' | 'search' | 'import'>('none');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [createdPartPk, setCreatedPartPk] = useState<number | null>(null);

    // Search InvenTree
    const [term, setTerm] = useState(defaultTerm);
    const [partResults, setPartResults] = useState<InvenTreePartRow[] | null>(null);

    // Import from Mouser
    const [supplierTerm, setSupplierTerm] = useState(defaultTerm);
    const [supplierResults, setSupplierResults] = useState<SupplierSearchResult[] | null>(null);
    const [selectedSku, setSelectedSku] = useState<string | null>(pending?.mpn || null);
    const [categorySearch, setCategorySearch] = useState('');
    const [categoryOptions, setCategoryOptions] = useState<{ value: string; label: string }[]>([]);
    const [categoryPk, setCategoryPk] = useState<string | null>(null);

    useEffect(() => {
        if (categorySearch.trim().length < 2) return;
        const timer = setTimeout(async () => {
            try {
                const response = await context.api.get('/api/part/category/', {
                    params: { search: categorySearch.trim(), limit: 20 },
                });
                const rows = response.data?.results ?? response.data ?? [];
                setCategoryOptions(rows.map((c: any) => ({ value: String(c.pk), label: c.pathstring || c.name })));
            } catch {
                setCategoryOptions([]);
            }
        }, 400);
        return () => clearTimeout(timer);
    }, [categorySearch, context.api]);

    const resolve = useCallback(async (action: 'link' | 'created' | 'dismiss', partPk?: number) => {
        setBusy(true);
        setError(null);
        try {
            await context.api.post(`/plugin/${PLUGIN_SLUG}/unresolved/${item.id}/resolve/`, {
                action,
                part_pk: partPk,
                ...(pending && action !== 'dismiss' ? { pending_part_id: pending.id, pending_mpn: pending.mpn } : {}),
            }, { timeout: SYNC_TIMEOUT_MS });
            notifications.show({
                title: action === 'dismiss' ? 'Dismissed' : 'Linked',
                message: action === 'dismiss' ? 'Item dismissed.' : 'Linked in InvenTree and reported to OMG.',
                color: 'green',
            });
            await onChanged();
        } catch (err: any) {
            const detail = err?.response?.data?.detail || err.message;
            setError(detail);
        } finally {
            setBusy(false);
        }
    }, [context.api, item.id, pending, onChanged]);

    const searchInvenTree = useCallback(async () => {
        if (!term.trim()) return;
        setBusy(true);
        setError(null);
        try {
            const response = await context.api.get('/api/part/', { params: { search: term.trim(), limit: 10 } });
            setPartResults(response.data?.results ?? response.data ?? []);
        } catch (err: any) {
            setError(err?.response?.data?.detail || err.message);
        } finally {
            setBusy(false);
        }
    }, [context.api, term]);

    const searchMouser = useCallback(async () => {
        if (!supplierTerm.trim()) return;
        setBusy(true);
        setError(null);
        try {
            const response = await context.api.get('/api/supplier/search/', {
                params: { plugin: PLUGIN_SLUG, supplier: SUPPLIER_SLUG, term: supplierTerm.trim() },
                timeout: SYNC_TIMEOUT_MS,
            });
            const rows: SupplierSearchResult[] = response.data ?? [];
            setSupplierResults(rows);
            const exact = rows.find((r) => r.sku.toLowerCase() === supplierTerm.trim().toLowerCase());
            setSelectedSku(exact ? exact.sku : null);
        } catch (err: any) {
            setError(err?.response?.data?.error || err?.response?.data?.detail || err.message);
        } finally {
            setBusy(false);
        }
    }, [context.api, supplierTerm]);

    const importFromMouser = useCallback(async () => {
        if (!selectedSku || !categoryPk) return;
        setBusy(true);
        setError(null);
        let partPk: number | null = null;
        try {
            const response = await context.api.post('/api/supplier/import/', {
                plugin: PLUGIN_SLUG,
                supplier: SUPPLIER_SLUG,
                part_import_id: selectedSku,
                category_id: Number(categoryPk),
            }, { timeout: SYNC_TIMEOUT_MS });
            partPk = response.data?.part_id ?? null;
        } catch (err: any) {
            setError(`Import from Mouser failed: ${err?.response?.data?.detail || err.message}`);
            setBusy(false);
            return;
        }
        setCreatedPartPk(partPk);
        setBusy(false);
        if (partPk) await resolve('created', partPk);
    }, [context.api, selectedSku, categoryPk, resolve]);

    // InvenTree's own "Add Part" form (the same one as Parts -> Add Part):
    // field definitions, validation and the category picker all come from
    // InvenTree itself. Pre-filled from OMG; on save, the new part is
    // linked and reported back exactly like a Mouser import.
    const createPartForm = context.forms.create({
        url: ApiEndpoints.part_list,
        title: 'Create part in InvenTree',
        fields: {
            category: {},
            name: {},
            IPN: {},
            description: {},
            keywords: {},
            link: {},
            units: {},
            component: {},
            purchaseable: {},
            active: {},
        },
        initialData: {
            // The part number typed in OMG (ConnectorPartNo, ConductorPartNo,
            // accessory/junction PartNo), else the pending Mouser MPN. Never
            // the review item's title - that's a label like "J1", not a part number.
            name: omg?.part_no || pending?.mpn || '',
            description: omg?.description || pending?.description || '',
            link: pending?.url || '',
            component: true,
            purchaseable: true,
            active: true,
        },
        follow: false,
        successMessage: null,
        onFormSuccess: (data: any) => {
            if (data?.pk) {
                setCreatedPartPk(data.pk);
                resolve('created', data.pk);
            }
        },
    });

    return (
        <Alert color={pending ? 'blue' : 'yellow'} title={reviewTitle(item)}>
            <Stack gap={6}>
                <Text size="sm">{item.notes}</Text>

                {omg && (omg.part_no || omg.description) && (
                    <Text size="xs" c="dimmed">
                        In OMG: {omg.part_no ? <b>{omg.part_no}</b> : 'no part number'}
                        {omg.description ? ` — ${omg.description}` : ''}
                    </Text>
                )}

                {pending && (
                    <Group gap="xs">
                        <Badge color="blue" variant="light">Mouser pending part</Badge>
                        <Text size="xs"><b>{pending.mpn}</b>{pending.manufacturer ? ` (${pending.manufacturer})` : ''}</Text>
                        {pending.url && <Anchor size="xs" href={pending.url} target="_blank">View on Mouser</Anchor>}
                    </Group>
                )}

                {item.candidates.length > 0 && (
                    <Group gap="xs">
                        {item.candidates.map((c) => (
                            <Button key={c.pk} size="xs" variant="outline" loading={busy}
                                    onClick={() => resolve('link', c.pk)}>
                                Use {c.name || c.ipn}
                            </Button>
                        ))}
                    </Group>
                )}

                <Group gap="xs">
                    {item.omg_object_type && (
                        <>
                            <Button size="xs" variant={mode === 'import' ? 'filled' : 'light'}
                                    onClick={() => setMode(mode === 'import' ? 'none' : 'import')}>
                                {pending ? 'Import pending part from Mouser' : 'Import from Mouser'}
                            </Button>
                            <Button size="xs" variant={mode === 'search' ? 'filled' : 'light'}
                                    onClick={() => setMode(mode === 'search' ? 'none' : 'search')}>
                                Search InvenTree
                            </Button>
                            <Button size="xs" variant="light" onClick={() => createPartForm.open()}>
                                Create part
                            </Button>
                        </>
                    )}
                    <Button size="xs" variant="subtle" color="gray" loading={busy} onClick={() => resolve('dismiss')}>
                        Dismiss
                    </Button>
                </Group>

                {mode === 'search' && (
                    <Stack gap={6}>
                        <Group gap="xs" align="flex-end">
                            <TextInput size="xs" label="Search InvenTree parts" value={term}
                                       onChange={(e) => setTerm(e.currentTarget.value)}
                                       onKeyDown={(e) => e.key === 'Enter' && searchInvenTree()}
                                       style={{ flex: 1 }} />
                            <Button size="xs" onClick={searchInvenTree} loading={busy}>Search</Button>
                        </Group>
                        {partResults !== null && partResults.length === 0 && (
                            <Text size="xs" c="dimmed">No InvenTree parts match "{term}".</Text>
                        )}
                        {(partResults || []).map((p) => (
                            <Group key={p.pk} justify="space-between" wrap="nowrap">
                                <Text size="xs"><b>{p.name}</b>{p.IPN ? ` [${p.IPN}]` : ''} — {p.description}</Text>
                                <Button size="xs" variant="outline" loading={busy} onClick={() => resolve('link', p.pk)}>
                                    Use this part
                                </Button>
                            </Group>
                        ))}
                    </Stack>
                )}

                {mode === 'import' && (
                    <Stack gap={6}>
                        {!pending && (
                            <Group gap="xs" align="flex-end">
                                <TextInput size="xs" label="Search Mouser" value={supplierTerm}
                                           onChange={(e) => setSupplierTerm(e.currentTarget.value)}
                                           onKeyDown={(e) => e.key === 'Enter' && searchMouser()}
                                           style={{ flex: 1 }} />
                                <Button size="xs" onClick={searchMouser} loading={busy}>Search</Button>
                            </Group>
                        )}
                        {!pending && supplierResults !== null && supplierResults.length === 0 && (
                            <Text size="xs" c="dimmed">Mouser has nothing for "{supplierTerm}".</Text>
                        )}
                        {!pending && (supplierResults || []).map((r) => (
                            <Group key={r.sku} justify="space-between" wrap="nowrap">
                                <Text size="xs">
                                    <b>{r.sku}</b> — {r.description}{r.price ? ` (${r.price})` : ''}{' '}
                                    {r.link && <Anchor size="xs" href={r.link} target="_blank">Mouser</Anchor>}
                                </Text>
                                {r.existing_part_id ? (
                                    <Button size="xs" variant="outline" loading={busy}
                                            onClick={() => resolve('link', r.existing_part_id as number)}>
                                        Already in InvenTree — use it
                                    </Button>
                                ) : (
                                    <Button size="xs" variant={selectedSku === r.sku ? 'filled' : 'outline'}
                                            onClick={() => setSelectedSku(r.sku)}>
                                        {selectedSku === r.sku ? 'Selected' : 'Select'}
                                    </Button>
                                )}
                            </Group>
                        ))}

                        {selectedSku && (
                            <Group gap="xs" align="flex-end">
                                <Select
                                    size="xs"
                                    label={`Category for ${selectedSku}`}
                                    placeholder="Type to search categories"
                                    searchable
                                    searchValue={categorySearch}
                                    onSearchChange={setCategorySearch}
                                    data={categoryOptions}
                                    value={categoryPk}
                                    onChange={setCategoryPk}
                                    nothingFoundMessage="Type at least 2 characters"
                                    style={{ flex: 1 }}
                                />
                                <Button size="xs" onClick={importFromMouser} loading={busy} disabled={!categoryPk}>
                                    Import &amp; link
                                </Button>
                            </Group>
                        )}
                    </Stack>
                )}

                {createPartForm.modal}

                {error && (
                    <Alert color="red" p="xs">
                        <Text size="xs">{error}</Text>
                        {createdPartPk && (
                            <Group gap="xs" mt={4}>
                                <Text size="xs">The part was created in InvenTree (#{createdPartPk}) but isn't linked yet.</Text>
                                <Button size="xs" variant="subtle" onClick={() => context.navigate(`/part/${createdPartPk}/`)}>
                                    Open part
                                </Button>
                                <Button size="xs" variant="subtle" onClick={() => resolve('created', createdPartPk)}>
                                    Try linking again
                                </Button>
                            </Group>
                        )}
                    </Alert>
                )}
            </Stack>
        </Alert>
    );
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
    const [queueContextError, setQueueContextError] = useState<string | null>(null);

    const groupedQueue = useMemo(() => {
        const known = new Set(REVIEW_GROUPS.map((g) => g.key));
        return REVIEW_GROUPS
            .map((group) => ({
                ...group,
                items: queue
                    .filter((item) => (known.has(item.omg_object_type || '') ? item.omg_object_type : 'other') === group.key)
                    .sort((a, b) => naturalCompare(reviewSortKey(a), reviewSortKey(b))),
            }))
            .filter((group) => group.items.length > 0);
    }, [queue]);

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
            // {items, omg_context_error} when scoped to a part (plain list from older plugin versions)
            const data = response.data;
            setQueue(Array.isArray(data) ? data : (data?.items || []));
            setQueueContextError(Array.isArray(data) ? null : (data?.omg_context_error || null));
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
            }, { timeout: SYNC_TIMEOUT_MS });
            setLastBatch(response.data);
            const flagged = response.data?.flagged_items || 0;
            notifications.show({
                title: 'Sync complete',
                message: flagged > 0
                    ? `Synced — ${flagged} item(s) need review below.`
                    : 'Synced — everything matched cleanly.',
                color: flagged > 0 ? 'yellow' : 'green',
            });
            if (response.data?.reconciliation_pushed === false) {
                notifications.show({
                    title: 'Report-back to OMG failed',
                    message: 'The sync itself succeeded, but InvenTree could not report the result back to OMG. Check the InvenTree Webhook Token setting.',
                    color: 'orange',
                });
            }
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
            }, { timeout: SYNC_TIMEOUT_MS });
            setLastBatch(response.data);
            setIsLinked(true);
            notifications.show({
                title: 'Linked',
                message: `Linked to ${partNumber} and imported its BOM.`,
                color: 'green',
            });
            if (response.data?.reconciliation_pushed === false) {
                notifications.show({
                    title: 'Report-back to OMG failed',
                    message: 'The link/import itself succeeded, but InvenTree could not report the result back to OMG. Check the InvenTree Webhook Token setting.',
                    color: 'orange',
                });
            }
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
                    {queueContextError && <Alert color="orange">{queueContextError}</Alert>}
                    <Stack gap="md">
                        {groupedQueue.map((group) => (
                            <Stack key={group.key} gap="xs">
                                <Group gap="xs">
                                    <Title order={6}>{group.title}</Title>
                                    <Badge size="sm" variant="light" color="yellow">{group.items.length}</Badge>
                                </Group>
                                {group.items.map((item) => (
                                    <ReviewItem key={item.id} item={item} context={context}
                                                onChanged={async () => { await loadQueue(); await loadStatus(); }} />
                                ))}
                            </Stack>
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
