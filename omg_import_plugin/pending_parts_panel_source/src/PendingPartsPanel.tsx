import { useCallback, useEffect, useState } from 'react';
import { Alert, Anchor, Badge, Button, Checkbox, Group, Loader, Stack, Table, Tabs, Text } from '@mantine/core';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

/**
 * "OMG Parts" dashboard item - two tabs:
 *   Pending      parts picked from Mouser in OMG, not in InvenTree yet (pending_parts.py)
 *   Needs setup  OMG's Part Data Check: parts OMG uses that are missing
 *                what it needs (read from OMG; OMG re-checks in the background)
 *
 * Pending:
 * Parts picked from Mouser inside OMG that aren't in InvenTree yet. Tick
 * and import: an exact match already in InvenTree is just linked; anything
 * else is created from Mouser (manufacturer part, Mouser supplier part,
 * prices, OMG's parameters) in the PENDING category - then moved by hand
 * to its own category (the list under the table). InvenTree's own Import
 * from Supplier wizard isn't affected.
 */

interface PartRow { pk: number; name: string; description: string; category: string; }
interface Use { harness: string; what: string; }
interface Pending {
    id: number; kind: string; mpn: string; spn: string; manufacturer: string; description: string; url: string;
    used_by: Use[]; matches: PartRow[];
}
interface State {
    error: string | null;
    items: Pending[];
    category: { pk: number; name: string } | null;
    in_category: PartRow[];
    mouser_key_set: boolean;
}
interface Result { id: number; mpn: string; status: string; part: PartRow | null; message: string; }

const URL = '/plugin/omg-harness-import/pending-parts/';

// A part link that opens INSIDE InvenTree (its router - no page reload, so no
// login check on every click) on the right panel: OMG Part Setup, or OMG
// Cavities for cavity problems. Ctrl / middle-click still opens a new tab.
function PartLink({ context, pk, name, panel = 'omg-part-setup' }:
    { context: InvenTreePluginContext; pk: number; name: string; panel?: string }) {
    const path = `/part/${pk}/${panel}`;
    return (
        <Anchor size="sm" href={`/web${path}`} onClick={(e: React.MouseEvent) => {
            if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            context.navigate(path);
        }}>{name}</Anchor>
    );
}

// Data-check problems that are fixed on the OMG Cavities panel
const CAVITY_CHECKS = new Set(['cavity_map', 'count', 'series']);
// These calls reach OMG (and an import reaches Mouser per part) - far longer
// than the 5s default of InvenTree's API client ("timeout of 5000ms exceeded").
const READ_TIMEOUT_MS = 60000;
const WRITE_TIMEOUT_MS = 600000;
const STATUS_COLOUR: Record<string, string> = { created: 'green', linked: 'blue', ambiguous: 'orange', failed: 'red' };

function apiError(err: any, fallback: string) {
    return err?.response?.data?.detail || err?.message || fallback;
}

function usesText(uses: Use[]) {
    const shown = uses.slice(0, 3).map((u) => `${u.harness} - ${u.what}`).join('; ');
    return uses.length > 3 ? `${shown} and ${uses.length - 3} more` : shown;
}

