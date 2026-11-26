import { useCallback, useEffect, useMemo, useState } from 'react';
import {
    ActionIcon, Alert, Anchor, Autocomplete, Badge, Box, Button, Card, Checkbox, Collapse, Divider, Group, Image, Loader,
    MultiSelect, NumberInput, Paper, Popover, SegmentedControl, Select, SimpleGrid, Stack, Tabs, Text, TextInput,
    ThemeIcon, Title, Tooltip, UnstyledButton,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
    IconAlertTriangle, IconBox, IconCheck, IconCloudOff, IconInfoCircle, IconPlug, IconPlus, IconRefresh, IconTrash,
} from '@tabler/icons-react';

import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';

// One panel on a part's page - "OMG Setup" (backend: part_setup.py, accessory_setup.py), in tabs:
//   Details      the Component Type and every parameter OMG needs for it
//   Cavities     (connectors) the Cavity Map as ranges + the related contacts / seals / blanks
//   Accessories  on a connector: which locks / boots / caps fit and which are required;
//                on an accessory: which connectors it fits
// What each type needs, template names, AWG sizes and colours come from OMG. A link can open a tab:
// /part/<pk>/omg-part-setup?tab=cavities (or accessories).

const PLUGIN = '/plugin/omg-harness-import';
// Every call reaches OMG (part profiles, rules) - often longer than the 5s default of InvenTree's API client.
const LONG = { timeout: 60000 };
const TITLE = 'OMG Setup';

// ==================== Wire sizes: mm² and AWG ====================
// A contact's / terminal's Min / Max Gauge can be written either way: "0.5" (a bare number is mm²) or
// "20 AWG". OMG compares them in mm² - these show the same size in the other unit, from OMG's table.

const GAUGE_RANGE_ROLES = ['contact.min_gauge', 'contact.max_gauge'];

function gaugeBoth(raw: string | undefined | null, table: Record<string, string | number>): string {
    const text = String(raw ?? '').trim();
    if (!text) return '';
    const isAwg = /awg|#|\/0/i.test(text);
    const number = parseFloat(text.replace(',', '.').replace(/^[^\d]*/, ''));
    if (Number.isNaN(number)) return '';
    if (isAwg) {
        const awg = text.match(/(\d\/0|\d+)/)?.[1] || '';
        const mm2 = table[awg];
        return mm2 !== undefined ? `${awg} AWG = ${Number(mm2)} mm²` : `${awg} AWG`;
    }
    let best = '', diff = Infinity;          // nearest AWG size in the table
    for (const [awg, mm2] of Object.entries(table)) {
        const d = Math.abs(Number(mm2) - number);
        if (d < diff) { diff = d; best = awg; }
    }
    return best ? `${number} mm² ≈ ${best} AWG` : `${number} mm²`;
}

function gaugeRange(min: string | undefined, max: string | undefined, table: Record<string, string | number>): string {
    const lo = gaugeBoth(min, table), hi = gaugeBoth(max, table);
    if (!lo && !hi) return '';
    return lo === hi ? lo : `${lo || '?'} to ${hi || '?'}`;
}

// ==================== Small shared pieces ====================

function apiError(error: any, fallback: string): string {
    const data = error?.response?.data;
    if (data?.detail) return String(data.detail);
    if (data?.errors && Array.isArray(data.errors)) return data.errors.join(' ');
    return error?.message || fallback;
}


// An ⓘ with the longer explanation - instead of a sentence under every field
function Help({ text }: { text: string }) {
    if (!text) return null;
    return (
        <Tooltip label={text} multiline w={300} withArrow>
            <IconInfoCircle size={14} style={{ color: 'var(--mantine-color-gray-5)', verticalAlign: -2, marginLeft: 4 }} />
        </Tooltip>
    );
}

function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
    return (
        <Group justify="space-between" gap="xs">
            <Text size="xs" fw={700} c="dimmed" tt="uppercase">{children}</Text>
            {right}
        </Group>
    );
}

// Unsaved changes on a tab: shown only while there are some
function SaveBar({ count, saving, disabled, onSave, onDiscard, label = 'Save' }: {
    count: number; saving: boolean; disabled?: boolean; onSave: () => void; onDiscard: () => void; label?: string;
}) {
    if (!count) return null;
    return (
        <Paper withBorder shadow="sm" p="xs" px="md" radius="md"
            style={{ position: 'sticky', bottom: 8, zIndex: 5, background: 'var(--mantine-color-indigo-0)' }}>
            <Group justify="space-between">
                <Text size="sm" fw={500}>{count} unsaved change{count === 1 ? '' : 's'}</Text>
                <Group gap="xs">
                    <Button size="xs" variant="default" onClick={onDiscard} disabled={saving}>Discard</Button>
                    <Button size="xs" onClick={onSave} loading={saving} disabled={disabled}>{label}</Button>
                </Group>
            </Group>
        </Paper>
    );
}

interface TablePart { pk: number; name: string; ipn?: string; description?: string; thumbnail?: string; }

// A part number that opens the part inside InvenTree (its router - no page reload); ctrl-click: a new tab
function PartLink({ context, part, query = '' }: { context: InvenTreePluginContext; part: TablePart; query?: string }) {
    const path = `/part/${part.pk}/${query}`;
    return (
        <Anchor size="sm" fw={600} href={`/web${path}`} onClick={(e: React.MouseEvent) => {
            e.stopPropagation();
            if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            context.navigate(path);
        }}>{part.name}</Anchor>
    );
}

// A part to pick, as a tile: picture, part number, description, a tick when picked
function PartTile({ context, part, picked, disabled, onToggle, children }: {
    context: InvenTreePluginContext; part: TablePart; picked: boolean; disabled?: boolean; onToggle: () => void;
    children?: React.ReactNode;
}) {
    return (
        <UnstyledButton onClick={() => !disabled && onToggle()} style={{ cursor: disabled ? 'default' : 'pointer' }}
            aria-pressed={picked}>
            <Card withBorder padding="xs" radius="md" h="100%" style={{
                borderColor: picked ? 'var(--mantine-color-indigo-5)' : undefined,
                background: picked ? 'var(--mantine-color-indigo-0)' : undefined }}>
                <Group gap="xs" wrap="nowrap" align="flex-start">
                    {part.thumbnail
                        ? <Image src={part.thumbnail} w={40} h={40} fit="contain" radius="sm" />
                        : <Box w={40} h={40} style={{ flex: '0 0 40px', borderRadius: 6, background: 'var(--mantine-color-gray-1)' }} />}
                    <Stack gap={0} style={{ minWidth: 0, flex: 1 }}>
                        <PartLink context={context} part={part} />
                        {part.description ? <Text size="xs" c="dimmed" lineClamp={2}>{part.description}</Text> : null}
                        {children}
                    </Stack>
                    {picked
                        ? <IconCheck size={18} color="var(--mantine-color-indigo-6)" style={{ flex: '0 0 18px' }} />
                        : <Box w={18} />}
                </Group>
            </Card>
        </UnstyledButton>
    );
}

