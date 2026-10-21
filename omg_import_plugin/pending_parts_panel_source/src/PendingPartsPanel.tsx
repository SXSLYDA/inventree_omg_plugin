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
            const response = await context.api.get(URL);
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
            const response = await context.api.post(URL, { ids: [...picked] });
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
    const partLink = (p: PartRow) => <Anchor size="sm" href={`/web/part/${p.pk}/`} target="_blank">{p.name}</Anchor>;

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
                        {state.in_category.map((p, n) => <span key={p.pk}>{n > 0 && ', '}{partLink(p)}</span>)}
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
            const response = await context.api.get(CHECK_URL, { params: refresh ? { refresh: 1 } : {} });
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
    const partLink = (i: CheckIssue) => <Anchor size="sm" href={`/web/part/${i.part}/`} target="_blank">{i.name}</Anchor>;

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
                                            <Table.Td w="28%">{partLink(i)}</Table.Td>
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
                </Tabs.List>
                <Tabs.Panel value="pending" pt="xs"><PendingPartsItem context={context} /></Tabs.Panel>
                <Tabs.Panel value="setup" pt="xs"><NeedsSetup context={context} onCount={setProblems} /></Tabs.Panel>
            </Tabs>
        </Stack>
    );
}

export function RenderOMGPartsDashboardItem(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return <OMGPartsItem context={context} />;
}