function PendingPartsItem({ context }: { context: InvenTreePluginContext }) {
    const [state, setState] = useState<State | null>(null);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [picked, setPicked] = useState<Set<number>>(new Set());
    const [importing, setImporting] = useState(false);
    const [results, setResults] = useState<Result[] | null>(null);
    const [reportErrors, setReportErrors] = useState<string[]>([]);

    const load = useCallback(async () => {
        setLoading(true);
        setLoadError(null);
        try {
            const response = await context.api.get(URL, { timeout: READ_TIMEOUT_MS });
            setState(response.data);
            setPicked(new Set());
        } catch (err) {
            setLoadError(apiError(err, "Couldn't load OMG's pending parts."));
        } finally {
            setLoading(false);
        }
    }, [context.api]);

    useEffect(() => { load(); }, [load]);

    const toggle = (id: number) => setPicked((old) => {
        const next = new Set(old);
        next.has(id) ? next.delete(id) : next.add(id);
        return next;
    });

    const runImport = async () => {
        setImporting(true);
        setResults(null);
        setReportErrors([]);
        try {
            const response = await context.api.post(URL, { ids: [...picked] }, { timeout: WRITE_TIMEOUT_MS });
            setResults(response.data.results || []);
            setReportErrors(response.data.report_errors || []);
            setState(response.data.state);
            setPicked(new Set());
        } catch (err) {
            setReportErrors([apiError(err, 'The import failed.')]);
        } finally {
            setImporting(false);
        }
    };

    if (loading) return <Group gap="xs"><Loader size="sm" /><Text size="sm">Asking OMG for pending parts...</Text></Group>;
    if (loadError) return <Alert color="red">{loadError}</Alert>;
    if (!state) return null;

    const items = state.items || [];
    const allPicked = items.length > 0 && picked.size === items.length;
    const partLink = (p: PartRow, panel?: string) => <PartLink context={context} pk={p.pk} name={p.name} panel={panel} />;

    return (
        <Stack gap="xs">
            {state.error && <Alert color="red">{state.error}</Alert>}
            {!state.mouser_key_set && (
                <Alert color="yellow">No Mouser API key in the plugin settings - parts are created from the data OMG
                    stored when they were picked (prices may be out of date).</Alert>
            )}
            <Text size="xs" c="dimmed">
                New parts go into <b>{state.category ? state.category.name : 'OMG Pending Parts (created on first import)'}</b> -
                move each to its own category afterwards. A part already in InvenTree (same name, MPN or Mouser number)
                is linked instead of created.
            </Text>

            {items.length === 0
                ? <Text size="sm" c="dimmed">No pending parts - everything OMG uses is in InvenTree.</Text>
                : (
                    <div style={{ maxHeight: 320, overflowY: 'auto' }}>
                        <Table striped withTableBorder verticalSpacing={4}>
                            <Table.Thead>
                                <Table.Tr>
                                    <Table.Th w={30}>
                                        <Checkbox size="xs" checked={allPicked}
                                            indeterminate={picked.size > 0 && !allPicked}
                                            onChange={() => setPicked(allPicked ? new Set() : new Set(items.map((i) => i.id)))} />
                                    </Table.Th>
                                    <Table.Th>Part</Table.Th>
                                    <Table.Th>Used on</Table.Th>
                                    <Table.Th>In InvenTree</Table.Th>
                                </Table.Tr>
                            </Table.Thead>
                            <Table.Tbody>
                                {items.map((i) => (
                                    <Table.Tr key={i.id}>
                                        <Table.Td><Checkbox size="xs" checked={picked.has(i.id)} onChange={() => toggle(i.id)} /></Table.Td>
                                        <Table.Td>
                                            <Group gap={6}>
                                                <Text size="sm" fw={600}>{i.mpn}</Text>
                                                <Badge size="xs" variant="light">{i.kind}</Badge>
                                            </Group>
                                            <Text size="xs" c="dimmed">
                                                {[i.manufacturer, i.description].filter(Boolean).join(' - ')}
                                                {i.url && <> · <Anchor size="xs" href={i.url} target="_blank">Mouser{i.spn ? ` ${i.spn}` : ''}</Anchor></>}
                                            </Text>
                                        </Table.Td>
                                        <Table.Td><Text size="xs">{usesText(i.used_by)}</Text></Table.Td>
                                        <Table.Td>
                                            {i.matches.length === 0 && <Text size="xs" c="dimmed">No - will be created</Text>}
                                            {i.matches.length === 1 && <Text size="xs">Yes - {partLink(i.matches[0])} (will be linked)</Text>}
                                            {i.matches.length > 1 && <Text size="xs" c="orange">{i.matches.length} parts match - choose in the review list</Text>}
                                        </Table.Td>
                                    </Table.Tr>
                                ))}
                            </Table.Tbody>
                        </Table>
                    </div>
                )}

            <Group>
                <Button size="xs" onClick={runImport} loading={importing} disabled={!picked.size}>
                    Import {picked.size || ''} {picked.size === 1 ? 'part' : 'parts'}
                </Button>
                <Button size="xs" variant="subtle" onClick={load} disabled={importing}>Refresh</Button>
            </Group>

            {results && (
                <Stack gap={2}>
                    {results.map((r) => (
                        <Group key={r.id} gap={6} wrap="nowrap" align="flex-start">
                            <Badge size="xs" color={STATUS_COLOUR[r.status] || 'gray'}>{r.status}</Badge>
                            <Text size="xs">{r.part ? partLink(r.part) : r.mpn}{r.message ? ` - ${r.message}` : ''}</Text>
                        </Group>
                    ))}
                </Stack>
            )}
            {reportErrors.length > 0 && (
                <Alert color="orange" title="OMG wasn't told about everything">
                    {reportErrors.map((e) => <Text key={e} size="xs">{e}</Text>)}
                    <Text size="xs">Check the InvenTree Webhook Token setting; the next harness sync links them anyway.</Text>
                </Alert>
            )}

            {state.in_category.length > 0 && (
                <Stack gap={2}>
                    <Text size="xs" fw={600}>Still in {state.category?.name} - move to their own category:</Text>
                    <Text size="xs">
                        {state.in_category.map((p, n) => <span key={p.pk}>{n > 0 && ', '}{partLink(p, 'details')}</span>)}
                    </Text>
                </Stack>
            )}
        </Stack>
    );
}