// A list of tiles with a search box once there are enough to need one
function PartTiles<T extends TablePart>({ context, parts, picked, onToggle, disabled, extra }: {
    context: InvenTreePluginContext; parts: T[]; picked: Set<number>; onToggle: (pk: number, on: boolean) => void;
    disabled?: boolean; extra?: (p: T) => React.ReactNode;
}) {
    const [term, setTerm] = useState('');
    const words = term.toLowerCase().split(/\s+/).filter(Boolean);
    const shown = parts.filter((p) => words.every((w) => `${p.name} ${p.ipn || ''} ${p.description || ''}`.toLowerCase().includes(w)));
    return (
        <Stack gap={6}>
            {parts.length > 6 && <TextInput size="xs" w={300} placeholder="Search part number or description" value={term}
                onChange={(e) => setTerm(e.currentTarget.value)} />}
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="xs">
                {shown.map((p) => (
                    <PartTile key={p.pk} context={context} part={p} picked={picked.has(p.pk)} disabled={disabled}
                        onToggle={() => onToggle(p.pk, !picked.has(p.pk))}>{extra?.(p)}</PartTile>
                ))}
            </SimpleGrid>
            {!shown.length && <Text size="sm" c="dimmed">{parts.length ? `Nothing matches "${term}".` : 'No parts.'}</Text>}
        </Stack>
    );
}

// ==================== The header's status: OMG's data check, and "not sent to OMG" ====================

interface PartIssue { part: number; message: string; used_by: { harness: string; what: string }[]; used_count: number; }
interface PartCheckState {
    report: { checks: { key: string; title: string; issues: PartIssue[] }[] } | null;
    checked_at: string | null;
    running: boolean;
    error: string;
}

// OMG's Part Data Check for this part - a badge, its problems in a popover
function DataCheckBadge({ context, partId }: { context: InvenTreePluginContext; partId: number }) {
    const [stored, setStored] = useState<PartCheckState | null>(null);
    const load = useCallback(async (refresh = false) => {
        try {
            const response = await context.api.get(`${PLUGIN}/data-check/`,
                { params: { part: partId, ...(refresh ? { refresh: 1 } : {}) }, ...LONG });
            setStored(response.data);
        } catch {
            setStored(null);
        }
    }, [context.api, partId]);
    useEffect(() => { load(); }, [load]);
    useEffect(() => {
        if (!stored?.running) return;
        const timer = setTimeout(() => load(), 3000);
        return () => clearTimeout(timer);
    }, [stored, load]);
    if (!stored) return null;
    const issues = (stored.report?.checks || []).flatMap((c) => c.issues.map((i) => ({ ...i, title: c.title })));
    const when = stored.checked_at ? new Date(stored.checked_at).toLocaleString() : '';
    const used = issues.find((i) => i.used_count);
    const badge = stored.running ? <Badge variant="light" color="gray" leftSection={<Loader size={10} />}>Checking</Badge>
        : !stored.report ? <Badge variant="light" color="gray">Not checked</Badge>
        : issues.length ? <Badge variant="light" color="orange" style={{ cursor: 'pointer' }}>{issues.length} data problem{issues.length === 1 ? '' : 's'}</Badge>
        : <Badge variant="light" color="green" style={{ cursor: 'pointer' }}>Data check OK</Badge>;
    return (
        <Popover width={360} position="bottom-end" withArrow shadow="md">
            <Popover.Target><span>{badge}</span></Popover.Target>
            <Popover.Dropdown>
                <Stack gap={6}>
                    <Text size="sm" fw={600}>OMG data check</Text>
                    {issues.map((i) => <Text key={`${i.title}-${i.message}`} size="xs"><b>{i.title}:</b> {i.message}</Text>)}
                    {!issues.length && <Text size="xs" c="dimmed">{stored.report ? 'Nothing wrong with this part.' : stored.error || "OMG hasn't checked parts yet."}</Text>}
                    {used && <Text size="xs" c="dimmed">Used on {used.used_by.slice(0, 3).map((u) => `${u.harness} (${u.what})`).join(', ')}
                        {used.used_count > 3 ? ` and ${used.used_count - 3} more` : ''}.</Text>}
                    <Group justify="space-between">
                        <Text size="xs" c="dimmed">{when && `Checked ${when}`}</Text>
                        <Button size="compact-xs" variant="light" onClick={() => load(true)} loading={stored.running}>Check again</Button>
                    </Group>
                </Stack>
            </Popover.Dropdown>
        </Popover>
    );
}

// "Changed in InvenTree - OMG not updated" (the plugin's omg_refresh.py), with Retry
function SyncFlag({ context, partId }: { context: InvenTreePluginContext; partId: number }) {
    const [flag, setFlag] = useState<{ error: string; unchecked: boolean; since: string } | null>(null);
    const [busy, setBusy] = useState(false);
    const url = `${PLUGIN}/omg-sync/`;
    const load = useCallback(async () => {
        try {
            const response = await context.api.get(url, { params: { part: partId } });
            setFlag(response.data.pending[0] || null);
        } catch {
            setFlag(null);
        }
    }, [context.api, partId, url]);
    useEffect(() => { load(); }, [load]);
    if (!flag) return null;
    const retry = async () => {
        setBusy(true);
        try {
            const response = await context.api.post(url, { parts: [partId] });
            notifications.show({ color: response.data.waiting ? 'orange' : 'green',
                message: response.data.waiting ? "OMG still can't be reached." : 'Sent to OMG.' });
            await load();
        } finally {
            setBusy(false);
        }
    };
    return (
        <Alert color="red" variant="light" icon={<IconCloudOff size={18} />} py="xs">
            <Group justify="space-between" wrap="nowrap">
                <Text size="sm">
                    <b>OMG hasn't got this part's last change</b> (since {new Date(flag.since).toLocaleString()}): {flag.error}
                    {flag.unchecked ? " Saved without OMG's harness check." : ''}
                </Text>
                <Button size="xs" loading={busy} onClick={retry}>Retry now</Button>
            </Group>
        </Alert>
    );
}

// ==================== Details ====================

interface FieldInfo {
    template: string;
    label: string;
    units: string;
    exists: boolean;
    choices: string[];
    suggestions: string[];   // OMG's selection list, not enforced on the template
    per_item: boolean;       // can hold several values ("DT, DTM")
    value: string | null;
}

interface Profile {
    required: string[];
    optional: string[];
    own_pins?: string[];     // a connector's own wire range and strip length - behind the own-pins switch
}

interface SetupState {
    error: string | null;
    can_edit: boolean;
    part: { pk: number; name: string; ipn: string; description: string };
    component_type: string;
    profile: string | null;
    profiles: Record<string, Profile>;
    fields: Record<string, FieldInfo>;
    missing_required: string[];
    standard_awg: string[];
    awg_to_mm2: Record<string, string | number>;
    colours: string[];
    colour_roles: string[];
    awg_roles: string[];
    accessory_types: string[];
}

// Set on the Cavities / Accessories tabs - shown on Details as summary cards, not fields
const ON_TABS = ['connector.cavity_map', 'connector.contact_count', 'connector.required_accessories',
    'connector.preferred_accessories'];

function DetailsTab({ state, componentType, values, setValues, errors, ownOn, setOwnPins, goTab }: {
    state: SetupState; componentType: string; values: Record<string, string>;
    setValues: (f: (old: Record<string, string>) => Record<string, string>) => void; errors: Record<string, string>;
    ownOn: boolean; setOwnPins: (on: boolean) => void; goTab: (tab: string) => void;
}) {
    const profile = state.profiles[componentType];
    if (!profile) {
        return <Text size="sm" c="dimmed">{componentType
            ? `OMG has no setup for "${componentType}".` : 'Pick what this part is (top) - its fields appear here.'}</Text>;
    }
    const connector = componentType === 'Connector';
    const own = profile.own_pins ?? [];
    const cavityMap = values['connector.cavity_map'] || '';

    const fieldInput = (role: string) => {
        const field = state.fields[role];
        if (!field) return null;
        const value = values[role] ?? '';
        const required = profile.required.includes(role);
        const set = (v: string | null) => setValues((old) => ({ ...old, [role]: v ?? '' }));
        const isRange = GAUGE_RANGE_ROLES.includes(role);
        const shown = isRange && value.trim() ? gaugeBoth(value, state.awg_to_mm2 || {}) || 'not a size OMG can read' : '';
        const common = {
            label: <>{field.template}{field.units ? ` (${field.units})` : ''}<Help text={isRange
                ? `${field.label} - mm² (0.5) or AWG (20 AWG); a plain number is mm²` : field.label} /></>,
            withAsterisk: required,
            description: shown || undefined,
            error: errors[role] || (!field.exists ? `No '${field.template}' template in InvenTree yet` : undefined),
            disabled: !state.can_edit || !field.exists,
        };
        if (state.colour_roles.includes(role)) {
            const data = value && !state.colours.includes(value) ? [value, ...state.colours] : state.colours;
            return <Select key={role} {...common} data={data} value={value || null} onChange={set} searchable clearable />;
        }
        if (state.awg_roles.includes(role)) {
            return <Select key={role} {...common} data={state.standard_awg} value={value || null} onChange={set} searchable clearable />;
        }
        // Several values (an accessory's Connector Series, "DT, DTM") - as many as fit from OMG's list
        const listValues = field.choices.length ? field.choices : (field.suggestions || []);
        if (field.per_item && listValues.length && (state.accessory_types || []).includes(componentType)) {
            const picked = value.split(/[,;]/).map((v) => v.trim()).filter(Boolean);
            const data = [...new Set([...picked.filter((v) => !listValues.includes(v)), ...listValues])];
            return <MultiSelect key={role} {...common} data={data} value={picked} searchable clearable onChange={(v) => set(v.join(', '))} />;
        }
        if (field.choices.length) {
            const data = value && !field.choices.includes(value) ? [value, ...field.choices] : field.choices;
            return <Select key={role} {...common} data={[...new Set(data)]} value={value || null} onChange={set} searchable clearable />;
        }
        if (field.suggestions?.length) {
            return <Autocomplete key={role} {...common} data={[...new Set(field.suggestions)]} value={value} onChange={set}
                placeholder="Pick from the list, or type" />;
        }
        return <TextInput key={role} {...common} value={value} onChange={(e) => set(e.currentTarget.value)} />;
    };

    const required = profile.required.filter((r) => !(connector && ON_TABS.includes(r)));
    const optional = profile.optional.filter((r) => !(connector && ON_TABS.includes(r)));
    const accessories = [values['connector.required_accessories'], values['connector.preferred_accessories']].filter(Boolean);
    return (
        <Stack gap="lg">
            {required.length > 0 && (
                <Stack gap="xs">
                    <SectionTitle>What OMG needs</SectionTitle>
                    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md" verticalSpacing="sm">{required.map(fieldInput)}</SimpleGrid>
                </Stack>
            )}
            {connector && (
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <Card withBorder padding="sm" radius="md">
                        <Group justify="space-between"><Text size="sm" fw={500}>Cavities</Text>
                            <Button size="compact-xs" variant="light" onClick={() => goTab('cavities')}>Edit cavities</Button></Group>
                        <Text size="sm" mt={4} c={cavityMap ? undefined : 'red'}>{cavityMap || 'Not set yet - OMG needs it'}</Text>
                        <Text size="xs" c="dimmed">Contact Count: {values['connector.contact_count'] || '-'} (follows the cavities)</Text>
                    </Card>
                    <Card withBorder padding="sm" radius="md">
                        <Group justify="space-between"><Text size="sm" fw={500}>Accessories</Text>
                            <Button size="compact-xs" variant="light" onClick={() => goTab('accessories')}>Edit accessories</Button></Group>
                        <Text size="sm" mt={4}>{values['connector.required_accessories']
                            ? `Must have: ${values['connector.required_accessories']}` : 'None required'}</Text>
                        <Text size="xs" c="dimmed">{values['connector.preferred_accessories']
                            ? `Use: ${values['connector.preferred_accessories']}` : accessories.length ? '' : 'No preferred parts'}</Text>
                    </Card>
                </SimpleGrid>
            )}
            {optional.length > 0 && (
                <Stack gap="xs">
                    <SectionTitle>Optional</SectionTitle>
                    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md" verticalSpacing="sm">{optional.map(fieldInput)}</SimpleGrid>
                </Stack>
            )}
            {own.length > 0 && (
                <Card withBorder radius="md" padding="sm" bg={ownOn ? 'var(--mantine-color-gray-0)' : undefined}>
                    <Checkbox checked={ownOn} disabled={!state.can_edit} onChange={(e) => setOwnPins(e.currentTarget.checked)}
                        label="Wires go straight onto its own pins"
                        description="Solder cups or captive contacts - no separate contacts. Turning it off clears these when saved." />
                    <Collapse expanded={ownOn}>
                        <SimpleGrid cols={{ base: 1, sm: 3 }} mt="sm">{own.map(fieldInput)}</SimpleGrid>
                        {!/solder/i.test(cavityMap) && (
                            <Text size="xs" c="dimmed" mt="xs">Mark those cavities <b>Solder</b> on the Cavities tab so no
                                separate contacts are picked for them.</Text>
                        )}
                    </Collapse>
                </Card>
            )}
        </Stack>
    );
}

// ==================== Cavities ====================

interface PartInfo {
    pk: number;
    name: string;
    ipn: string;
    description: string;
    component_type: string;
    series: string;
    thumbnail?: string;
    [key: string]: any;
}

interface CavityRow {
    cavities: string;
    series: string;
    sealing: string;
    max_od: string;
    blank: string;          // a chosen blank (part name), any series - '' = the series' own blank
    problems?: string[];
}

interface CavityState {
    error: string | null;
    can_edit: boolean;
    part: { pk: number; name: string; ipn: string };
    map_template: string;
    map_template_exists: boolean;
    raw: string;
    rows: { cavities: string; series: string; solder: boolean; sealing: string; max_od: number | null; blank: string;
        problems: string[] }[];
    all_blanks: PartInfo[];                 // every Blank part, any series (a range's chosen blank)
    related: PartInfo[];
    by_series: Record<string, PartInfo[]>;
    series_keys: Record<string, string>;    // series as written -> OMG's comparison key
    series_choices: string[];               // the Contact Series selection list
    solder_series: string;                  // OMG's series for solder-cup cavities (no contact, seal or blank)
    cavities: string[];                     // every cavity the saved map names
    sealings: string[];
    awg_to_mm2: Record<string, string | number>;
}