// ==================== Needs setup (OMG's Part Data Check) ====================

interface CheckIssue { part: number; name: string; message: string; used_by: Use[]; used_count: number; }
interface Check { key: string; title: string; issues: CheckIssue[]; }
interface Stored {
    report: { checks: Check[]; parts_checked: number; missing_templates: string[] } | null;
    checked_at: string | null;
    running: boolean;
    error: string;
}

const CHECK_URL = '/plugin/omg-harness-import/data-check/';
const POLL_MS = 3000;

function NeedsSetup({ context, onCount }: { context: InvenTreePluginContext; onCount: (n: number) => void }) {
    const [stored, setStored] = useState<Stored | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async (refresh = false) => {
        try {
            const response = await context.api.get(CHECK_URL, { params: refresh ? { refresh: 1 } : {},
                                                                timeout: READ_TIMEOUT_MS });
            setStored(response.data);
            setError(null);
            const checks = response.data.report?.checks || [];
            onCount(checks.reduce((n: number, c: Check) => n + c.issues.length, 0));
        } catch (err) {
            setError(apiError(err, "Couldn't read OMG's part data check."));
        }
    }, [context.api, onCount]);

    useEffect(() => { load(); }, [load]);
    // OMG checks in the background - poll until it's done
    useEffect(() => {
        if (!stored?.running) return;
        const timer = setTimeout(() => load(), POLL_MS);
        return () => clearTimeout(timer);
    }, [stored, load]);

    if (error) return <Alert color="red">{error}</Alert>;
    if (!stored) return <Group gap="xs"><Loader size="sm" /><Text size="sm">Reading OMG's data check...</Text></Group>;

    const report = stored.report;
    const checks = (report?.checks || []).filter((c) => c.issues.length);
    const total = checks.reduce((n, c) => n + c.issues.length, 0);
    const partLink = (i: CheckIssue, key: string) => (
        <PartLink context={context} pk={i.part} name={i.name} panel={CAVITY_CHECKS.has(key) ? 'omg-cavities' : 'omg-part-setup'} />
    );

    return (
        <Stack gap="xs">
            <Group justify="space-between">
                <Text size="xs" c="dimmed">
                    {stored.running ? 'OMG is checking every part...'
                        : stored.checked_at ? `Checked ${new Date(stored.checked_at).toLocaleString()}` : 'Not checked yet.'}
                    {report && !stored.running && ` - ${report.parts_checked} parts, ${total} problems (parts on a harness first).`}
                </Text>
                <Button size="xs" variant="light" onClick={() => load(true)} loading={stored.running}>Check again</Button>
            </Group>
            {stored.error && !stored.running && <Alert color="red">The last check failed: {stored.error}</Alert>}
            {report && report.missing_templates.length > 0 && (
                <Alert color="yellow">No InvenTree template yet for {report.missing_templates.join(', ')} - OMG's InvenTree
                    Settings page (Part Parameters) can create them.</Alert>
            )}
            {report && total === 0 && !stored.running && <Text size="sm">Nothing to fix - every part OMG uses is set up.</Text>}
            <div style={{ maxHeight: 340, overflowY: 'auto' }}>
                <Stack gap="sm">
                    {checks.map((c) => (
                        <Stack key={c.key} gap={2}>
                            <Group gap={6}><Text size="sm" fw={600}>{c.title}</Text><Badge size="xs" color="orange">{c.issues.length}</Badge></Group>
                            <Table withTableBorder verticalSpacing={2}>
                                <Table.Tbody>
                                    {c.issues.map((i) => (
                                        <Table.Tr key={`${c.key}-${i.part}-${i.message}`}>
                                            <Table.Td w="28%">{partLink(i, c.key)}</Table.Td>
                                            <Table.Td><Text size="xs">{i.message}</Text></Table.Td>
                                            <Table.Td w="34%">
                                                <Text size="xs" c={i.used_count ? undefined : 'dimmed'}>
                                                    {i.used_count ? usesText(i.used_by) + (i.used_count > i.used_by.length
                                                        ? ` (${i.used_count} uses)` : '') : 'Not used on a harness'}
                                                </Text>
                                            </Table.Td>
                                        </Table.Tr>
                                    ))}
                                </Table.Tbody>
                            </Table>
                        </Stack>
                    ))}
                </Stack>
            </div>
            <Text size="xs" c="dimmed">Fix a part on its page (OMG Part Setup and OMG Cavities panels), then check again.</Text>
        </Stack>
    );
}