const TYPES = ['Contact', 'Seal', 'Blank'];
const SEALING_LABELS: Record<string, string> = { none: 'None', mat: 'Mat', individual: 'Each wire' };
// A range may take contacts of several series ("HDP-16, HDP-20") - kept as that text in the row
const splitSeries = (value: string) => [...new Set((value || '').split(/[,|+]/).map((s) => s.trim()).filter(Boolean))];
const rowsOf = (data: CavityState): CavityRow[] => (data.rows || []).map((r) => ({ cavities: r.cavities, series: r.series,
    sealing: r.sealing, max_od: r.max_od == null ? '' : String(r.max_od), blank: r.blank || '', problems: r.problems }));
const rowKey = (r: CavityRow) => [r.cavities, r.series, r.sealing, r.max_od, r.blank].join('|');

// The cavities as numbered boxes, coloured by range - what the map says at a glance
function CavityStrip({ state }: { state: CavityState }) {
    const palette = ['indigo', 'teal', 'grape', 'orange', 'cyan', 'pink'];
    const saved = state.cavities || [];
    if (!saved.length) return <Text size="sm" c="dimmed">No cavities saved yet - add a range below and save.</Text>;
    const colourOf = (cavity: string) => {
        const i = (state.rows || []).findIndex((r) => r.cavities.split(',').some((part) => {
            const [a, b] = part.trim().split('-');
            if (!b) return a.trim().toUpperCase() === cavity.toUpperCase();
            const n = Number(cavity), lo = Number(a), hi = Number(b);
            if (!Number.isNaN(n) && !Number.isNaN(lo) && !Number.isNaN(hi)) return n >= lo && n <= hi;
            return cavity.toUpperCase() >= a.trim().toUpperCase() && cavity.toUpperCase() <= b.trim().toUpperCase();
        }));
        return palette[Math.max(i, 0) % palette.length];
    };
    return (
        <Group gap={4}>
            {saved.map((cavity) => {
                const c = colourOf(cavity);
                return (
                    <Tooltip key={cavity} label={`Cavity ${cavity}`}>
                        <Box miw={30} h={30} px={4} style={{ borderRadius: 6, background: `var(--mantine-color-${c}-1)`,
                            border: `1px solid var(--mantine-color-${c}-3)`, display: 'flex', alignItems: 'center',
                            justifyContent: 'center', fontSize: 12, fontWeight: 600, color: `var(--mantine-color-${c}-8)` }}>{cavity}</Box>
                    </Tooltip>
                );
            })}
        </Group>
    );
}