// ==================== Auto-fill (bulk_fill.py) ====================

interface FillItem {
    part: { pk: number; name: string; description: string; category: string };
    component_type: string;
    type_known: boolean;
    values: Record<string, string>;
}
interface FillPreview {
    error: string | null;
    items: FillItem[];
    labels: Record<string, string>;
    missing_templates: string[];
    refused: { template: string; allowed: string[]; values: Record<string, string[]> }[];
    parts_checked: number;
}
interface FillError { name: string; role: string; message: string }

// "Component Type: Invalid choice for parameter value - 58 parts: W4P, W4S, ..."
function groupErrors(errors: FillError[], labels: Record<string, string>) {
    const groups: Record<string, string[]> = {};
    errors.forEach((e) => (groups[`${(labels[e.role] || e.role).split(/ \(| - /)[0]}: ${e.message}`] ||= []).push(e.name));
    return Object.entries(groups).sort((a, b) => b[1].length - a[1].length);
}

const FILL_URL = '/plugin/omg-harness-import/bulk-fill/';

function AutoFill({ context }: { context: InvenTreePluginContext }) {
    const [preview, setPreview] = useState<FillPreview | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [picked, setPicked] = useState<Set<number>>(new Set());
    const [applying, setApplying] = useState(false);
    const [result, setResult] = useState<{ written: number; parts: number; errors: FillError[] } | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const response = await context.api.get(FILL_URL, { timeout: READ_TIMEOUT_MS });
            if (response.data.error) throw new Error(response.data.error);
            setPreview(response.data);
            setPicked(new Set((response.data.items || []).map((i: FillItem) => i.part.pk)));
        } catch (err) {
            setError(apiError(err, "Couldn't work out the values."));
        } finally {
            setLoading(false);
        }
    }, [context.api]);

    const items = preview?.items || [];
    const byType = items.reduce<Record<string, FillItem[]>>((acc, i) => {
        (acc[i.component_type] ||= []).push(i);
        return acc;
    }, {});
    const toggle = (pk: number) => setPicked((old) => {
        const next = new Set(old);
        next.has(pk) ? next.delete(pk) : next.add(pk);
        return next;
    });
    const setGroup = (group: FillItem[], on: boolean) => setPicked((old) => {
        const next = new Set(old);
        group.forEach((i) => (on ? next.add(i.part.pk) : next.delete(i.part.pk)));
        return next;
    });

    const runApply = async () => {
        setApplying(true);
        setResult(null);
        try {
            const chosen = items.filter((i) => picked.has(i.part.pk)).map((i) => ({ part: i.part.pk, values: i.values }));
            const response = await context.api.post(FILL_URL, { items: chosen }, { timeout: WRITE_TIMEOUT_MS });
            setResult(response.data);
            await load();
        } catch (err) {
            setError(apiError(err, 'Filling the parameters failed.'));
        } finally {
            setApplying(false);
        }
    };

    if (!preview) {
        return (
            <Stack gap="xs">
                <Text size="xs" c="dimmed">
                    Works out each part's Component Type and OMG's parameters from its description and category
                    ("Recept., DT, 8 way" = Connector, DT, 8 cavities, Receptacle; "Wire TXL 16AWG ... Black" = Wire,
                    16 AWG / 1.5 mm², TXL, Black) - only for parameters a part doesn't have yet. You see everything
                    before it's written; nothing already set is changed.
                </Text>
                {error && <Alert color="red">{error}</Alert>}
                <Group><Button size="xs" onClick={load} loading={loading}>Work out values</Button></Group>
            </Stack>
        );
    }

    return (
        <Stack gap="xs">
            <Group justify="space-between">
                <Text size="xs" c="dimmed">
                    {items.length} of {preview.parts_checked} parts have something to fill. Untick anything that's wrong
                    - a blank is flagged by OMG's data check, a wrong value isn't.
                </Text>
                <Button size="xs" variant="subtle" onClick={load} loading={loading}>Work out again</Button>
            </Group>
            {error && <Alert color="red">{error}</Alert>}
            {result && (
                <Alert color={result.errors.length ? 'orange' : 'green'} p="xs">
                    <Text size="xs" fw={600}>
                        {result.written} values set on {result.parts} parts
                        {result.errors.length > 0 && ` - ${result.errors.length} refused`}.
                    </Text>
                    <div style={{ maxHeight: 110, overflowY: 'auto' }}>
                        {groupErrors(result.errors, preview.labels).map(([what, names]) => (
                            <Text key={what} size="xs">
                                {what} - {names.length} {names.length === 1 ? 'part' : 'parts'}: {names.slice(0, 8).join(', ')}
                                {names.length > 8 && ` and ${names.length - 8} more`}
                            </Text>
                        ))}
                    </div>
                    {result.errors.length === 0 && (
                        <Text size="xs">Next: OMG's InvenTree Settings - Selection Lists - "Fill lists from parts",
                            then check each list and turn it on.</Text>
                    )}
                </Alert>
            )}
            {(preview.refused || []).map((r) => {
                const count = Object.values(r.values).reduce((n, names) => n + names.length, 0);
                return (
                    <Alert key={r.template} color="red" p="xs">
                        <Text size="xs" fw={600}>
                            InvenTree's {r.template} template won't accept {Object.keys(r.values).join(', ')}
                            {' '}({count} {count === 1 ? 'part' : 'parts'}) - it only allows {r.allowed.join(', ')}.
                        </Text>
                        <Text size="xs">
                            Those values would be refused. In OMG: InvenTree Settings - Selection Lists - "Set up lists"
                            (it turns OMG's own lists on, which replaces the old choices), then "Work out again" here.
                        </Text>
                    </Alert>
                );
            })}
            {preview.missing_templates.length > 0 && (
                <Alert color="yellow">No InvenTree template yet for {preview.missing_templates.join(', ')} - those
                    values will be skipped (OMG's InvenTree Settings page can create the templates).</Alert>
            )}
            <div style={{ maxHeight: 340, overflowY: 'auto' }}>
                <Stack gap="sm">
                    {Object.entries(byType).sort((a, b) => b[1].length - a[1].length).map(([ctype, group]) => {
                        const all = group.every((i) => picked.has(i.part.pk));
                        return (
                            <Stack key={ctype} gap={2}>
                                <Group gap={6}>
                                    <Checkbox size="xs" checked={all} indeterminate={!all && group.some((i) => picked.has(i.part.pk))}
                                        onChange={() => setGroup(group, !all)} />
                                    <Text size="sm" fw={600}>{ctype}</Text>
                                    <Badge size="xs" variant="light">{group.length}</Badge>
                                </Group>
                                <Table withTableBorder verticalSpacing={2}>
                                    <Table.Tbody>
                                        {group.map((i) => (
                                            <Table.Tr key={i.part.pk}>
                                                <Table.Td w={30}><Checkbox size="xs" checked={picked.has(i.part.pk)} onChange={() => toggle(i.part.pk)} /></Table.Td>
                                                <Table.Td w="30%">
                                                    <PartLink context={context} pk={i.part.pk} name={i.part.name} />
                                                    <Text size="xs" c="dimmed" lineClamp={2}>{i.part.description}</Text>
                                                </Table.Td>
                                                <Table.Td>
                                                    <Group gap={4}>
                                                        {Object.entries(i.values).map(([role, value]) => (
                                                            <Badge key={role} size="xs" variant={role === 'part.component_type' ? 'filled' : 'light'}
                                                                style={{ textTransform: 'none' }}>
                                                                {(preview.labels[role] || role).split(/ \(| - /)[0]}: {value}
                                                            </Badge>
                                                        ))}
                                                    </Group>
                                                </Table.Td>
                                            </Table.Tr>
                                        ))}
                                    </Table.Tbody>
                                </Table>
                            </Stack>
                        );
                    })}
                </Stack>
            </div>
            <Group>
                <Button size="xs" onClick={runApply} loading={applying} disabled={!picked.size}>
                    Fill {picked.size} {picked.size === 1 ? 'part' : 'parts'}
                </Button>
            </Group>
        </Stack>
    );
}

function OMGPartsItem({ context }: { context: InvenTreePluginContext }) {
    const [problems, setProblems] = useState<number | null>(null);
    return (
        <Stack gap="xs">
            <Text size="sm" fw={600} c={context.theme.primaryColor}>OMG Parts</Text>
            <Tabs defaultValue="pending">
                <Tabs.List>
                    <Tabs.Tab value="pending">Pending (Mouser)</Tabs.Tab>
                    <Tabs.Tab value="setup" rightSection={problems ? <Badge size="xs" color="orange">{problems}</Badge> : null}>
                        Needs setup
                    </Tabs.Tab>
                    <Tabs.Tab value="fill">Auto-fill</Tabs.Tab>
                </Tabs.List>
                <Tabs.Panel value="pending" pt="xs"><PendingPartsItem context={context} /></Tabs.Panel>
                <Tabs.Panel value="setup" pt="xs"><NeedsSetup context={context} onCount={setProblems} /></Tabs.Panel>
                <Tabs.Panel value="fill" pt="xs"><AutoFill context={context} /></Tabs.Panel>
            </Tabs>
        </Stack>
    );
}

export function RenderOMGPartsDashboardItem(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return <OMGPartsItem context={context} />;
}