function CavitiesTab({ context, partId, onSaved, onDirty }: {
    context: InvenTreePluginContext; partId: number; onSaved: () => void; onDirty: (n: number) => void;
}) {
    const [state, setState] = useState<CavityState | null>(null);
    const [rows, setRows] = useState<CavityRow[]>([]);
    const [related, setRelated] = useState<Set<number>>(new Set());
    const [bySeries, setBySeries] = useState<Record<string, PartInfo[]>>({});
    // OMG decides how series compare ("DT-16" = "dt 16"); a series not looked up yet only matches itself
    const [seriesKeys, setSeriesKeys] = useState<Record<string, string>>({});
    const keyOf = useCallback((series: string) => seriesKeys[(series || '').trim()] ?? `raw:${(series || '').trim()}`,
        [seriesKeys]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const apply = useCallback((data: CavityState) => {
        setState(data);
        setRows(rowsOf(data));
        setRelated(new Set((data.related || []).map((p) => p.pk)));
        setBySeries(data.by_series || {});
        setSeriesKeys(data.series_keys || {});
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            apply((await context.api.get(`${PLUGIN}/cavity-setup/${partId}/`, LONG)).data);
        } catch (error: any) {
            setState(null);
            notifications.show({ color: 'red', title: TITLE, message: apiError(error, "Couldn't load the cavities.") });
        } finally {
            setLoading(false);
        }
    }, [context.api, partId, apply]);
    useEffect(() => { load(); }, [load]);

    const solder = state?.solder_series || '';
    const isSolder = (row: CavityRow) => !!solder && row.series.trim().toLowerCase() === solder.toLowerCase();
    const seriesList = useMemo(() => [...new Set(rows.filter((r) => !isSolder(r)).flatMap((r) => splitSeries(r.series)))],
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [rows, solder]);

    // Series typed or picked but not looked up yet: their parts are fetched straight away
    const unknownSeries = seriesList.filter((s) => !(s.trim() in seriesKeys)).join('|');
    useEffect(() => {
        if (!unknownSeries || !state) return;
        const timer = setTimeout(async () => {
            try {
                const response = await context.api.get(`${PLUGIN}/cavity-setup/${partId}/`,
                    { params: { series: seriesList.join('|') }, ...LONG });
                setBySeries(response.data.by_series || {});
                setSeriesKeys((old) => ({ ...old, ...(response.data.series_keys || {}) }));
            } catch (error: any) {
                notifications.show({ color: 'red', title: TITLE, message: apiError(error, "Couldn't find parts for that series.") });
            }
        }, 500);
        return () => clearTimeout(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [unknownSeries]);

    const relatedParts = useMemo(() => {
        const out: Record<number, PartInfo> = {};
        for (const p of state?.related || []) out[p.pk] = p;
        for (const list of Object.values(bySeries)) for (const p of list) out[p.pk] = p;
        return out;
    }, [state, bySeries]);

    // unsaved changes: rows changed + related parts ticked / unticked
    const changes = useMemo(() => {
        if (!state) return 0;
        const before = rowsOf(state).map(rowKey), now = rows.map(rowKey);
        const rowChanges = Math.max(before.length, now.length) === 0 ? 0
            : now.filter((k, i) => k !== before[i]).length + Math.max(0, before.length - now.length);
        const was = new Set((state.related || []).map((p) => p.pk));
        const relChanges = [...related].filter((pk) => !was.has(pk)).length + [...was].filter((pk) => !related.has(pk)).length;
        return rowChanges + relChanges;
    }, [state, rows, related]);
    useEffect(() => { onDirty(changes); }, [changes, onDirty]);

    const setRow = (i: number, patch: Partial<CavityRow>) => setRows((old) => old.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    const toggle = (pk: number, on: boolean) => setRelated((old) => {
        const next = new Set(old);
        if (on) next.add(pk); else next.delete(pk);
        return next;
    });

    const save = useCallback(async () => {
        if (!state) return;
        setSaving(true);
        const before = new Set((state.related || []).map((p) => p.pk));
        try {
            const response = await context.api.post(`${PLUGIN}/cavity-setup/${partId}/`, {
                rows: rows.map((r) => ({ cavities: r.cavities, series: r.series, sealing: r.sealing, max_od: r.max_od, blank: r.blank })),
                related_add: [...related].filter((pk) => !before.has(pk)),
                related_remove: [...before].filter((pk) => !related.has(pk)),
            }, LONG);
            apply(response.data);
            notifications.show({ color: 'green', title: TITLE, message: 'Cavities saved.' });
            onSaved();
        } catch (error: any) {
            notifications.show({ color: 'red', title: TITLE, message: apiError(error, 'Save failed.') });
        } finally {
            setSaving(false);
        }
    }, [state, related, rows, context.api, partId, apply, onSaved]);

    if (loading) return <Loader size="sm" />;
    if (!state) return <Alert color="red">Couldn't load the cavities.</Alert>;
    if (state.error) return <Alert color="orange">{state.error}</Alert>;

    // the Contact Series list, plus OMG's solder series (once - the list may already hold it)
    const seriesChoices = [...new Set([...(state.series_choices || []), ...(solder ? [solder] : [])])];
    const blankChoices = (current: string) => {
        const choices = [{ value: '', label: 'The series\' own blank' }, ...(state.all_blanks || []).map((b) => ({
            value: b.name, label: `${b.name}${b.series ? ` (${b.series})` : ''}`,
        }))];
        if (current && !choices.some((c) => c.value.toUpperCase() === current.toUpperCase())) {
            choices.push({ value: current, label: `${current} (not a Blank part)` });
        }
        return choices.filter((c, i) => choices.findIndex((x) => x.value === c.value) === i);
    };
    const relatedOfSeries = (names: string[], type: string) => {
        const keys = new Set(names.map((s) => keyOf(s)));
        return Object.values(relatedParts).filter((p) => related.has(p.pk) && keys.has(p.series_key || `raw:${p.series}`)
            && (p.component_type || '').toLowerCase() === type.toLowerCase());
    };
    const preview = rows.filter((r) => r.cavities.trim() && r.series.trim())
        .map((r) => `${r.cavities.trim()}: ${[r.series.trim(), r.sealing || 'none', ...(r.max_od.trim() ? [`maxod ${r.max_od.trim()}`] : []),
            ...(r.blank ? [`blank ${r.blank}`] : [])].join(' / ')}`).join('; ');

    return (
        <Stack gap="lg">
            {!state.map_template_exists && <Alert color="red">No '{state.map_template}' parameter template in InvenTree yet -
                create it (OMG &gt; Component Settings &gt; Part parameters can) before saving.</Alert>}
            <Stack gap={6}>
                <SectionTitle right={<Text size="xs" c="dimmed">{state.cavities?.length || 0} cavities · Contact Count follows</Text>}>
                    Cavities<Help text="Every cavity this connector has, as ranges (1-12, A-D) or single cavities (A, B, C, E), with the contact series it takes and how it's sealed. OMG's pinout, blanks and Contact Count all come from it. A range that takes two contact sizes (HDP-16, HDP-20): the wire's size picks the contact; the first series gives the empty cavities' blank. Solder cups: series Solder - no contact, seal or blank." />
                </SectionTitle>
                <CavityStrip state={state} />
            </Stack>

            <Stack gap="xs">
                {rows.map((row, i) => {
                    const listed = splitSeries(row.series);
                    const contacts = relatedOfSeries(listed, 'Contact');
                    const seals = relatedOfSeries(listed, 'Seal');
                    const blanks = relatedOfSeries(listed.slice(0, 1), 'Blank');
                    const missing = listed.filter((s) => !relatedOfSeries([s], 'Contact').length);
                    const unknown = listed.filter((s) => !seriesChoices.includes(s));
                    const sealings = (isSolder(row) ? state.sealings.filter((v) => v !== 'individual') : state.sealings)
                        .map((v) => ({ value: v, label: SEALING_LABELS[v] || v }));
                    return (
                        <Card key={i} withBorder radius="md" padding="sm">
                            <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm" style={{ alignItems: 'end' }}>
                                <TextInput size="xs" label="Cavities" value={row.cavities} placeholder="1-12, or A, B, C, E"
                                    disabled={!state.can_edit} onChange={(e) => setRow(i, { cavities: e.currentTarget.value })} />
                                {(state.series_choices || []).length
                                    ? <MultiSelect size="xs" label="Takes contacts of series" searchable disabled={!state.can_edit}
                                        data={[...new Set([...unknown, ...seriesChoices])]} value={listed}
                                        onChange={(values) => {
                                            // solder cups can't share a range with contact series
                                            const picked = values.includes(solder) && !listed.includes(solder) ? [solder]
                                                : values.filter((v) => v !== solder || values.length === 1);
                                            setRow(i, { series: picked.join(', '),
                                                ...(picked.includes(solder) && row.sealing === 'individual' ? { sealing: 'none' } : {}),
                                                ...(picked.includes(solder) ? { blank: '' } : {}) });
                                        }}
                                        error={unknown.length ? `Not in the Contact Series list: ${unknown.join(', ')}` : undefined} />
                                    : <TextInput size="xs" label="Takes contacts of series" value={row.series} disabled={!state.can_edit}
                                        placeholder="e.g. DT-16, or HDP-16, HDP-20" onChange={(e) => setRow(i, { series: e.currentTarget.value })} />}
                                <Stack gap={2}>
                                    <Text size="xs" fw={500}>Sealing</Text>
                                    <SegmentedControl size="xs" data={sealings} value={row.sealing || 'none'} disabled={!state.can_edit}
                                        onChange={(v) => setRow(i, { sealing: v })} />
                                </Stack>
                                {isSolder(row)
                                    ? <Text size="xs" c="dimmed">Solder cups - no blank</Text>
                                    : <Select size="xs" label="Empty cavities get" data={blankChoices(row.blank)} value={row.blank}
                                        disabled={!state.can_edit} allowDeselect={false} searchable
                                        onChange={(v) => setRow(i, { blank: v || '' })} />}
                            </SimpleGrid>
                            <Stack gap={4} mt="sm">
                                <Divider label="Optional" labelPosition="left" />
                                <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
                                    <TextInput size="xs" label="Max wire OD (mm)" placeholder="e.g. 3.2" value={row.max_od}
                                        description="Largest insulation OD - blank if unknown"
                                        disabled={!state.can_edit} onChange={(e) => setRow(i, { max_od: e.currentTarget.value })} />
                                </SimpleGrid>
                            </Stack>
                            <Group gap={6} mt="sm">
                                {isSolder(row) ? <Badge color="gray" variant="light">solder cups - no parts</Badge> : <>
                                    <Badge color={contacts.length && !missing.length ? 'green' : 'red'} variant="light"
                                        leftSection={contacts.length && !missing.length ? <IconCheck size={12} /> : <IconAlertTriangle size={12} />}>
                                        {contacts.length} contact{contacts.length === 1 ? '' : 's'}{missing.length ? ` - none for ${missing.join(', ')}` : ''}</Badge>
                                    {row.sealing === 'individual'
                                        ? <Badge color={seals.length ? 'green' : 'red'} variant="light">{seals.length} seal{seals.length === 1 ? '' : 's'}</Badge>
                                        : <Badge color="gray" variant="light">no wire seals</Badge>}
                                    {row.blank
                                        ? <Badge color="green" variant="light" leftSection={<IconCheck size={12} />}>blank {row.blank}</Badge>
                                        : <Badge color={blanks.length === 1 ? 'green' : 'orange'} variant="light">
                                            {blanks.length === 1 ? `blank ${blanks[0].name}` : blanks.length ? `${blanks.length} blanks - pick one` : 'no blank'}</Badge>}
                                </>}
                                {state.can_edit && <Tooltip label="Remove this range"><ActionIcon variant="subtle" color="red" ml="auto"
                                    onClick={() => setRows((old) => old.filter((_r, j) => j !== i))}><IconTrash size={16} /></ActionIcon></Tooltip>}
                            </Group>
                            {(row.problems || []).map((p, j) => <Text key={j} size="xs" c="red" mt={4}>{p}</Text>)}
                        </Card>
                    );
                })}
                {state.can_edit && (
                    <Button size="xs" variant="subtle" leftSection={<IconPlus size={14} />} w="fit-content"
                        onClick={() => setRows((old) => [...old, { cavities: '', series: '', sealing: 'none', max_od: '', blank: '' }])}>
                        {rows.length ? 'Add another range (e.g. A-D: DT-8)' : 'Add the cavities (e.g. 1-12: DT-16)'}</Button>
                )}
                {preview && <Text size="xs" c="dimmed">Cavity Map: {preview}</Text>}
            </Stack>

            {seriesList.map((series) => {
                const parts = (bySeries[keyOf(series)] || []).filter((p) => TYPES.includes(p.component_type));
                return (
                    <Stack key={series} gap={6}>
                        <SectionTitle>{series} parts this connector uses</SectionTitle>
                        {parts.length === 0
                            ? <Text size="sm" c="dimmed">No contacts, seals or blanks with Contact Series "{series}" in InvenTree yet.</Text>
                            : <PartTiles context={context} parts={parts} picked={related} disabled={!state.can_edit} onToggle={toggle}
                                extra={(p) => (
                                    <Group gap={4} mt={2}>
                                        <Badge size="xs" variant="light" color={p.component_type === 'Contact' ? 'blue' : p.component_type === 'Seal' ? 'cyan' : 'gray'}>
                                            {p.component_type}</Badge>
                                        {p['contact.min_gauge'] && <Text size="xs">{gaugeRange(p['contact.min_gauge'], p['contact.max_gauge'], state.awg_to_mm2 || {})}</Text>}
                                        {p['seal.min_od'] && <Text size="xs">OD {p['seal.min_od']}-{p['seal.max_od']} mm</Text>}
                                    </Group>
                                )} />}
                    </Stack>
                );
            })}

            <SaveBar count={changes} saving={saving} disabled={!state.can_edit || !state.map_template_exists}
                onSave={save} onDiscard={() => apply(state)} />
        </Stack>
    );
}

// ==================== Accessories ====================

interface AccessoryPart extends TablePart { type: string; series: string; side: string; ways: string; exit: string; }

interface KindRow {
    kind: string;
    required: boolean;
    quantity: number;
    status: string;
    default: number | null;
    options: AccessoryPart[];
    preferred: number[];
}

interface AccessoryState {
    error: string | null;
    can_edit: boolean;
    part: { pk: number; name: string; ipn: string };
    component_type: string;
    accessory_types: string[];
    diagram_only_types: string[];       // splice plugs - fitted from the wiring diagram
    missing_templates: string[];
    mode: 'connector' | 'accessory';
    connector?: string;
    spec?: { series: string; side: string; ways: string; required: string; preferred: string };
    kinds?: KindRow[];
    problems?: string[];
    fits?: { series: string; side: string; ways: string } | null;
    connectors?: { pk: number; name: string; description: string; connector: string; required: boolean; preferred: boolean; thumbnail?: string }[];
}

interface EditRow { kind: string; required: boolean; quantity: string; preferred: number[]; options: AccessoryPart[]; }

const editRows = (data: AccessoryState): EditRow[] => (data.kinds || []).map((k) => ({
    kind: k.kind, required: k.required, quantity: String(k.quantity || 1), preferred: [...k.preferred], options: k.options }));
const editKey = (r: EditRow) => [r.kind, r.required, r.required ? r.quantity : '', [...r.preferred].sort().join(',')].join('|');

function AccessoriesTab({ context, partId, onSaved, onDirty, goTab }: {
    context: InvenTreePluginContext; partId: number; onSaved: () => void; onDirty: (n: number) => void; goTab: (t: string) => void;
}) {
    const [state, setState] = useState<AccessoryState | null>(null);
    const [rows, setRows] = useState<EditRow[]>([]);
    const [linkRelated, setLinkRelated] = useState(true);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const apply = useCallback((data: AccessoryState) => {
        setState(data);
        setRows(editRows(data));
    }, []);
    const load = useCallback(async () => {
        setLoading(true);
        try {
            apply((await context.api.get(`${PLUGIN}/accessory-setup/${partId}/`, LONG)).data);
        } catch (error: any) {
            setState(null);
            notifications.show({ color: 'red', title: TITLE, message: apiError(error, "Couldn't load the accessories.") });
        } finally {
            setLoading(false);
        }
    }, [context.api, partId, apply]);
    useEffect(() => { load(); }, [load]);

    const changes = useMemo(() => {
        if (!state || state.mode !== 'connector') return 0;
        const before = editRows(state).map(editKey), now = rows.map(editKey);
        return now.filter((k, i) => k !== before[i]).length + Math.max(0, before.length - now.length);
    }, [state, rows]);
    useEffect(() => { onDirty(changes); }, [changes, onDirty]);

    const save = useCallback(async () => {
        setSaving(true);
        try {
            const response = await context.api.post(`${PLUGIN}/accessory-setup/${partId}/`, {
                kinds: rows.map((r) => ({ kind: r.kind, required: r.required, quantity: r.quantity, preferred_pks: r.preferred })),
                link_related: linkRelated,
            }, LONG);
            apply(response.data);
            notifications.show({ color: 'green', title: TITLE, message: 'Accessories saved.' });
            onSaved();
        } catch (error: any) {
            notifications.show({ color: 'red', title: TITLE, message: apiError(error, 'Save failed.') });
        } finally {
            setSaving(false);
        }
    }, [rows, linkRelated, context.api, partId, apply, onSaved]);

    if (loading) return <Loader size="sm" />;
    if (!state) return <Alert color="red">Couldn't load the accessories.</Alert>;
    if (state.error) return <Alert color="orange">{state.error}</Alert>;
    const templatesAlert = state.missing_templates.length > 0 && (
        <Alert color="red">Missing parameter templates in InvenTree: {state.missing_templates.join(', ')} - create them
            (OMG &gt; Component Settings &gt; Part parameters can).</Alert>
    );

    // A lock, boot, cap...: which connectors it fits (read only - it follows its Connector Series, Side and Ways)
    if (state.mode === 'accessory') {
        const list = state.connectors || [];
        return (
            <Stack gap="md">
                {templatesAlert}
                <Text size="sm">Fits connectors with Connector Series <b>{state.fits?.series || '?'}</b>, Side
                    <b> {state.fits?.side || 'Both'}</b> and {state.fits?.ways ? <b>{state.fits.ways} ways</b> : 'any number of ways'}
                    {' '}- <Anchor size="sm" component="button" onClick={() => goTab('details')}>change on Details</Anchor>.</Text>
                {list.length === 0
                    ? <Alert color="yellow">No connector in InvenTree matches yet - check its Connector Series, Side and Ways, and
                        the connectors' own Connector Series / Side / Contact Count.</Alert>
                    : <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="xs">
                        {list.map((c) => (
                            <Card key={c.pk} withBorder padding="xs" radius="md">
                                <Group gap="xs" wrap="nowrap" align="flex-start">
                                    {c.thumbnail ? <Image src={c.thumbnail} w={40} h={40} fit="contain" radius="sm" />
                                        : <Box w={40} h={40} style={{ flex: '0 0 40px', borderRadius: 6, background: 'var(--mantine-color-gray-1)' }} />}
                                    <Stack gap={2} style={{ minWidth: 0 }}>
                                        <PartLink context={context} part={c} query="omg-part-setup?tab=accessories" />
                                        <Text size="xs" c="dimmed" lineClamp={2}>{c.description}</Text>
                                        <Group gap={4}>
                                            <Badge size="xs" variant="light" color={c.required ? 'red' : 'gray'}>{c.required ? 'required' : 'optional'}</Badge>
                                            {c.preferred && <Badge size="xs" variant="light" color="green">the one it uses</Badge>}
                                        </Group>
                                    </Stack>
                                </Group>
                            </Card>
                        ))}
                    </SimpleGrid>}
            </Stack>
        );
    }

    const setRow = (i: number, patch: Partial<EditRow>) => setRows((old) => old.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    // splice plugs, caps...: fitted where the wiring diagram has one, never required by the connector
    const unused = state.accessory_types.filter((t) => !rows.some((r) => r.kind === t) && !(state.diagram_only_types || []).includes(t));
    return (
        <Stack gap="md">
            {templatesAlert}
            <Text size="sm" c="dimmed">Parts that fit: same Connector Series and Side, with this connector's Contact Count in their Ways.
                <Help text="Required kinds go onto every harness using this connector. Where several parts fit, pick the one to use (pick two if both are needed, e.g. signal and power locks), or leave none picked for whoever builds the harness to choose." /></Text>
            {(state.problems || []).map((p, i) => <Alert key={i} color="yellow" py="xs">{p}</Alert>)}
            {rows.length === 0 && <Text size="sm" c="dimmed">No accessory in InvenTree fits this connector yet.</Text>}
            {rows.map((r, i) => (
                <Card key={r.kind} withBorder radius="md" padding="sm">
                    <Group justify="space-between" mb={r.options.length ? 'xs' : 0}>
                        <Group gap="sm">
                            <Text fw={600}>{r.kind}</Text>
                            <SegmentedControl size="xs" disabled={!state.can_edit} value={r.required ? 'req' : 'opt'}
                                onChange={(v) => setRow(i, { required: v === 'req' })}
                                data={[{ value: 'opt', label: 'Optional' }, { value: 'req', label: 'Required' }]} />
                            {r.required && <NumberInput size="xs" w={80} min={1} value={Number(r.quantity) || 1} disabled={!state.can_edit}
                                onChange={(v) => setRow(i, { quantity: String(v || 1) })} aria-label={`${r.kind} quantity`} prefix="× " />}
                        </Group>
                        <Text size="xs" c={r.options.length ? 'dimmed' : 'red'}>
                            {!r.options.length ? 'Nothing fits yet'
                                : r.options.length === 1 ? 'The only one that fits'
                                : r.preferred.length === 0 ? 'None picked - chosen when building the harness'
                                : r.preferred.length > 1 ? `All ${r.preferred.length} picked are needed`
                                : 'This one is used'}</Text>
                    </Group>
                    {r.options.length > 0 && (
                        <PartTiles context={context} parts={r.options} disabled={!state.can_edit || r.options.length === 1}
                            picked={new Set(r.options.length === 1 ? (r.required ? [r.options[0].pk] : []) : r.preferred)}
                            onToggle={(pk, on) => setRow(i, { preferred: on ? [...r.preferred, pk] : r.preferred.filter((x) => x !== pk) })}
                            extra={(p) => p.exit ? <Text size="xs">{p.exit} exit</Text> : null} />
                    )}
                </Card>
            ))}
            {unused.length > 0 && state.can_edit && (
                <Select size="xs" w={260} placeholder="Require another kind..." data={unused} value={null} leftSection={<IconPlus size={14} />}
                    onChange={(v) => v && setRows((old) => [...old, { kind: v, required: true, quantity: '1', preferred: [], options: [] }])} />
            )}
            <SaveBar count={changes} saving={saving} disabled={!state.can_edit} onSave={save} onDiscard={() => apply(state)} />
            {changes > 0 && <Checkbox size="xs" checked={linkRelated} onChange={(e) => setLinkRelated(e.currentTarget.checked)}
                label="Also link the fitting parts as related parts in InvenTree (for reference)" />}
        </Stack>
    );
}

// ==================== The panel ====================

function typeIcon(type: string) {
    return type === 'Connector' ? <IconPlug size={28} /> : <IconBox size={28} />;
}

function OMGSetupPanel({ context }: { context: InvenTreePluginContext }) {
    const partId = Number(context.id);
    const [state, setState] = useState<SetupState | null>(null);
    const [loading, setLoading] = useState(true);
    const [componentType, setComponentType] = useState<string>('');
    const [values, setValues] = useState<Record<string, string>>({});
    const [errors, setErrors] = useState<Record<string, string>>({});
    const [saving, setSaving] = useState(false);
    // "Wires go straight onto its own pins" (solder cups / captive contacts) - null: from the part's values
    const [ownPins, setOwnPins] = useState<boolean | null>(null);
    const [tab, setTab] = useState<string>(() => new URLSearchParams(window.location.search).get('tab') || 'details');
    const [dirty, setDirty] = useState<Record<string, number>>({});
    const setCavityDirty = useCallback((n: number) => setDirty((d) => (d.cavities === n ? d : { ...d, cavities: n })), []);
    const setAccessoryDirty = useCallback((n: number) => setDirty((d) => (d.accessories === n ? d : { ...d, accessories: n })), []);

    const apply = useCallback((data: SetupState) => {
        setState(data);
        setOwnPins(null);
        const profileKey = Object.keys(data.profiles || {}).find((k) => k.toLowerCase() === (data.component_type || '').toLowerCase()) || '';
        setComponentType(profileKey || data.component_type || '');
        const v: Record<string, string> = {};
        for (const [role, field] of Object.entries(data.fields || {})) v[role] = field.value ?? '';
        setValues(v);
        setErrors({});
    }, []);

    const load = useCallback(async (refresh = false) => {
        try {
            apply((await context.api.get(`${PLUGIN}/part-setup/${partId}/`, { params: refresh ? { refresh: '1' } : {}, ...LONG })).data);
        } catch (error: any) {
            setState(null);
            notifications.show({ color: 'red', title: TITLE, message: apiError(error, "Couldn't load.") });
        } finally {
            setLoading(false);
        }
    }, [context.api, partId, apply]);
    useEffect(() => { load(); }, [load]);

    const profile: Profile | null = state && componentType ? state.profiles?.[componentType] || null : null;
    const own = profile?.own_pins ?? [];
    const ownOn = ownPins ?? (own.some((r) => !!values[r]) || /solder/i.test(values['connector.cavity_map'] || ''));

    // what's changed on Details (type, fields, the own-pins switch clearing values)
    const detailChanges = useMemo(() => {
        if (!state || !state.fields) return 0;     // OMG couldn't be read: only {error} - shown below
        let n = componentType && componentType !== (state.profile || state.component_type) ? 1 : 0;
        for (const [role, field] of Object.entries(state.fields)) {
            const now = own.includes(role) && !ownOn ? '' : values[role] ?? '';
            if (now !== (field.value ?? '')) n += 1;
        }
        return n;
    }, [state, componentType, values, own, ownOn]);

    const save = useCallback(async () => {
        if (!state) return;
        setSaving(true);
        const payload: Record<string, string> = {};
        if (profile) {
            for (const role of [...profile.required, ...profile.optional]) payload[role] = values[role] ?? '';
            for (const role of profile.own_pins ?? []) payload[role] = ownOn ? (values[role] ?? '') : '';   // off: cleared
        }
        try {
            apply((await context.api.post(`${PLUGIN}/part-setup/${partId}/`, { component_type: componentType, values: payload }, LONG)).data);
            notifications.show({ color: 'green', title: TITLE, message: 'Saved.' });
        } catch (error: any) {
            setErrors(error?.response?.data?.errors || {});
            notifications.show({ color: 'red', title: TITLE, message: apiError(error, 'Save failed.') });
        } finally {
            setSaving(false);
        }
    }, [state, profile, values, componentType, context.api, partId, apply, ownOn]);

    const goTab = useCallback((next: string) => setTab(next), []);
    const reloadQuiet = useCallback(() => { load(); }, [load]);

    if (loading) return <Loader size="sm" />;
    if (!state) return <Alert color="red">Couldn't load the OMG setup.</Alert>;
    if (state.error) return <Alert color="orange" title={TITLE}>{state.error}</Alert>;

    // the Cavities / Accessories tabs follow the SAVED type (their data is read for it)
    const savedType = state.profile || '';
    const hasCavities = savedType === 'Connector';
    const hasAccessories = hasCavities || (state.accessory_types || []).includes(savedType);
    const shownTab = (tab === 'cavities' && !hasCavities) || (tab === 'accessories' && !hasAccessories) ? 'details' : tab;
    const missing = state.missing_required;
    const typeChanged = !!componentType && componentType !== savedType;

    return (
        <Stack gap="md">
            <Group justify="space-between" align="flex-start" wrap="nowrap">
                <Group gap="md" wrap="nowrap" align="flex-start">
                    <ThemeIcon size={50} radius="md" variant="light" color={missing.length || !savedType ? 'orange' : 'indigo'}>
                        {typeIcon(componentType)}</ThemeIcon>
                    <Stack gap={4}>
                        <Group gap="xs">
                            <Title order={4}>{TITLE}</Title>
                            <Select size="xs" w={170} data={Object.keys(state.profiles)} value={componentType || null}
                                placeholder="What is this part?" searchable allowDeselect={false}
                                disabled={!state.can_edit || !state.fields['part.component_type']?.exists}
                                onChange={(v) => setComponentType(v ?? '')}
                                error={errors['part.component_type'] || (!state.fields['part.component_type']?.exists
                                    ? `No '${state.fields['part.component_type']?.template}' template in InvenTree yet` : undefined)} />
                        </Group>
                        {!savedType
                            ? <Text size="sm" c="orange.8">{state.component_type
                                ? `Component Type "${state.component_type}" isn't one OMG knows - pick what it is.`
                                : 'Pick what this part is, then fill in what OMG needs.'}</Text>
                            : missing.length
                                ? <Group gap={6}><IconAlertTriangle size={15} color="var(--mantine-color-orange-6)" />
                                    <Text size="sm">OMG still needs:</Text>
                                    {missing.map((role) => (
                                        <Badge key={role} color="orange" variant="light" style={{ cursor: 'pointer' }}
                                            onClick={() => setTab(ON_TABS.slice(0, 2).includes(role) ? 'cavities'
                                                : ON_TABS.includes(role) ? 'accessories' : 'details')}>
                                            {state.fields[role]?.template || role}</Badge>
                                    ))}</Group>
                                : <Group gap={6}><IconCheck size={15} color="var(--mantine-color-green-6)" />
                                    <Text size="sm" c="green.8">Everything OMG needs is set</Text></Group>}
                        {typeChanged && savedType && <Text size="xs" c="orange.8">Saving changes it from {savedType} to {componentType}.</Text>}
                    </Stack>
                </Group>
                <Group gap="xs" wrap="nowrap">
                    <DataCheckBadge context={context} partId={partId} />
                    <Tooltip label="Read the part and OMG's rules again">
                        <ActionIcon variant="subtle" color="gray" onClick={() => load(true)} aria-label="Reload from OMG"><IconRefresh size={18} /></ActionIcon>
                    </Tooltip>
                </Group>
            </Group>
            <SyncFlag context={context} partId={partId} />

            <Tabs value={shownTab} onChange={(v) => v && setTab(v)} keepMounted>
                <Tabs.List>
                    <Tabs.Tab value="details" rightSection={detailChanges ? <Badge size="xs" circle color="indigo">{detailChanges}</Badge>
                        : missing.filter((r) => !ON_TABS.includes(r)).length ? <Badge size="xs" circle color="orange">
                            {missing.filter((r) => !ON_TABS.includes(r)).length}</Badge> : null}>Details</Tabs.Tab>
                    {hasCavities && <Tabs.Tab value="cavities" rightSection={dirty.cavities ? <Badge size="xs" circle color="indigo">{dirty.cavities}</Badge>
                        : missing.includes('connector.cavity_map') ? <Badge size="xs" circle color="orange">!</Badge> : null}>Cavities</Tabs.Tab>}
                    {hasAccessories && <Tabs.Tab value="accessories" rightSection={dirty.accessories
                        ? <Badge size="xs" circle color="indigo">{dirty.accessories}</Badge> : null}>
                        {savedType === 'Connector' ? 'Accessories' : 'Fits'}</Tabs.Tab>}
                </Tabs.List>

                <Tabs.Panel value="details" pt="md">
                    <Stack gap="md">
                        <DetailsTab state={state} componentType={componentType} values={values} setValues={setValues} errors={errors}
                            ownOn={ownOn} setOwnPins={setOwnPins} goTab={goTab} />
                        <SaveBar count={detailChanges} saving={saving} disabled={!state.can_edit || !componentType}
                            onSave={save} onDiscard={() => apply(state)} />
                    </Stack>
                </Tabs.Panel>
                {hasCavities && (
                    <Tabs.Panel value="cavities" pt="md">
                        <CavitiesTab context={context} partId={partId} onSaved={reloadQuiet} onDirty={setCavityDirty} />
                    </Tabs.Panel>
                )}
                {hasAccessories && (
                    <Tabs.Panel value="accessories" pt="md">
                        <AccessoriesTab context={context} partId={partId} onSaved={reloadQuiet} onDirty={setAccessoryDirty} goTab={goTab} />
                    </Tabs.Panel>
                )}
            </Tabs>
        </Stack>
    );
}

export function RenderOMGPartSetupPanel(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return <OMGSetupPanel context={context} />;
}
